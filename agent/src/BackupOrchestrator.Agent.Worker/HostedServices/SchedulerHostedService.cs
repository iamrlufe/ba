using System.Collections.Concurrent;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Worker.Pipeline;

namespace BackupOrchestrator.Agent.Worker.HostedServices;

/// <summary>
/// Owns the cron scheduling timer loop AND manual-run pickup for
/// SCHEDULE-mode jobs -- the actual end-to-end run pipeline (create/claim
/// job run -> patch RUNNING -> fetch connection config -> transfer -> create
/// backup record -> complete run) lives in BackupRunPipeline (shared with
/// WatchHostedService). Overlap policy is skip-and-log (DECISIONS #1),
/// enforced by JobScheduler.Tick -- see its doc comment. JobScheduler.Tick()
/// itself skips any job whose TriggerMode is not "SCHEDULE" -- WATCH-mode
/// jobs are driven entirely by WatchHostedService.
///
/// Manual triggering is backend-forbidden (409) for WATCH-mode jobs, so
/// BackupJobDto.PendingManualRunId is only ever populated for SCHEDULE jobs
/// in practice; the TriggerMode check in the manual-dispatch loop below is a
/// defensive no-op, not expected to ever matter.
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
    private readonly BackupRunPipeline _pipeline;
    private readonly IBackendApiClient _backendApiClient;
    private readonly ILogger<SchedulerHostedService> _logger;

    /// <summary>
    /// In-flight guard for schedule-error backend reports, keyed by
    /// BackupJob.Id -- same ConcurrentDictionary-as-a-set pattern as
    /// JobScheduler's own _runningJobIds (value unused, only key presence
    /// matters). Needed because NeedsBackendReport stays true on EVERY tick
    /// (every 15s) until the report actually succeeds; without this guard, a
    /// backend call that takes longer than one tick interval would get a
    /// second, third, etc. concurrent fire-and-forget POST kicked off for the
    /// same job, increasing the odds of out-of-order completion (see
    /// JobScheduler.MarkScheduleTransitionReportedToBackend's fingerprint-
    /// staleness guard) for no benefit -- one in-flight attempt per job is
    /// always enough; the next tick after it completes will naturally retry
    /// if it failed.
    /// </summary>
    private readonly ConcurrentDictionary<int, byte> _inFlightScheduleReports = new();

    public SchedulerHostedService(
        JobScheduler scheduler,
        IJobCache jobCache,
        BackupRunPipeline pipeline,
        IBackendApiClient backendApiClient,
        ILogger<SchedulerHostedService> logger)
    {
        _scheduler = scheduler;
        _jobCache = jobCache;
        _pipeline = pipeline;
        _backendApiClient = backendApiClient;
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

        foreach (var notification in result.ScheduleErrorNotifications)
        {
            if (notification.NeedsLogging)
            {
                if (notification.IsBroken)
                {
                    _logger.LogError(
                        "Backup job {JobId} ({JobName}) has an invalid schedule and cannot be evaluated: {ErrorMessage}",
                        notification.JobId, notification.JobName, notification.ErrorMessage);
                }
                else
                {
                    _logger.LogInformation(
                        "Backup job {JobId} ({JobName}) schedule is valid again; resuming normal cron evaluation",
                        notification.JobId, notification.JobName);
                }

                _scheduler.MarkScheduleTransitionLogged(notification.JobId);
            }

            if (notification.NeedsBackendReport)
            {
                // In-flight guard: skip starting a second concurrent report
                // for the same job while an earlier one (from a previous
                // tick, since NeedsBackendReport stays true until it
                // succeeds) is still outstanding -- see
                // _inFlightScheduleReports' doc comment.
                if (_inFlightScheduleReports.TryAdd(notification.JobId, 0))
                {
                    // Deliberately not awaited -- same fire-and-forget pattern
                    // as the due-job dispatch loop below, so one slow/
                    // unavailable backend call doesn't delay the rest of this
                    // tick. Exceptions are fully handled inside
                    // ReportScheduleErrorAsync, which also always clears the
                    // in-flight guard on completion.
                    _ = ReportScheduleErrorAsync(notification, shutdownToken);
                }
            }
        }

        foreach (var job in result.DueJobs)
        {
            _scheduler.MarkRunning(job.Id);

            // Deliberately not awaited: due jobs run concurrently so one
            // slow transfer doesn't delay another job's fire. Exceptions are
            // fully handled inside BackupRunPipeline.RunAsync -- nothing
            // should escape as an unobserved task exception.
            _ = _pipeline.RunAsync(job, "scheduler", _ => Task.FromResult(job.SourcePath), shutdownToken)
                .ContinueWith(
                    _ => _scheduler.MarkFinished(job.Id),
                    CancellationToken.None,
                    TaskContinuationOptions.ExecuteSynchronously,
                    TaskScheduler.Default);
        }

        // Manual-run pickup: a second pass over the SAME jobs snapshot
        // already fetched above. Only ever applies to SCHEDULE-mode jobs --
        // manual triggering is backend-forbidden (409) for WATCH-mode jobs,
        // so job.TriggerMode != "SCHEDULE" is not expected to ever match
        // here; the check is defensive, not load-bearing.
        foreach (var job in jobs)
        {
            if (job.PendingManualRunId is not { } manualRunId)
            {
                continue;
            }

            if (job.TriggerMode != "SCHEDULE")
            {
                continue;
            }

            if (_scheduler.IsRunning(job.Id))
            {
                continue;
            }

            _scheduler.MarkRunning(job.Id);

            // Same fire-and-forget/MarkFinished pattern as the cron dispatch
            // loop above.
            _ = DispatchManualRunAsync(job, manualRunId, shutdownToken)
                .ContinueWith(
                    _ => _scheduler.MarkFinished(job.Id),
                    CancellationToken.None,
                    TaskContinuationOptions.ExecuteSynchronously,
                    TaskScheduler.Default);
        }
    }

    private async Task DispatchManualRunAsync(BackupJobDto job, int manualRunId, CancellationToken shutdownToken)
    {
        JobRunDto? claimedRun;
        try
        {
            claimedRun = await _backendApiClient.ClaimJobRunAsync(manualRunId, shutdownToken);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(ex, "Could not claim manual job run {JobRunId} for backup job {JobId}; will retry next tick", manualRunId, job.Id);
            return;
        }

        if (claimedRun is null)
        {
            _logger.LogInformation(
                "Manual job run {JobRunId} for backup job {JobId} was already claimed by a concurrent cycle", manualRunId, job.Id);
            return;
        }

        await _pipeline.RunClaimedAsync(job, claimedRun, _ => Task.FromResult(job.SourcePath), shutdownToken);
    }

    /// <summary>
    /// Reports one schedule-error transition (broken or recovered) to the
    /// backend. Mirrors WatchHostedService.ReportWatchEventIfChangedAsync's
    /// failure policy: on BackendUnavailableException, log a Warning and do
    /// NOT enqueue to the offline queue -- the in-memory throttle state in
    /// JobScheduler simply keeps NeedsBackendReport true, so the next tick
    /// retries automatically.
    /// </summary>
    private async Task ReportScheduleErrorAsync(ScheduleErrorNotification notification, CancellationToken shutdownToken)
    {
        try
        {
            try
            {
                await _backendApiClient.ReportScheduleErrorAsync(
                    new ScheduleErrorRequest
                    {
                        BackupJobId = notification.JobId,
                        Active = notification.IsBroken,
                        Detail = notification.ErrorMessage,
                    },
                    shutdownToken);

                _scheduler.MarkScheduleTransitionReportedToBackend(notification.JobId, notification.Fingerprint);
            }
            catch (BackendUnavailableException ex)
            {
                _logger.LogWarning(
                    ex, "Could not report schedule-error transition (active={Active}) for backup job {JobId}; will retry next tick",
                    notification.IsBroken, notification.JobId);
            }
        }
        finally
        {
            // Always clears, success or failure -- otherwise a single failed
            // attempt would permanently wedge this job out of ever being
            // retried by the in-flight guard above.
            _inFlightScheduleReports.TryRemove(notification.JobId, out _);
        }
    }
}
