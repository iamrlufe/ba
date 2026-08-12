using System.Collections.Concurrent;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Worker.Pipeline;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.Watch;

/// <summary>
/// Owns all WATCH-mode orchestration: per-job FileSystemWatcher +
/// reconciliation rescan lifecycle, per-file readiness-detection loops, and
/// the single-candidate-slot dispatch flow. On a ~30s timer (same order of
/// magnitude as JobPollIntervalSeconds -- this codebase is all-polling, no
/// new evented pub-sub introduced for one feature), diffs
/// IJobCache.GetAll().Where(j => j.TriggerMode == "WATCH") against currently-
/// active IWatchDirectoryMonitor instances: creates a monitor + per-job
/// CancellationTokenSource for new/newly-appearing WATCH jobs, disposes+cancels
/// for jobs removed/disabled/switched away from WATCH, disposes+recreates if
/// WatchDirectory itself changed.
///
/// IMPORTANT, confirmed design decision: for WATCH-triggered runs, NO JobRun
/// is created and BackupRunPipeline.RunAsync is NEVER invoked until the copy
/// window is confirmed open (or unrestricted) AND a final candidate is
/// resolved. All waiting/candidate-tracking happens agent-side, in-memory,
/// entirely before that point -- see RunWaitAndDispatchAsync. This is
/// deliberately different from SCHEDULE mode (BackupRunPipeline/
/// SchedulerHostedService, which still creates its JobRun immediately and
/// waits PENDING inside the pipeline) -- it's safe because WATCH jobs are
/// already fully excluded from the backend's check_missed_runs (filtered by
/// trigger_mode == "SCHEDULE"), so there's no missed-run-alert risk from a
/// long agent-side wait with no JobRun yet.
///
/// Reconciliation additionally respawns orphaned NOT_READY readiness loops:
/// IWatchLedger.GetNotReadyEntriesAsync (an addition beyond the original
/// interface shape) lets RunReconciliationAsync detect a ledger row whose
/// in-memory readiness loop died with a prior agent process (crash, deploy,
/// reboot) -- such a row has no entry in this fresh process's
/// state.ActiveReadinessLoopPaths, so its loop is respawned using the row's
/// ORIGINAL FirstSeenAtUtc (not "now"), preserving the correct lock-check
/// timeout countdown across the restart rather than silently resetting it.
/// </summary>
public sealed class WatchHostedService : BackgroundService
{
    private static readonly TimeSpan TickInterval = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan MaxCopyWindowWaitChunk = TimeSpan.FromMinutes(5);

    private readonly IJobCache _jobCache;
    private readonly Func<IWatchDirectoryMonitor> _monitorFactory;
    private readonly ISqlBackupFinishDetector _sqlDetector;
    private readonly IFileLockChecker _lockChecker;
    private readonly IWatchLedger _ledger;
    private readonly WatchCandidateTracker _tracker;
    private readonly JobScheduler _jobScheduler;
    private readonly BackupRunPipeline _pipeline;
    private readonly IBackendApiClient _backendApiClient;
    private readonly IClock _clock;
    private readonly AgentOptions _options;
    private readonly ILogger<WatchHostedService> _logger;

    private readonly Dictionary<int, WatchedJobState> _watchedJobs = new();
    private CancellationToken _stoppingToken;

    public WatchHostedService(
        IJobCache jobCache,
        Func<IWatchDirectoryMonitor> monitorFactory,
        ISqlBackupFinishDetector sqlDetector,
        IFileLockChecker lockChecker,
        IWatchLedger ledger,
        WatchCandidateTracker tracker,
        JobScheduler jobScheduler,
        BackupRunPipeline pipeline,
        IBackendApiClient backendApiClient,
        IClock clock,
        IOptions<AgentOptions> options,
        ILogger<WatchHostedService> logger)
    {
        _jobCache = jobCache;
        _monitorFactory = monitorFactory;
        _sqlDetector = sqlDetector;
        _lockChecker = lockChecker;
        _ledger = ledger;
        _tracker = tracker;
        _jobScheduler = jobScheduler;
        _pipeline = pipeline;
        _backendApiClient = backendApiClient;
        _clock = clock;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _stoppingToken = stoppingToken;
        using var timer = new PeriodicTimer(TickInterval);

        try
        {
            do
            {
                await TickOnceAsync(stoppingToken);
            }
            while (await timer.WaitForNextTickAsync(stoppingToken));
        }
        finally
        {
            foreach (var jobId in _watchedJobs.Keys.ToList())
            {
                RemoveWatchedJob(jobId);
            }
        }
    }

