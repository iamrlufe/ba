using System.Collections.Concurrent;
using System.Text.Json;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Core.Transfer;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.Pipeline;

/// <summary>
/// The shared end-to-end run pipeline: create JobRun -> (wait for copy
/// window, PENDING) -> resolve local file -> PATCH RUNNING -> fetch connection
/// config -> transfer -> create BackupRecord -> complete JobRun. Used by BOTH
/// SchedulerHostedService (cron-triggered SCHEDULE jobs, which create the
/// JobRun eagerly and may wait as PENDING inside this pipeline) and
/// WatchHostedService (WATCH-triggered jobs, which only ever call this once
/// dispatch -- including the copy-window wait -- has already been resolved
/// agent-side; the window-check below is then a near-instant no-op for them).
/// No WATCH-specific dependencies -- callers own all WATCH-specific
/// bookkeeping (MarkRunning/MarkFinished, WatchCandidateTracker.MarkTransferred,
/// ledger updates, re-offer-on-failure) around the returned bool themselves.
///
/// Extracted (not just tidied) from SchedulerHostedService.RunJobAsync + its
/// private helpers: AddHostedService&lt;T&gt;() only registers T as
/// IHostedService, not separately resolvable by concrete type, so
/// WatchHostedService needs this logic as an explicitly-registered singleton
/// to share it at all.
/// </summary>
public sealed class BackupRunPipeline
{
    private readonly IBackendApiClient _backendApiClient;
    private readonly IBackupTransferClient _transferClient;
    private readonly IOfflineEventQueue _offlineQueue;
    private readonly IJobCache _jobCache;
    private readonly IClock _clock;
    private readonly AgentOptions _options;
    private readonly ILogger<BackupRunPipeline> _logger;

    private static readonly TimeSpan MaxCopyWindowWaitChunk = TimeSpan.FromMinutes(5);

    /// <summary>
    /// Job-run ids the backend has told us are already terminal (409 on a
    /// PATCH). See SchedulerHostedService's original doc comment for this
    /// field (moved here verbatim) -- ConcurrentDictionary because progress
    /// patches are fire-and-forget tasks that can race each other.
    /// </summary>
    private readonly ConcurrentDictionary<int, byte> _terminalRunIds = new();

    /// <summary>
    /// Set true on a 403 (ServerDisabled) connection-config response, false
    /// again once a connection-config fetch succeeds. There is exactly one
    /// server per agent process (AgentOptions.ServerId), so a single flag
    /// suffices. Observability/logging state ONLY -- must never gate whether
    /// RunAsync attempts a connection-config fetch (see the original
    /// SchedulerHostedService doc comment for the bug this guards against).
    /// </summary>
    private volatile bool _serverDisabledForTransfers;

    public BackupRunPipeline(
        IBackendApiClient backendApiClient,
        IBackupTransferClient transferClient,
        IOfflineEventQueue offlineQueue,
        IJobCache jobCache,
        IClock clock,
        IOptions<AgentOptions> options,
        ILogger<BackupRunPipeline> logger)
    {
        _backendApiClient = backendApiClient;
        _transferClient = transferClient;
        _offlineQueue = offlineQueue;
        _jobCache = jobCache;
        _clock = clock;
        _options = options.Value;
        _logger = logger;
    }

