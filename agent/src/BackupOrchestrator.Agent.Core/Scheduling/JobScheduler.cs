using System.Collections.Concurrent;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using Cronos;

namespace BackupOrchestrator.Agent.Core.Scheduling;

/// <summary>
/// Pure scheduling decision logic, deliberately free of any hosting/timer/
/// transfer concerns so it's unit-testable. SchedulerHostedService (Worker)
/// owns the actual timer loop and calls Tick() periodically, then acts on
/// the returned due jobs.
///
/// Overlap policy (DECISIONS #1): skip-and-log. If a job's next fire time
/// has arrived while its previous run is still in flight, Tick() skips it
/// entirely for this call -- it does NOT queue it up to fire immediately
/// afterward. The next cron tick re-evaluates from a fresh "now", exactly as
/// if the skipped fire never happened. Per-job next-fire bookkeeping still
/// advances normally (via ICronNextRunCalculator against the job's real
/// schedule), so a skipped fire does not cause rapid catch-up firing.
/// </summary>
public sealed class JobScheduler
{
    private readonly ICronNextRunCalculator _cronCalculator;
    private readonly IClock _clock;
    private readonly Dictionary<int, DateTimeOffset> _nextFireUtcByJobId = new();

    /// <summary>
    /// Thread-safety note: MarkRunning/IsRunning are called from Tick(),
    /// which always runs on the scheduler's own timer-tick thread, but
    /// MarkFinished is invoked from a `.ContinueWith(..., TaskScheduler.Default)`
    /// continuation on an arbitrary thread-pool thread whenever a job's
    /// RunJobAsync completes -- i.e. genuinely concurrent with the timer
    /// thread, with no ordering relationship between the two. A plain
    /// HashSet&lt;int&gt; is not safe under that access pattern (can corrupt
    /// the set, or make IsRunning wrongly return false for a still-running
    /// job -- exactly the overlap bug skip-and-log exists to prevent), so
    /// this uses ConcurrentDictionary instead; the value is unused (`byte`
    /// sentinel), only key presence matters.
    /// </summary>
    private readonly ConcurrentDictionary<int, byte> _runningJobIds = new();

    /// <summary>
    /// In-memory (not durable -- confirmed decision, agent restart resets it)
    /// per-job throttle state for cron/timezone parse failures. Keyed by
    /// BackupJob.Id. Exists so a job with an invalid ScheduleCron/Timezone
    /// produces exactly one ERROR log line and one backend report per distinct
    /// bad (cron, timezone) pair, not one per 15s Tick -- see Tick()'s
    /// per-job try/catch and the reconciliation pass at the end of Tick().
    /// Mutated both synchronously within Tick() (single logical caller,
    /// SchedulerHostedService's timer loop) and from the fire-and-forget
    /// backend-report continuation in SchedulerHostedService (genuinely
    /// concurrent with the next Tick) -- MarkScheduleTransitionLogged/
    /// MarkScheduleTransitionReportedToBackend use a compare-and-swap loop
    /// via ConcurrentDictionary.TryUpdate for that reason.
    /// </summary>
    private readonly ConcurrentDictionary<int, ScheduleErrorState> _scheduleErrorStates = new();

    public JobScheduler(ICronNextRunCalculator cronCalculator, IClock clock)
    {
        _cronCalculator = cronCalculator;
        _clock = clock;
    }

    /// <summary>Marks a job as currently executing -- must be paired with a later MarkFinished call.</summary>
    public void MarkRunning(int backupJobId) => _runningJobIds[backupJobId] = 0;

    public void MarkFinished(int backupJobId) => _runningJobIds.TryRemove(backupJobId, out _);

    public bool IsRunning(int backupJobId) => _runningJobIds.ContainsKey(backupJobId);