    internal async Task TickOnceAsync(CancellationToken cancellationToken)
    {
        var currentWatchJobs = _jobCache.GetAll()
            .Where(j => j.IsEnabled && j.TriggerMode == "WATCH")
            .ToDictionary(j => j.Id);

        foreach (var jobId in _watchedJobs.Keys.ToList())
        {
            if (!currentWatchJobs.ContainsKey(jobId))
            {
                RemoveWatchedJob(jobId);
            }
        }

        var now = _clock.UtcNow;

        foreach (var job in currentWatchJobs.Values)
        {
            if (job.WatchDirectory is null)
            {
                _logger.LogWarning("WATCH job {JobId} is enabled with TriggerMode=WATCH but has no WatchDirectory configured; skipping", job.Id);
                continue;
            }

            if (_watchedJobs.TryGetValue(job.Id, out var state))
            {
                if (!string.Equals(state.WatchDirectory, job.WatchDirectory, StringComparison.OrdinalIgnoreCase))
                {
                    _logger.LogInformation(
                        "WATCH job {JobId} WatchDirectory changed ({Old} -> {New}); recreating monitor",
                        job.Id, state.WatchDirectory, job.WatchDirectory);
                    RemoveWatchedJob(job.Id);
                    state = CreateWatchedJob(job);
                    await RunReconciliationAsync(state, job, cancellationToken);
                    state.LastReconciliationUtc = now;
                    continue;
                }
            }
            else
            {
                state = CreateWatchedJob(job);
                await RunReconciliationAsync(state, job, cancellationToken);
                state.LastReconciliationUtc = now;
                continue;
            }

            if (now - state.LastReconciliationUtc >= TimeSpan.FromSeconds(_options.WatchReconciliationIntervalSeconds))
            {
                await RunReconciliationAsync(state, job, cancellationToken);
                state.LastReconciliationUtc = now;
            }
        }
    }

    private WatchedJobState CreateWatchedJob(BackupJobDto job)
    {
        var cts = CancellationTokenSource.CreateLinkedTokenSource(_stoppingToken);
        var monitor = _monitorFactory();

        // Job-level applicability gate (DECISIONS -- see class doc comment):
        // evaluated once per job snapshot, never periodically re-checked.
        var useMsdbForJob = job.SqlInstanceId is not null
                          && job.SqlInstanceUseWindowsAuth == true
                          && job.SqlInstanceHost is not null
                          && job.DatabaseName is not null;

        var state = new WatchedJobState
        {
            JobId = job.Id,
            WatchDirectory = job.WatchDirectory!,
            Monitor = monitor,
            Cts = cts,
            UseMsdbForJob = useMsdbForJob,
            LastReconciliationUtc = DateTimeOffset.MinValue,
        };

        monitor.Start(
            job.WatchDirectory!,
            fullPath => OnFileAppeared(state, job, fullPath),
            ex => _logger.LogWarning(ex, "WATCH monitor faulted for job {JobId}; relying on reconciliation rescan as backstop", job.Id));

        _watchedJobs[job.Id] = state;
        _logger.LogInformation(
            "WATCH monitoring started for job {JobId} ({WatchDirectory}); msdb detection {UseMsdb}",
            job.Id, job.WatchDirectory, useMsdbForJob ? "enabled" : "disabled (lock-check only)");

        return state;
    }

