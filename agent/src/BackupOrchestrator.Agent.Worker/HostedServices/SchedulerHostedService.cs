using System.Collections.Concurrent;
using System.Text.Json;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Core.Transfer;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.HostedServices;

/// <summary>
/// Owns the cron scheduling loop and the end-to-end run pipeline: create
/// job run -> patch RUNNING -> fetch connection config -> transfer -> create
/// backup record -> complete run. Overlap policy is skip-and-log (DECISIONS
/// #1), enforced by JobScheduler.Tick -- see its doc comment.
///
/// Two distinct CancellationToken scopes are in play per job run:
///   - shutdownToken (this service's stoppingToken): governs bookkeeping
///     HTTP calls (create run, RUNNING patch, connection-config fetch,
///     progress patches). If the app is shutting down, these calls are
///     allowed to cancel promptly rather than hang the shutdown.
///   - watchdogToken (shutdownToken linked + CancelAfter(effective timeout)):
///     governs ONLY the transfer call itself. WinScpTransferClient reacts to
///     this by aborting the WinSCP session and returning a TIMEOUT
///     TransferResult rather than throwing -- see its doc comment.
/// "Must survive" completion/backup-record calls are made with
/// CancellationToken.None so a shutdown race doesn't drop the one message
/// that proves a backup happened; they fall back to the offline queue like
/// any other backend-unavailable call, they just aren't allowed to be
/// pre-empted by the shutdown token itself.
/// </summary>
public sealed class SchedulerHostedService : BackgroundService
{
    /// <summary>
    /// How often the scheduler re-evaluates which jobs are due. Not exposed
    /// via AgentOptions in the spec -- 15s gives sub-minute cron granularity
    /// without excessive CPU/log churn; deliberately independent of
    /// HeartbeatIntervalSeconds/JobPollIntervalSeconds.
    /// </summary>
    private static readonly TimeSpan TickInterval = TimeSpan.FromSeconds(15);

    private readonly JobScheduler _scheduler;
    private readonly IJobCache _jobCache;
    private readonly IBackendApiClient _backendApiClient;
    private readonly IBackupTransferClient _transferClient;
    private readonly IOfflineEventQueue _offlineQueue;
    private readonly AgentOptions _options;
    private readonly ILogger<SchedulerHostedService> _logger;

    /// <summary>
    /// Job-run ids the backend has told us are already terminal (409 on a
    /// PATCH). Progress patches keep arriving from WinSCP's callback for a
    /// short window after that -- without this, every remaining progress
    /// tick for a finished run makes a wasted round trip that just 409s
    /// again. ConcurrentDictionary because progress patches are fire-and-
    /// forget tasks that can race each other. Entries are removed once
    /// RunJobAsync itself finishes for that run, so this never grows
    /// unbounded across the process lifetime.
    /// </summary>
    private readonly ConcurrentDictionary<int, byte> _terminalRunIds = new();

    /// <summary>
    /// Set true on a 403 (ServerDisabled) connection-config response, false
    /// again once a connection-config fetch succeeds. There is exactly one
    /// server per agent process (AgentOptions.ServerId), so a single flag
    /// suffices -- see ConnectionConfigOutcome.ServerDisabled doc comment
    /// for why this must NOT stop heartbeat/job-poll, only transfer attempts.
    ///
    /// IMPORTANT: this is observability/logging state ONLY -- it must never
    /// gate whether RunJobAsync attempts a connection-config fetch. An
    /// earlier version early-returned out of RunJobAsync whenever this was
    /// true, which made the flag a one-way latch: the only code path that
    /// could clear it (a successful GetConnectionConfigAsync call) became
    /// permanently unreachable once set, so a server re-enabled by an
    /// operator would silently never back up again for the rest of the
    /// process lifetime. RunJobAsync now always calls GetConnectionConfigAsync
    /// on every due job fire regardless of this flag's value.
    /// </summary>
    private volatile bool _serverDisabledForTransfers;

    public SchedulerHostedService(
        JobScheduler scheduler,
        IJobCache jobCache,
        IBackendApiClient backendApiClient,
        IBackupTransferClient transferClient,
        IOfflineEventQueue offlineQueue,
        IOptions<AgentOptions> options,
        ILogger<SchedulerHostedService> logger)
    {
        _scheduler = scheduler;
        _jobCache = jobCache;
        _backendApiClient = backendApiClient;
        _transferClient = transferClient;
        _offlineQueue = offlineQueue;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TickInterval);