    /// <summary>
    /// Evaluates every job in the current snapshot and returns the ones due
    /// to fire right now. Skipped-due-to-overlap jobs are reported separately
    /// so the caller can log them at Warning per spec, without conflating
    /// them with jobs that simply aren't due yet.
    /// </summary>
    public SchedulerTickResult Tick(IReadOnlyList<BackupJobDto> jobs)
    {
        var now = _clock.UtcNow;
        var due = new List<BackupJobDto>();
        var skippedOverlap = new List<BackupJobDto>();

        foreach (var job in jobs)
        {
            if (!job.IsEnabled)
            {
                continue;
            }

            if (job.TriggerMode != "SCHEDULE")
            {
                // WATCH-mode jobs are driven by WatchHostedService, not the
                // cron loop. Placed BEFORE ScheduleCron is ever touched --
                // required since ScheduleCron is nullable (null for WATCH).
                continue;
            }

            // A single try/catch wraps BOTH Cronos call sites below
            // (GetOrComputeNextFire and the "advance bookkeeping"
            // GetNextOccurrence call once the job is recognized due) --
            // this is the only place with per-job context AND the natural
            // per-job iteration needed to isolate one bad BackupJob's cron/
            // timezone from crashing the whole Tick (and therefore the whole
            // agent host, since BackgroundServiceExceptionBehavior.StopHost
            // is intentionally left in place for everything else). `continue`
            // statements inside this try are safe -- they simply advance the
            // foreach without triggering the catch blocks.
            try
            {
                var nextFire = GetOrComputeNextFire(job, now);

                // Successful parse: if this job was previously broken, this
                // is the recovery transition. Checked here -- immediately
                // after the successful parse, before the "not due yet"
                // continue below -- so recovery is reported on the very tick
                // parsing starts succeeding again, not deferred until the
                // job happens to also be due.
                HandleScheduleRecovery(job);

                if (nextFire > now)
                {
                    continue;
                }

                // Due. Advance bookkeeping regardless of overlap outcome so a
                // skipped fire doesn't cause the same slot to be re-evaluated
                // as "due" on every subsequent tick until the run finishes.
                // job.ScheduleCron! -- safe: the TriggerMode != "SCHEDULE" filter
                // above already `continue`d for any job where this could be null
                // (backend invariant: ScheduleCron is required iff SCHEDULE).
                var following = _cronCalculator.GetNextOccurrence(job.ScheduleCron!, job.Timezone, now);
                if (following.HasValue)
                {
                    _nextFireUtcByJobId[job.Id] = following.Value;
                }
                else
                {
                    _nextFireUtcByJobId.Remove(job.Id);
                }

                if (IsRunning(job.Id))
                {
                    skippedOverlap.Add(job);
                    continue;
                }

                due.Add(job);
            }
            catch (CronFormatException ex)
            {
                HandleScheduleError(job, ex.Message);
                continue;
            }
            catch (TimeZoneNotFoundException ex)
            {
                HandleScheduleError(job, ex.Message);
                continue;
            }
            catch (InvalidTimeZoneException ex)
            {
                HandleScheduleError(job, ex.Message);
                continue;
            }
        }

        // Reconciliation pass: a job that's still marked broken in
        // _scheduleErrorStates but is no longer in the current snapshot (or
        // no longer eligible -- disabled, or switched away from SCHEDULE) is
        // never going to reach the try/catch above again (it's filtered out
        // by the IsEnabled/TriggerMode checks earlier in the loop, or simply
        // absent from `jobs`), so without this pass its alert would never
        // resolve. Treat all three cases as recovery.
        var eligibleJobIds = new HashSet<int>(
            jobs.Where(j => j.IsEnabled && j.TriggerMode == "SCHEDULE").Select(j => j.Id));

        foreach (var entry in _scheduleErrorStates)
        {
            if (entry.Value.IsBroken && !eligibleJobIds.Contains(entry.Key))
            {
                var current = entry.Value;
                _scheduleErrorStates.TryUpdate(
                    entry.Key,
                    current with { Fingerprint = string.Empty, IsBroken = false, ErrorMessage = null, Logged = false, BackendAcked = false },
                    current);
            }
        }

        var scheduleErrorNotifications = new List<ScheduleErrorNotification>();
        foreach (var entry in _scheduleErrorStates)
        {
            var state = entry.Value;
            if (state.Logged && state.BackendAcked)
            {
                continue;
            }

            scheduleErrorNotifications.Add(new ScheduleErrorNotification
            {
                JobId = entry.Key,
                JobName = state.JobName,
                IsBroken = state.IsBroken,
                ErrorMessage = state.ErrorMessage,
                NeedsLogging = !state.Logged,
                NeedsBackendReport = !state.BackendAcked,
                Fingerprint = state.Fingerprint,
            });
        }

        return new SchedulerTickResult
        {
            DueJobs = due,
            SkippedOverlapJobs = skippedOverlap,
            ScheduleErrorNotifications = scheduleErrorNotifications,
        };
    }

