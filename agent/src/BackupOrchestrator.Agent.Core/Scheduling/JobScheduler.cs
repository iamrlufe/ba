using System.Collections.Concurrent;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;

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

            var nextFire = GetOrComputeNextFire(job, now);
            if (nextFire > now)
            {
                continue;
            }

            // Due. Advance bookkeeping regardless of overlap outcome so a
            // skipped fire doesn't cause the same slot to be re-evaluated
            // as "due" on every subsequent tick until the run finishes.
            var following = _cronCalculator.GetNextOccurrence(job.ScheduleCron, job.Timezone, now);
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

        return new SchedulerTickResult { DueJobs = due, SkippedOverlapJobs = skippedOverlap };
    }

    /// <summary>Effective watchdog timeout for a job: its own configured value, or the fallback default.</summary>
    public static TimeSpan GetWatchdogTimeout(BackupJobDto job, int defaultJobTimeoutMinutes)
    {
        var minutes = job.ExpectedMaxDurationMinutes ?? defaultJobTimeoutMinutes;
        return TimeSpan.FromMinutes(minutes);
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
        var seed = _cronCalculator.GetNextOccurrence(job.ScheduleCron, job.Timezone, now.AddSeconds(-1));
        var nextFire = seed ?? DateTimeOffset.MaxValue;
        _nextFireUtcByJobId[job.Id] = nextFire;
        return nextFire;
    }
}

public sealed class SchedulerTickResult
{
    public required IReadOnlyList<BackupJobDto> DueJobs { get; init; }
    public required IReadOnlyList<BackupJobDto> SkippedOverlapJobs { get; init; }
}