        do
        {
            TickOnce(stoppingToken);
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    internal void TickOnce(CancellationToken shutdownToken)
    {
        var jobs = _jobCache.GetAll();
        var result = _scheduler.Tick(jobs);

        foreach (var skipped in result.SkippedOverlapJobs)
        {
            _logger.LogWarning(
                "Skipping fire for backup job {JobId} ({JobName}): previous run is still in progress",
                skipped.Id, skipped.Name);
        }

        foreach (var job in result.DueJobs)
        {
            _scheduler.MarkRunning(job.Id);

            // Deliberately not awaited: due jobs run concurrently so one
            // slow transfer doesn't delay another job's fire. Exceptions are
            // fully handled inside RunJobAsync -- nothing should escape as
            // an unobserved task exception.
            _ = RunJobAsync(job, shutdownToken).ContinueWith(
                _ => _scheduler.MarkFinished(job.Id),
                CancellationToken.None,
                TaskContinuationOptions.ExecuteSynchronously,
                TaskScheduler.Default);
        }
    }

    private async Task RunJobAsync(BackupJobDto job, CancellationToken shutdownToken)
    {
        // NOTE: deliberately no early-return gate on _serverDisabledForTransfers
        // here. GetConnectionConfigAsync below is re-checked on every due job
        // fire (i.e. as often as the job's own cron schedule -- not every
        // scheduler tick), so a stale cached "disabled" flag can never
        // permanently block transfers once an operator re-enables the server;
        // the 403/Success branches in the switch below are what keep the flag
        // current, purely for logging/observability, not as a gate.
        if (_serverDisabledForTransfers)
        {
            _logger.LogDebug(
                "Server {ServerId} was last reported administratively disabled; re-checking connection config " +
                "for backup job {JobId} in case it has since been re-enabled", job.ServerId, job.Id);
        }

        JobRunDto run;
        try
        {
            run = await _backendApiClient.CreateJobRunAsync(
                new JobRunCreateRequest { BackupJobId = job.Id, TriggeredBy = "scheduler" }, shutdownToken);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(ex, "Could not create a job run for backup job {JobId}; skipping this fire", job.Id);
            return;
        }
        catch (OperationCanceledException) when (shutdownToken.IsCancellationRequested)
        {
            return;
        }

        try
        {
            await PatchAsync(
                run.Id,
                new JobRunPatch { Status = JobRunStatus.RUNNING, StartedAt = DateTimeOffset.UtcNow },
                shutdownToken);

            var connectionConfigResult = await _backendApiClient.GetConnectionConfigAsync(job.ServerId, shutdownToken);

            switch (connectionConfigResult.Outcome)
            {
                case ConnectionConfigOutcome.ServerDisabled:
                    _serverDisabledForTransfers = true;
                    _logger.LogInformation(
                        "Server {ServerId} is administratively disabled; stopping transfer attempts until re-enabled " +
                        "(heartbeat/job-poll continue normally)", job.ServerId);
                    await CompleteAsync(run.Id, JobRunStatus.CANCELLED, "Server administratively disabled");
                    return;
                case ConnectionConfigOutcome.ServerNotFound:
                    _logger.LogError(
                        "Server {ServerId} not found while fetching connection config for backup job {JobId}",
                        job.ServerId, job.Id);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "Server not found");
                    return;
                case ConnectionConfigOutcome.Unavailable:
                    _logger.LogWarning(
                        "No usable connection config for server {ServerId} (deleted or no credentials configured); " +
                        "backing off", job.ServerId);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "No connection config available for this server");
                    return;
                case ConnectionConfigOutcome.DecryptionFailed:
                    _logger.LogWarning(
                        "Connection config decryption failed server-side for server {ServerId}; treating as transient, " +
                        "will retry on next scheduled fire", job.ServerId);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "Connection config decryption failed (transient)");
                    return;
                case ConnectionConfigOutcome.Success:
                    _serverDisabledForTransfers = false;
                    break;
            }

            var connectionConfig = connectionConfigResult.Config!;
            var transferStartUtc = DateTimeOffset.UtcNow;
            var remoteDirectory = RemotePathBuilder.BuildRemoteDirectory(job.ServerId, job.Id);
            var remoteFileName = RemotePathBuilder.BuildRemoteFileName(job.SourcePath, transferStartUtc);

            var transferRequest = new TransferRequest
            {
                BackupJobId = job.Id,
                JobRunId = run.Id,
                LocalSourcePath = job.SourcePath,
                RemoteDirectory = remoteDirectory,
                RemoteFileName = remoteFileName,
                ConnectionConfig = connectionConfig,
            };

            var watchdogTimeout = JobScheduler.GetWatchdogTimeout(job, _options.DefaultJobTimeoutMinutes);
            using var watchdogCts = CancellationTokenSource.CreateLinkedTokenSource(shutdownToken);
            watchdogCts.CancelAfter(watchdogTimeout);

            var progress = new Progress<TransferProgress>(p => ReportProgressFireAndForget(run.Id, p, shutdownToken));
            var transferResult = await _transferClient.TransferAsync(transferRequest, progress, watchdogCts.Token);

            if (transferResult.Success)
            {
                await CreateBackupRecordAsync(job.Id, run.Id, transferResult);
            }