    private void RemoveWatchedJob(int jobId)
    {
        if (!_watchedJobs.Remove(jobId, out var state))
        {
            return;
        }

        _logger.LogInformation("WATCH monitoring stopped for job {JobId}", jobId);
        state.Cts.Cancel();
        state.Monitor.Dispose();
        state.Cts.Dispose();
    }

    // ------------------------------------------------------------------
    // Trigger 1: a file just became READY (live watcher event or
    // reconciliation-spawned readiness loop).
    // ------------------------------------------------------------------

    private void OnFileAppeared(WatchedJobState state, BackupJobDto job, string fullPath)
    {
        if (!state.ActiveReadinessLoopPaths.TryAdd(fullPath, 0))
        {
            return; // already tracked (reconciliation raced ahead, or duplicate Created+Renamed events)
        }

        _ = Task.Run(async () =>
        {
            long? fileSizeBytes = TryGetFileSize(fullPath);
            var nowUtc = _clock.UtcNow;

            try
            {
                await _ledger.InsertNotReadyAsync(job.Id, fullPath, fileSizeBytes, nowUtc, state.Cts.Token);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "WATCH could not insert ledger row for newly-appeared file {FilePath} (job {JobId})", fullPath, job.Id);
                state.ActiveReadinessLoopPaths.TryRemove(fullPath, out _);
                return;
            }

            await RunReadinessLoopAsync(state, job, fullPath, nowUtc);
        }, state.Cts.Token);
    }

    // ------------------------------------------------------------------
    // Trigger 2: periodic reconciliation tick -- backstop for "window
    // opened with no new file arriving", and for discovering files that
    // arrived while the agent was down/a FileSystemWatcher event was missed.
    // ------------------------------------------------------------------

    private async Task RunReconciliationAsync(WatchedJobState state, BackupJobDto job, CancellationToken cancellationToken)
    {
        string[] diskFiles;
        try
        {
            diskFiles = Directory.GetFiles(job.WatchDirectory!);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or DirectoryNotFoundException)
        {
            _logger.LogWarning(ex, "WATCH reconciliation could not list {WatchDirectory} for job {JobId}", job.WatchDirectory, job.Id);
            TryStartDispatchCycle(job);
            return;
        }

        IReadOnlyList<string> knownPaths;
        try
        {
            knownPaths = await _ledger.GetKnownFilePathsAsync(job.Id, cancellationToken);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "WATCH reconciliation could not read the ledger for job {JobId}", job.Id);
            TryStartDispatchCycle(job);
            return;
        }

        var knownSet = new HashSet<string>(knownPaths, StringComparer.OrdinalIgnoreCase);

        // Closes the restart-mid-detection gap: a NOT_READY ledger row whose
        // in-memory readiness loop died with a prior agent process (crash,
        // deploy, reboot) is "known" to the ledger but has no entry in this
        // fresh process's state.ActiveReadinessLoopPaths -- respawn its loop,
        // preserving the ORIGINAL FirstSeenAtUtc (not "now") so the lock-check
        // timeout countdown isn't silently reset/extended by the restart.
        try
        {
            var orphanedNotReady = await _ledger.GetNotReadyEntriesAsync(job.Id, cancellationToken);
            foreach (var entry in orphanedNotReady)
            {
                if (!File.Exists(entry.FilePath))
                {
                    // Vanished while the agent was down -- mark it now rather
                    // than waiting for a readiness loop that would immediately
                    // see the same thing on its first tick anyway.
                    await _ledger.MarkVanishedAsync(job.Id, entry.FilePath, cancellationToken);
                    continue;
                }

                if (!state.ActiveReadinessLoopPaths.TryAdd(entry.FilePath, 0))
                {
                    continue; // already has a live loop (normal case, not orphaned)
                }

                _logger.LogInformation(
                    "Respawning orphaned readiness loop for {FilePath} (job {JobId}); originally first seen at {FirstSeenAtUtc}",
                    entry.FilePath, job.Id, entry.FirstSeenAtUtc);
                _ = Task.Run(() => RunReadinessLoopAsync(state, job, entry.FilePath, entry.FirstSeenAtUtc), state.Cts.Token);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "WATCH reconciliation could not check for orphaned NOT_READY rows for job {JobId}", job.Id);
        }

        foreach (var filePath in diskFiles)
        {
            if (knownSet.Contains(filePath))
            {
                // Already tracked in the ledger (any state). Per spec:
                // reconciliation's only job is discovering untracked files --
                // an already-NOT_READY row's own readiness loop continues
                // independently, terminal rows are never re-checked.
                continue;
            }

            if (!state.ActiveReadinessLoopPaths.TryAdd(filePath, 0))
            {
                continue; // a concurrent watcher event already claimed this path this tick
            }

            var fileSizeBytes = TryGetFileSize(filePath);
            var nowUtc = _clock.UtcNow;

            try
            {
                await _ledger.InsertNotReadyAsync(job.Id, filePath, fileSizeBytes, nowUtc, cancellationToken);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "WATCH reconciliation could not insert ledger row for {FilePath} (job {JobId})", filePath, job.Id);
                state.ActiveReadinessLoopPaths.TryRemove(filePath, out _);
                continue;
            }

            _ = Task.Run(() => RunReadinessLoopAsync(state, job, filePath, nowUtc), state.Cts.Token);
        }

        TryStartDispatchCycle(job);
    }

    // ------------------------------------------------------------------
    // Per-file readiness-detection loop (section 5 of the spec).
    // ------------------------------------------------------------------

    private async Task RunReadinessLoopAsync(WatchedJobState state, BackupJobDto job, string filePath, DateTimeOffset firstSeenUtc)
    {
        var ct = state.Cts.Token;
        var interval = TimeSpan.FromSeconds(_options.FileLockCheckIntervalSeconds);
        var timeoutMinutes = job.ExpectedMaxDurationMinutes ?? _options.FileLockCheckTimeoutMinutes;

        try
        {
            while (true)
            {
                if (ct.IsCancellationRequested)
                {
                    return;
                }

                if (!File.Exists(filePath))
                {
                    await SafeLedgerCallAsync(() => _ledger.MarkVanishedAsync(job.Id, filePath, CancellationToken.None), job.Id, filePath, "mark vanished");
                    _logger.LogDebug("WATCH file {FilePath} (job {JobId}) vanished before becoming ready", filePath, job.Id);
                    return;
                }

                DateTimeOffset? orderingTimestamp = null;
                WatchDetectionMethod? detectionMethod = null;

                if (state.UseMsdbForJob)
                {
                    try
                    {
                        var finishUtc = await _sqlDetector.TryGetBackupFinishUtcAsync(
                            job.SqlInstanceHost!, job.SqlInstancePort, job.SqlInstanceInstanceName,
                            job.DatabaseName!, filePath, ct);

                        if (finishUtc is not null)
                        {
                            orderingTimestamp = finishUtc.Value;
                            detectionMethod = WatchDetectionMethod.Msdb;
                        }
                    }
                    catch (SqlDetectorUnavailableException ex)
                    {
                        _logger.LogDebug(ex, "msdb unavailable this cycle for job {JobId} file {FilePath}; falling back to lock-check", job.Id, filePath);
                    }
                }

                if (orderingTimestamp is null)
                {
                    bool unlocked;
                    try
                    {
                        unlocked = _lockChecker.IsUnlocked(filePath);
                    }
                    catch (Exception ex) when (ex is FileNotFoundException or UnauthorizedAccessException)
                    {
                        _logger.LogWarning(ex, "Lock check for {FilePath} (job {JobId}) failed distinctly (not routine 'still locked')", filePath, job.Id);
                        unlocked = false;
                    }

                    if (unlocked)
                    {
                        orderingTimestamp = File.GetLastWriteTimeUtc(filePath);
                        detectionMethod = WatchDetectionMethod.LockCheck;
                    }
                }

                if (orderingTimestamp is not null && detectionMethod is not null)
                {
                    // Clear any active lock-timeout alert now that the file is ready.
                    await ReportWatchEventIfChangedAsync(job.Id, filePath, active: false, detail: "file became ready");

                    var candidate = new WatchCandidateFile
                    {
                        BackupJobId = job.Id,
                        LocalFilePath = filePath,
                        OrderingTimestampUtc = orderingTimestamp.Value,
                        DetectionMethod = detectionMethod.Value,
                        FileSizeBytes = TryGetFileSize(filePath) ?? 0,
                    };

                    await OnFileReadyAsync(job, candidate);
                    return;
                }

                var elapsed = _clock.UtcNow - firstSeenUtc;
                if (elapsed >= TimeSpan.FromMinutes(timeoutMinutes))
                {
                    await ReportWatchEventIfChangedAsync(
                        job.Id, filePath, active: true,
                        detail: $"still locked after {elapsed.TotalMinutes:F0} minutes (timeout {timeoutMinutes}m)");
                }

                try
                {
                    await Task.Delay(interval, ct);
                }
                catch (OperationCanceledException) when (ct.IsCancellationRequested)
                {
                    return;
                }
            }
        }
        finally
        {
            state.ActiveReadinessLoopPaths.TryRemove(filePath, out _);
        }
    }

    private async Task OnFileReadyAsync(BackupJobDto job, WatchCandidateFile candidate)
    {
        var outcome = _tracker.OfferCandidate(candidate, out var supersededOrDiscarded);

        if (outcome != CandidateOfferOutcome.Accepted)
        {
            var affectedPath = supersededOrDiscarded!.LocalFilePath;
            _logger.LogInformation(
                "WATCH candidate {Outcome} for job {JobId}: {FilePath} -- routine, not an anomaly",
                outcome, job.Id, affectedPath);
            await SafeLedgerCallAsync(
                () => _ledger.MarkSupersededAsync(job.Id, affectedPath, CancellationToken.None), job.Id, affectedPath, "mark superseded");
        }
        else
        {
            await SafeLedgerCallAsync(
                () => _ledger.MarkReadyAsync(job.Id, candidate.LocalFilePath, candidate.OrderingTimestampUtc, candidate.DetectionMethod.ToString(), CancellationToken.None),
                job.Id, candidate.LocalFilePath, "mark ready");
        }

        TryStartDispatchCycle(job);
    }

    // ------------------------------------------------------------------
    // Dispatch flow (section 11 of the spec).
    // ------------------------------------------------------------------

    private void TryStartDispatchCycle(BackupJobDto job)
    {
        if (_jobScheduler.IsRunning(job.Id))
        {
            return; // an actual transfer is in flight; its own completion re-checks
        }

        if (!_tracker.TryBeginDispatchCycle(job.Id))
        {
            return; // a wait-then-dispatch loop is already in flight for this job
        }

        _ = RunWaitAndDispatchAsync(job);
    }

    private async Task RunWaitAndDispatchAsync(BackupJobDto job)
    {
        try
        {
            while (true)
            {
                var current = _jobCache.GetById(job.Id);
                if (current is null || !current.IsEnabled || current.TriggerMode != "WATCH")
                {
                    return; // job removed/disabled/switched out from under us mid-wait
                }

                var now = _clock.UtcNow;
                if (CopyWindowCalculator.IsWithinCopyWindow(current, now))
                {
                    break;
                }

                var target = CopyWindowCalculator.NextWindowOpenUtc(current, now);
                var delay = target - now;
                if (delay > MaxCopyWindowWaitChunk)
                {
                    delay = MaxCopyWindowWaitChunk;
                }

                try
                {
                    await Task.Delay(delay, _stoppingToken);
                }
                catch (OperationCanceledException) when (_stoppingToken.IsCancellationRequested)
                {
                    return;
                }
            }

            var claimed = _tracker.ClaimForDispatch(job.Id);
            if (claimed is null)
            {
                return; // nothing left worth dispatching
            }

            _jobScheduler.MarkRunning(job.Id);
            try
            {
                var succeeded = await _pipeline.RunAsync(
                    job,
                    "watch",
                    _ => Task.FromResult<string?>(claimed.LocalFilePath),
                    _stoppingToken);

                if (succeeded)
                {
                    _tracker.MarkTransferred(claimed);
                    await SafeLedgerCallAsync(
                        () => _ledger.MarkTransferredAsync(job.Id, claimed.LocalFilePath, _clock.UtcNow, CancellationToken.None),
                        job.Id, claimed.LocalFilePath, "mark transferred");
                }
                else
                {
                    int attempts;
                    try
                    {
                        attempts = await _ledger.IncrementAttemptCountAsync(job.Id, claimed.LocalFilePath, CancellationToken.None);
                    }
                    catch (Exception ex)
                    {
                        _logger.LogWarning(ex, "WATCH could not increment attempt count for job {JobId} file {FilePath}; assuming attempt 1", job.Id, claimed.LocalFilePath);
                        attempts = 1;
                    }

                    if (attempts < _options.MaxWatchTransferAttempts)
                    {
                        _tracker.OfferCandidate(claimed, out _); // re-insert; a genuinely newer arrival still wins naturally via normal comparison
                    }
                    else
                    {
                        _logger.LogError("Giving up on {FilePath} (job {JobId}) after {Attempts} failed transfer attempts", claimed.LocalFilePath, job.Id, attempts);
                        await SafeLedgerCallAsync(
                            () => _ledger.MarkFailedPermanentAsync(job.Id, claimed.LocalFilePath, CancellationToken.None),
                            job.Id, claimed.LocalFilePath, "mark failed permanent");
                    }
                }
            }
            finally
            {
                _jobScheduler.MarkFinished(job.Id);
            }
        }
        finally
        {
            _tracker.EndDispatchCycle(job.Id);
            TryStartDispatchCycle(job); // re-check immediately for a newer candidate that arrived meanwhile
        }
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private async Task ReportWatchEventIfChangedAsync(int jobId, string filePath, bool active, string? detail)
    {
        bool changed;
        try
        {
            changed = await _ledger.TrySetLockTimeoutAlertActiveAsync(jobId, filePath, active, CancellationToken.None);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "WATCH ledger flag update failed for job {JobId} file {FilePath}; skipping watch-event report this cycle", jobId, filePath);
            return;
        }

        if (!changed)
        {
            return;
        }

        try
        {
            await _backendApiClient.ReportWatchEventAsync(
                new WatchEventRequest { BackupJobId = jobId, EventType = "FILE_LOCK_TIMEOUT", Active = active, FilePath = filePath, Detail = detail },
                CancellationToken.None);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(
                ex, "Failed to report watch event (active={Active}) for job {JobId} file {FilePath}; reverting ledger " +
                "flag so the next lock-check cycle retries", active, jobId, filePath);

            try
            {
                await _ledger.TrySetLockTimeoutAlertActiveAsync(jobId, filePath, !active, CancellationToken.None);
            }
            catch (Exception revertEx)
            {
                _logger.LogWarning(revertEx, "Failed to revert WATCH ledger flag for job {JobId} file {FilePath}", jobId, filePath);
            }
        }
    }

    private async Task SafeLedgerCallAsync(Func<Task> action, int jobId, string filePath, string what)
    {
        try
        {
            await action();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "WATCH ledger operation '{What}' failed for job {JobId} file {FilePath}", what, jobId, filePath);
        }
    }

    private static long? TryGetFileSize(string filePath)
    {
        try
        {
            return new FileInfo(filePath).Length;
        }
        catch (IOException)
        {
            return null;
        }
    }

    private sealed class WatchedJobState
    {
        public required int JobId { get; init; }
        public required string WatchDirectory { get; init; }
        public required IWatchDirectoryMonitor Monitor { get; init; }
        public required CancellationTokenSource Cts { get; init; }
        public required bool UseMsdbForJob { get; init; }
        public required DateTimeOffset LastReconciliationUtc { get; set; }
        public ConcurrentDictionary<string, byte> ActiveReadinessLoopPaths { get; } = new(StringComparer.OrdinalIgnoreCase);
    }
}