    /// <summary>
    /// Records (or leaves untouched) the broken-schedule state for a job
    /// whose cron/timezone just failed to parse. Only called from within
    /// Tick()'s per-job try/catch, so this itself does not need to be
    /// concurrency-safe against another Tick -- only one Tick runs at a time
    /// (SchedulerHostedService's sequential PeriodicTimer loop) -- but it can
    /// race with a concurrent MarkScheduleTransitionReportedToBackend call
    /// from a previous tick's fire-and-forget backend report; a plain
    /// overwrite here is intentional (a fresh parse failure this tick always
    /// supersedes whatever report-ack bookkeeping was in flight).
    /// </summary>
    private void HandleScheduleError(BackupJobDto job, string? errorMessage)
    {
        // "|" separator between ScheduleCron and Timezone avoids a
        // (theoretical, unlikely for real cron strings) fingerprint
        // collision between two different (cron, timezone) pairs that would
        // otherwise concatenate to the same string -- e.g. "0 5" + "*UTC" vs
        // "0 5*" + "UTC". job.ScheduleCron! is safe here: this method is only
        // reached for jobs that already passed the TriggerMode == "SCHEDULE"
        // filter earlier in Tick()'s loop (same invariant as the existing
        // ScheduleCron! usages in this class).
        var fingerprint = job.ScheduleCron! + "|" + job.Timezone;

        var shouldRecord = !_scheduleErrorStates.TryGetValue(job.Id, out var existing)
            || existing.Fingerprint != fingerprint
            || !existing.IsBroken;

        if (shouldRecord)
        {
            _scheduleErrorStates[job.Id] = new ScheduleErrorState(
                Fingerprint: fingerprint,
                JobName: job.Name,
                IsBroken: true,
                ErrorMessage: errorMessage,
                Logged: false,
                BackendAcked: false);
        }
    }

    /// <summary>See HandleScheduleError's concurrency note -- same reasoning applies here.</summary>
    private void HandleScheduleRecovery(BackupJobDto job)
    {
        if (_scheduleErrorStates.TryGetValue(job.Id, out var existing) && existing.IsBroken)
        {
            _scheduleErrorStates[job.Id] = existing with
            {
                Fingerprint = string.Empty,
                IsBroken = false,
                ErrorMessage = null,
                Logged = false,
                BackendAcked = false,
            };
        }
    }

    /// <summary>Effective watchdog timeout for a job: its own configured value, or the fallback default.</summary>
    public static TimeSpan GetWatchdogTimeout(BackupJobDto job, int defaultJobTimeoutMinutes)
    {
        var minutes = job.ExpectedMaxDurationMinutes ?? defaultJobTimeoutMinutes;
        return TimeSpan.FromMinutes(minutes);
    }

    /// <summary>
    /// Called by SchedulerHostedService once it has logged a schedule-error
    /// (or recovery) notification -- by the same pairing convention as
    /// MarkRunning/MarkFinished. Uses a compare-and-swap loop because this
    /// can race with a concurrent Tick() call recording a fresh error for the
    /// same job (see HandleScheduleError's doc comment).
    /// </summary>
    public void MarkScheduleTransitionLogged(int backupJobId)
    {
        while (_scheduleErrorStates.TryGetValue(backupJobId, out var current))
        {
            if (_scheduleErrorStates.TryUpdate(backupJobId, current with { Logged = true }, current))
            {
                return;
            }
        }
    }