            await CompleteAsync(run.Id, transferResult.Status, transferResult.ErrorMessage, transferResult);
        }
        catch (OperationCanceledException) when (shutdownToken.IsCancellationRequested)
        {
            // Application shutting down mid-run. Deliberately do not attempt
            // any further backend calls (they would just cancel too) --
            // this run stays RUNNING on the backend and is picked up by the
            // backend's own missed-run/timeout watchdog logic on restart.
            _logger.LogWarning("Job run {JobRunId} for backup job {JobId} interrupted by application shutdown", run.Id, job.Id);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Unexpected error running backup job {JobId} (run {JobRunId})", job.Id, run.Id);
            await CompleteAsync(run.Id, JobRunStatus.FAILED, ex.Message);
        }
        finally
        {
            // This run is over one way or another -- drop its entry so
            // _terminalRunIds doesn't grow unbounded across the process
            // lifetime. Harmless no-op if PatchAsync never observed a 409
            // for this run.
            _terminalRunIds.TryRemove(run.Id, out _);
        }
    }

    private void ReportProgressFireAndForget(int jobRunId, TransferProgress progress, CancellationToken shutdownToken)
    {
        _ = ReportProgressAsync(jobRunId, progress, shutdownToken);
    }

    private async Task ReportProgressAsync(int jobRunId, TransferProgress progress, CancellationToken shutdownToken)
    {
        try
        {
            var patch = new JobRunPatch
            {
                Percent = progress.PercentComplete,
                CurrentFile = progress.CurrentFileName,
                BytesDone = progress.BytesTransferred,
            };
            await PatchAsync(jobRunId, patch, shutdownToken);
        }
        catch (Exception ex)
        {
            // Defensive only -- PatchAsync already handles
            // BackendUnavailableException/cancellation internally. This is a
            // fire-and-forget callback from IProgress<T>.Report, so an
            // unhandled exception here would become an unobserved task
            // exception; never let that happen.
            _logger.LogError(ex, "Unexpected error reporting transfer progress for job run {JobRunId}", jobRunId);
        }
    }

    private async Task PatchAsync(int jobRunId, JobRunPatch patch, CancellationToken shutdownToken)
    {
        if (_terminalRunIds.ContainsKey(jobRunId))
        {
            _logger.LogDebug(
                "Skipping PATCH for job run {JobRunId}; already known terminal (previous PATCH returned 409)", jobRunId);
            return;
        }

        try
        {
            var outcome = await _backendApiClient.PatchJobRunAsync(jobRunId, patch, shutdownToken);
            if (outcome == JobRunUpdateOutcome.AlreadyTerminal)
            {
                _terminalRunIds.TryAdd(jobRunId, 0);
                _logger.LogInformation(
                    "Job run {JobRunId} is already terminal on the backend; no further patches will be sent", jobRunId);
            }
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogDebug(
                ex, "PATCH for job run {JobRunId} failed (backend unavailable); enqueueing (safe-to-lose event)", jobRunId);
            var payloadJson = JsonSerializer.Serialize(patch, AgentJsonOptions.Default);
            await _offlineQueue.EnqueueAsync(QueuedEventType.JobRunPatch, payloadJson, jobRunId, CancellationToken.None);
        }
        catch (OperationCanceledException) when (shutdownToken.IsCancellationRequested)
        {
            // Shutdown in progress -- a lost intermediate progress patch is
            // explicitly safe to lose per spec, don't fight the shutdown.
        }
    }

    private async Task CreateBackupRecordAsync(int backupJobId, int jobRunId, TransferResult transferResult)
    {
        var request = new BackupRecordCreateRequest
        {
            BackupJobId = backupJobId,
            JobRunId = jobRunId,
            FileName = Path.GetFileName(transferResult.RemotePath ?? string.Empty),
            RemotePath = transferResult.RemotePath ?? string.Empty,
            FileSizeBytes = transferResult.FileSizeBytes ?? 0,
            Checksum = transferResult.Sha256Checksum,
            ChecksumAlgorithm = transferResult.Sha256Checksum is not null ? Sha256Hasher.AlgorithmName : null,
        };

        try
        {
            // CancellationToken.None: this is a "must survive" event (never
            // silently dropped) -- always attempted/queued even if a
            // shutdown races with it.
            await _backendApiClient.CreateBackupRecordAsync(request, CancellationToken.None);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(
                ex, "Failed to report backup record for job run {JobRunId} (backend unavailable); enqueueing (must-survive event)",
                jobRunId);
            var payloadJson = JsonSerializer.Serialize(request, AgentJsonOptions.Default);
            await _offlineQueue.EnqueueAsync(QueuedEventType.BackupRecordUpsert, payloadJson, jobRunId, CancellationToken.None);
        }
    }

    private async Task CompleteAsync(
        int jobRunId, JobRunStatus status, string? errorMessage, TransferResult? transferResult = null)
    {
        var request = new JobRunCompleteRequest
        {
            Status = status,
            ErrorMessage = errorMessage,
            FilePath = transferResult?.RemotePath,
            FileSizeBytes = transferResult?.FileSizeBytes,
        };

        try
        {
            // CancellationToken.None: "must survive" event, same rationale
            // as CreateBackupRecordAsync above.
            await _backendApiClient.CompleteJobRunAsync(jobRunId, request, CancellationToken.None);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(
                ex, "Failed to complete job run {JobRunId} (backend unavailable); enqueueing (must-survive event)", jobRunId);
            var payloadJson = JsonSerializer.Serialize(request, AgentJsonOptions.Default);
            await _offlineQueue.EnqueueAsync(QueuedEventType.JobRunComplete, payloadJson, jobRunId, CancellationToken.None);
        }
    }
}
