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
/// Outcome of a full run through BackupRunPipeline.RunAsync/RunClaimedAsync.
/// Callers (SchedulerHostedService, WatchHostedService) use this to decide
/// whether a WATCH candidate should be re-offered without consuming a retry
/// attempt (Cancelled/Skipped) vs. counted as a genuine failed attempt
/// (Failed).
/// </summary>
public enum BackupRunOutcome
{
    Success,
    Cancelled,
    Failed,
    Skipped,
}

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
/// ledger updates, re-offer-on-failure) around the returned BackupRunOutcome
/// themselves.
///
/// Also used for manual-run pickup (SCHEDULE-mode only -- WATCH-mode manual
/// triggering is backend-forbidden with a 409): SchedulerHostedService's
/// manual-dispatch loop claims an already-created JobRunDto via
/// IBackendApiClient.ClaimJobRunAsync and calls RunClaimedAsync, which skips
/// CreateJobRunAsync entirely since the run already exists.
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
    /// How often the in-flight transfer is polled against
    /// IJobCache.GetById(job.Id)?.CancelRequestedRunId to detect an
    /// operator-requested cancellation. Hardcoded (matching this codebase's
    /// existing style of hardcoded intervals like TickInterval/
    /// MaxCopyWindowWaitChunk), not an AgentOptions field.
    /// </summary>
    private static readonly TimeSpan CancelPollInterval = TimeSpan.FromSeconds(10);

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
    public async Task<BackupRunOutcome> RunAsync(
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

        JobRunDto? run;
        try
        {
            run = await _backendApiClient.CreateJobRunAsync(
                new JobRunCreateRequest { BackupJobId = job.Id, TriggeredBy = triggeredBy }, shutdownToken);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(ex, "Could not create a job run for backup job {JobId}; skipping this fire", job.Id);
            return BackupRunOutcome.Failed;
        }
        catch (OperationCanceledException) when (shutdownToken.IsCancellationRequested)
        {
            return BackupRunOutcome.Failed;
        }

        if (run is null)
        {
            // Backend rejected job-run creation with 409: job disabled, or an
            // active run already exists. Not an error -- skip this fire.
            _logger.LogInformation(
                "Backend rejected job-run creation for backup job {JobId} with 409 (disabled or active run already exists); skipping this fire",
                job.Id);
            return BackupRunOutcome.Skipped;
        }

        return await RunFromExistingRunAsync(job, run, resolveLocalFileAsync, shutdownToken);
    }

    /// <summary>
    /// Entry point for manual-run pickup: the JobRun already exists (created
    /// by the backend when the operator requested a manual fire) and has
    /// just been claimed via POST /api/job-runs/{id}/claim -- skips
    /// CreateJobRunAsync entirely.
    /// </summary>
    public Task<BackupRunOutcome> RunClaimedAsync(
        BackupJobDto job,
        JobRunDto claimedRun,
        Func<CancellationToken, Task<string?>> resolveLocalFileAsync,
        CancellationToken shutdownToken) =>
        RunFromExistingRunAsync(job, claimedRun, resolveLocalFileAsync, shutdownToken);

    private async Task<BackupRunOutcome> RunFromExistingRunAsync(
        BackupJobDto job,
        JobRunDto run,
        Func<CancellationToken, Task<string?>> resolveLocalFileAsync,
        CancellationToken shutdownToken)
    {
        try
        {
            var withinWindow = await WaitForCopyWindowAsync(job, run.Id, shutdownToken);
            if (!withinWindow)
            {
                // WaitForCopyWindowAsync already completed the run as
                // CANCELLED (disabled/removed, or operator-cancel while
                // waiting for the copy window to open).
                return BackupRunOutcome.Cancelled;
            }

            var localFilePath = await resolveLocalFileAsync(shutdownToken);
            if (localFilePath is null)
            {
                await CompleteAsync(run.Id, JobRunStatus.FAILED, "Local file unexpectedly unavailable at dispatch time");
                return BackupRunOutcome.Failed;
            }

            if (string.IsNullOrWhiteSpace(job.RemoteDirectory))
            {
                _logger.LogError(
                    "Backup job {JobId} has an empty or missing RemoteDirectory from the backend; cannot determine transfer destination -- failing this run without attempting a transfer",
                    job.Id);
                await CompleteAsync(run.Id, JobRunStatus.FAILED, "Backend did not supply a remote destination directory for this job");
                return BackupRunOutcome.Failed;
            }

            var runningPatchOutcome = await PatchAsync(
                run.Id,
                new JobRunPatch { Status = JobRunStatus.RUNNING, StartedAt = DateTimeOffset.UtcNow },
                shutdownToken);
            if (runningPatchOutcome == JobRunUpdateOutcome.AlreadyTerminal)
            {
                _logger.LogInformation(
                    "Job run {JobRunId} for backup job {JobId} was already terminal when transitioning to RUNNING; " +
                    "aborting before connection-config fetch or transfer", run.Id, job.Id);
                return BackupRunOutcome.Cancelled;
            }

            var connectionConfigResult = await _backendApiClient.GetConnectionConfigAsync(job.ServerId, shutdownToken);

            switch (connectionConfigResult.Outcome)
            {
                case ConnectionConfigOutcome.ServerDisabled:
                    _serverDisabledForTransfers = true;
                    _logger.LogInformation(
                        "Server {ServerId} is administratively disabled; stopping transfer attempts until re-enabled " +
                        "(heartbeat/job-poll continue normally)", job.ServerId);
                    await CompleteAsync(run.Id, JobRunStatus.CANCELLED, "Server administratively disabled");
                    return BackupRunOutcome.Cancelled;
                case ConnectionConfigOutcome.ServerNotFound:
                    _logger.LogError(
                        "Server {ServerId} not found while fetching connection config for backup job {JobId}",
                        job.ServerId, job.Id);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "Server not found");
                    return BackupRunOutcome.Failed;
                case ConnectionConfigOutcome.Unavailable:
                    _logger.LogWarning(
                        "No usable connection config for server {ServerId} (deleted or no credentials configured); " +
                        "backing off", job.ServerId);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "No connection config available for this server");
                    return BackupRunOutcome.Failed;
                case ConnectionConfigOutcome.DecryptionFailed:
                    _logger.LogWarning(
                        "Connection config decryption failed server-side for server {ServerId}; treating as transient, " +
                        "will retry on next scheduled fire", job.ServerId);
                    await CompleteAsync(run.Id, JobRunStatus.FAILED, "Connection config decryption failed (transient)");
                    return BackupRunOutcome.Failed;
                case ConnectionConfigOutcome.Success:
                    _serverDisabledForTransfers = false;
                    break;
            }

            var connectionConfig = connectionConfigResult.Config!;
            var remoteDirectory = RemotePathBuilder.NormalizeRemoteDirectory(job.RemoteDirectory);
            var remoteFileName = RemotePathBuilder.BuildRemoteFileName(localFilePath);

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

            // Dedicated source for operator-cancel, distinct from the
            // watchdog/shutdown source above -- only this method, holding
            // all three underlying sources, can tell them apart once
            // TransferAsync comes back reporting "cancelled" (WinSCP itself
            // only sees "the token was cancelled", not why).
            using var operatorCancelCts = new CancellationTokenSource();
            using var combinedCts = CancellationTokenSource.CreateLinkedTokenSource(watchdogCts.Token, operatorCancelCts.Token);
            using var pollLifetimeCts = new CancellationTokenSource();

            var pollTask = PollForOperatorCancelAsync(job.Id, run.Id, operatorCancelCts, pollLifetimeCts.Token);

            var progress = new Progress<TransferProgress>(p => ReportProgressFireAndForget(run.Id, p, shutdownToken));
            TransferResult transferResult;
            try
            {
                transferResult = await _transferClient.TransferAsync(transferRequest, progress, combinedCts.Token);
            }
            finally
            {
                // Stop the poll loop and make sure it's fully awaited before
                // moving on -- never leak the polling task.
                pollLifetimeCts.Cancel();
                await pollTask;
            }

            if (transferResult.Status == JobRunStatus.TIMEOUT && shutdownToken.IsCancellationRequested)
            {
                // WinScpTransferClient cannot distinguish a shutdown-triggered
                // cancellation from a genuine watchdog timeout or operator cancel --
                // it always reports TIMEOUT for any OperationCanceledException. Only
                // this caller, holding shutdownToken directly, can tell. Checked
                // BEFORE the operator-cancel reclassification below and gated on
                // Status == TIMEOUT (not on shutdownToken alone) so a transfer that
                // already completed successfully is never affected. Mirrors the
                // outer catch (OperationCanceledException) when
                // (shutdownToken.IsCancellationRequested) block's policy: no further
                // backend calls -- this run stays RUNNING on the backend, picked up
                // by the backend's own missed-run/timeout watchdog logic on restart.
                _logger.LogWarning(
                    "Job run {JobRunId} for backup job {JobId} interrupted by application shutdown mid-transfer",
                    run.Id, job.Id);
                return BackupRunOutcome.Failed;
            }

            if (transferResult.Status == JobRunStatus.TIMEOUT && operatorCancelCts.IsCancellationRequested)
            {
                // WinScpTransferClient cannot itself distinguish a watchdog
                // timeout from an operator cancel -- it only sees "token was
                // cancelled". Only this caller, holding the dedicated
                // operatorCancelCts, can make that distinction.
                _logger.LogInformation(
                    "Transfer for job run {JobRunId} (backup job {JobId}) was cancelled by operator request", run.Id, job.Id);
                transferResult = new TransferResult
                {
                    Success = false,
                    Status = JobRunStatus.CANCELLED,
                    RemotePath = transferResult.RemotePath,
                    FileSizeBytes = transferResult.FileSizeBytes,
                    Sha256Checksum = transferResult.Sha256Checksum,
                    ErrorMessage = "Cancelled by operator",
                };
            }

            if (transferResult.Success)
            {
                await CreateBackupRecordAsync(job.Id, run.Id, transferResult);
            }

            await CompleteAsync(run.Id, transferResult.Status, transferResult.ErrorMessage, transferResult);

            return transferResult.Status switch
            {
                JobRunStatus.SUCCESS => BackupRunOutcome.Success,
                JobRunStatus.CANCELLED => BackupRunOutcome.Cancelled,
                _ => BackupRunOutcome.Failed,
            };
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
            return BackupRunOutcome.Failed;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Unexpected error running backup job {JobId} (run {JobRunId})", job.Id, run.Id);
            await CompleteAsync(run.Id, JobRunStatus.FAILED, ex.Message);
            return BackupRunOutcome.Failed;
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
    /// chunk so an admin edit/disable/delete/cancel mid-wait is picked up
    /// promptly rather than only at the next PeriodicTimer tick. Returns
    /// false (having already completed the run as CANCELLED) if the job
    /// disappears, is disabled, or is cancelled by the operator while
    /// waiting.
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

            if (current.CancelRequestedRunId == jobRunId)
            {
                await CompleteAsync(jobRunId, JobRunStatus.CANCELLED, "Cancelled by operator while waiting for the copy window to open");
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

    /// <summary>
    /// Polls IJobCache.GetById(jobId)?.CancelRequestedRunId against runId on
    /// CancelPollInterval for the duration of the in-flight transfer,
    /// cancelling operatorCancelCts the moment a match is observed. Stopped
    /// via stopToken once the transfer completes for any other reason
    /// (success, failure, or watchdog/shutdown cancellation) -- callers must
    /// always cancel stopToken's source and await this task in a finally
    /// block so it never leaks past the transfer it was polling for.
    /// </summary>
    private async Task PollForOperatorCancelAsync(int jobId, int runId, CancellationTokenSource operatorCancelCts, CancellationToken stopToken)
    {
        try
        {
            while (!stopToken.IsCancellationRequested)
            {
                await Task.Delay(CancelPollInterval, stopToken);

                var current = _jobCache.GetById(jobId);
                if (current?.CancelRequestedRunId == runId)
                {
                    operatorCancelCts.Cancel();
                    return;
                }
            }
        }
        catch (OperationCanceledException) when (stopToken.IsCancellationRequested)
        {
            // Transfer finished before an operator-cancel was observed --
            // normal stop, not an error.
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

            // Fire-and-forget progress callback: the return value must never
            // be used to abort anything from this call site -- only the
            // RUNNING-transition call site above acts on AlreadyTerminal.
            _ = await PatchAsync(jobRunId, patch, shutdownToken);
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

    private async Task<JobRunUpdateOutcome> PatchAsync(int jobRunId, JobRunPatch patch, CancellationToken shutdownToken)
    {
        if (_terminalRunIds.ContainsKey(jobRunId))
        {
            _logger.LogDebug(
                "Skipping PATCH for job run {JobRunId}; already known terminal (previous PATCH returned 409)", jobRunId);
            return JobRunUpdateOutcome.AlreadyTerminal;
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

            return outcome;
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogDebug(
                ex, "PATCH for job run {JobRunId} failed (backend unavailable); enqueueing (safe-to-lose event)", jobRunId);
            var payloadJson = JsonSerializer.Serialize(patch, AgentJsonOptions.Default);
            await _offlineQueue.EnqueueAsync(QueuedEventType.JobRunPatch, payloadJson, jobRunId, CancellationToken.None);

            // An unreachable backend must never itself abort a run -- preserve
            // today's behavior by reporting Success (i.e. "not terminal").
            return JobRunUpdateOutcome.Success;
        }
        catch (OperationCanceledException) when (shutdownToken.IsCancellationRequested)
        {
            // Shutdown in progress -- a lost intermediate progress patch is
            // explicitly safe to lose per spec, don't fight the shutdown.
            // Same non-aborting rationale as the BackendUnavailableException
            // branch above.
            return JobRunUpdateOutcome.Success;
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