    /// <summary>
    /// Called by SchedulerHostedService once it has successfully reported a
    /// schedule-error (or recovery) notification to the backend. A recovered
    /// notification (fingerprint == "") is removed from the throttle map
    /// entirely -- there's nothing left to remember once the backend has
    /// acknowledged the resolution, and a future fresh parse failure for the
    /// same job should be treated as a brand-new occurrence, not a
    /// re-flagging of stale bookkeeping. A still-broken notification just has
    /// its BackendAcked flag set.
    ///
    /// Every branch below is gated on <c>current.Fingerprint == fingerprint</c>
    /// -- fire-and-forget backend calls can complete out of order (a slow
    /// call for an OLDER state can complete after a newer Tick() has already
    /// overwritten that job's state with something else -- a new failure, a
    /// new recovery, or a fingerprint change). Without this check, a stale
    /// ack could mark a completely different, still-unreported state as
    /// acked (or, for the fingerprint=="" removal case, delete a fresh
    /// broken-state entry outright), permanently losing that report. A
    /// mismatch means "this ack is for a state that no longer exists" --
    /// discard it silently; the CURRENT state's own NeedsBackendReport stays
    /// true and gets its own report attempt on a later Tick.
    /// </summary>
    public void MarkScheduleTransitionReportedToBackend(int backupJobId, string fingerprint)
    {
        while (_scheduleErrorStates.TryGetValue(backupJobId, out var current))
        {
            if (current.Fingerprint != fingerprint)
            {
                return; // stale ack for a state that has since changed -- discard
            }

            if (fingerprint.Length == 0)
            {
                // CAS-based conditional remove (key AND value must match) --
                // NOT a plain TryRemove(key), which would delete whatever the
                // CURRENT value is even if it changed between the
                // TryGetValue above and this call.
                if (((ICollection<KeyValuePair<int, ScheduleErrorState>>)_scheduleErrorStates)
                    .Remove(new KeyValuePair<int, ScheduleErrorState>(backupJobId, current)))
                {
                    return;
                }

                continue;
            }

            if (_scheduleErrorStates.TryUpdate(backupJobId, current with { BackendAcked = true }, current))
            {
                return;
            }
        }
    }

    private DateTimeOffset GetOrComputeNextFire(BackupJobDto job, DateTimeOffset now)
    {
        if (_nextFireUtcByJobId.TryGetValue(job.Id, out var cached))
        {
            return cached;
        }

        // First time we've seen this job: seed from "just before now" so a
        // cron expression matching the current instant fires immediately
        // rather than waiting a full period.
        // job.ScheduleCron! -- only reachable for TriggerMode == "SCHEDULE"
        // jobs; see the call site in Tick().
        var seed = _cronCalculator.GetNextOccurrence(job.ScheduleCron!, job.Timezone, now.AddSeconds(-1));
        var nextFire = seed ?? DateTimeOffset.MaxValue;
        _nextFireUtcByJobId[job.Id] = nextFire;
        return nextFire;
    }
}

public sealed class SchedulerTickResult
{
    public required IReadOnlyList<BackupJobDto> DueJobs { get; init; }
    public required IReadOnlyList<BackupJobDto> SkippedOverlapJobs { get; init; }

    /// <summary>
    /// Jobs whose cron/timezone parse state changed (broke or recovered)
    /// and/or still need logging/backend-reporting -- see JobScheduler's
    /// _scheduleErrorStates doc comment and Tick()'s per-job try/catch.
    /// </summary>
    public required IReadOnlyList<ScheduleErrorNotification> ScheduleErrorNotifications { get; init; }
}

/// <summary>
/// In-memory (not durable) per-job cron/timezone parse-failure throttle
/// state. IsBroken=true means the most recent Tick() attempt to parse this
/// job's ScheduleCron/Timezone failed; Fingerprint identifies exactly which
/// (cron, timezone) pair that failure is for, so a subsequent edit to a still
/// -broken job's schedule produces a fresh notification rather than being
/// silently swallowed by the "already broken" throttle. Fingerprint is reset
/// to "" on recovery -- MarkScheduleTransitionReportedToBackend uses that as
/// the signal to drop the entry entirely once the backend has acknowledged
/// the recovery.
/// </summary>
internal sealed record ScheduleErrorState(
    string Fingerprint,
    string JobName,
    bool IsBroken,
    string? ErrorMessage,
    bool Logged,
    bool BackendAcked);

/// <summary>
/// A pending schedule-error transition (broken or recovered) that
/// SchedulerHostedService still needs to log and/or report to the backend
/// for. NeedsLogging/NeedsBackendReport are independent -- a notification can
/// need one, the other, or both, depending on which side (log vs. backend
/// report) already succeeded on a previous tick.
/// </summary>
public sealed record ScheduleErrorNotification
{
    public required int JobId { get; init; }
    public required string JobName { get; init; }
    public required bool IsBroken { get; init; }
    public string? ErrorMessage { get; init; }
    public required bool NeedsLogging { get; init; }
    public required bool NeedsBackendReport { get; init; }
    public required string Fingerprint { get; init; }
}