    /// <param name="job">Snapshot of the job at dispatch time.</param>
    /// <param name="triggeredBy">"scheduler" or "watch".</param>
    /// <param name="resolveLocalFileAsync">
    /// Invoked after the copy-window wait, immediately before the RUNNING
    /// transition. SCHEDULE callers pass `_ => Task.FromResult(job.SourcePath)`;
    /// WATCH callers pass an already-resolved value.
    /// </param>
    /// <returns>true iff the run completed with a successful transfer; false for any FAILED/CANCELLED/TIMEOUT outcome.</returns>
    public async Task<bool> RunAsync(
        BackupJobDto job,
        string triggeredBy,
        Func<CancellationToken, Task<string?>> resolveLocalFileAsync,
        CancellationToken shutdownToken)
    {
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
                new JobRunCreateRequest { BackupJobId = job.Id, TriggeredBy = triggeredBy }, shutdownToken);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(ex, "Could not create a job run for backup job {JobId}; skipping this fire", job.Id);
            return false;
        }
        catch (OperationCanceledException) when (shutdownToken.IsCancellationRequested)
        {
            return false;
        }

        try
        {
            var withinWindow = await WaitForCopyWindowAsync(job, run.Id, shutdownToken);
            if (!withinWindow)
            {
                // WaitForCopyWindowAsync already completed the run as CANCELLED.
                return false;
            }

            var localFilePath = await resolveLocalFileAsync(shutdownToken);
            if (localFilePath is null)
            {
                await CompleteAsync(run.Id, JobRunStatus.FAILED, "Local file unexpectedly unavailable at dispatch time");
                return false;
            }

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
                    return false;
                case ConnectionConfigOutcome.ServerNotFound:
                    _logger.LogError(
                        "Server {ServerId} not found while fetching connection config for backup job {JobId}",
                        job.ServerId, job.Id);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "Server not found");
                    return false;
                case ConnectionConfigOutcome.Unavailable:
                    _logger.LogWarning(
                        "No usable connection config for server {ServerId} (deleted or no credentials configured); " +
                        "backing off", job.ServerId);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "No connection config available for this server");
                    return false;
                case ConnectionConfigOutcome.DecryptionFailed:
                    _logger.LogWarning(
                        "Connection config decryption failed server-side for server {ServerId}; treating as transient, " +
                        "will retry on next scheduled fire", job.ServerId);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "Connection config decryption failed (transient)");
                    return false;
                case ConnectionConfigOutcome.Success:
                    _serverDisabledForTransfers = false;
                    break;
            }

            var connectionConfig = connectionConfigResult.Config!;
            var transferStartUtc = DateTimeOffset.UtcNow;
            var remoteDirectory = RemotePathBuilder.BuildRemoteDirectory(job.ServerId, job.Id);
            var remoteFileName = RemotePathBuilder.BuildRemoteFileName(localFilePath, transferStartUtc);

            var transferRequest = new TransferRequest
            {
                BackupJobId = job.Id,
                JobRunId = run.Id,
                LocalSourcePath = localFilePath,
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
            return transferResult.Success;
        }
        catch (OperationCanceledException) when (shutdownToken.IsCancellationRequested)
        {
            // Application shutting down mid-run. Deliberately do not attempt
            // any further backend calls (they would just cancel too) --
            // this run stays RUNNING (or PENDING) on the backend and is
            // picked up by the backend's own missed-run/timeout watchdog
            // logic on restart.
            _logger.LogWarning(
                "Job run {JobRunId} for backup job {JobId} interrupted by application shutdown", run.Id, job.Id);
            return false;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Unexpected error running backup job {JobId} (run {JobRunId})", job.Id, run.Id);
            await CompleteAsync(run.Id, JobRunStatus.FAILED, ex.Message);
            return false;
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

    /// <summary>
    /// Chunked 5-min Task.Delay, re-fetching IJobCache.GetById(job.Id) each
    /// chunk so an admin edit/disable/delete mid-wait is picked up promptly
    /// rather than only at the next PeriodicTimer tick. Returns false (having
    /// already completed the run as CANCELLED) if the job disappears or is
    /// disabled while waiting.
    /// </summary>
    private async Task<bool> WaitForCopyWindowAsync(BackupJobDto job, int jobRunId, CancellationToken shutdownToken)
    {
        while (true)
        {
            var current = _jobCache.GetById(job.Id);
            if (current is null || !current.IsEnabled)
            {
                await CompleteAsync(jobRunId, JobRunStatus.CANCELLED, "Backup job disabled or removed while waiting for the copy window to open");
                return false;
            }

            var now = _clock.UtcNow;
            if (CopyWindowCalculator.IsWithinCopyWindow(current, now))
            {
                return true;
            }

            var target = CopyWindowCalculator.NextWindowOpenUtc(current, now);
            var delay = target - now;
            if (delay > MaxCopyWindowWaitChunk)
            {
                delay = MaxCopyWindowWaitChunk;
            }

            await Task.Delay(delay, shutdownToken);
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
