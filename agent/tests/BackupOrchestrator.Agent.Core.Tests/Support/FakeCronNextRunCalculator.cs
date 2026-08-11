using BackupOrchestrator.Agent.Core.Scheduling;

namespace BackupOrchestrator.Agent.Core.Tests.Support;

/// <summary>
/// Fully scriptable ICronNextRunCalculator test double: results are queued
/// per cron-expression string and consumed FIFO on each call, so a test can
/// deterministically control both the "seed" call (JobScheduler.GetOrComputeNextFire,
/// called with now-1s) and the "advance bookkeeping" call (Tick, called with
/// now) independently of real cron/timezone math -- JobScheduler's overlap
/// and due-check logic is what's under test here, not Cronos itself (see
/// CronosNextRunCalculatorTests for that).
/// </summary>
public sealed class FakeCronNextRunCalculator : ICronNextRunCalculator
{
    private readonly Dictionary<string, Queue<DateTimeOffset?>> _queuedResults = new();
    public List<(string CronExpression, string TimeZone, DateTimeOffset AfterUtc)> Calls { get; } = new();

    public void Enqueue(string cronExpression, DateTimeOffset? result)
    {
        if (!_queuedResults.TryGetValue(cronExpression, out var queue))
        {
            queue = new Queue<DateTimeOffset?>();
            _queuedResults[cronExpression] = queue;
        }

        queue.Enqueue(result);
    }

    public DateTimeOffset? GetNextOccurrence(string cronExpression, string ianaTimeZoneId, DateTimeOffset afterUtc)
    {
        Calls.Add((cronExpression, ianaTimeZoneId, afterUtc));

        if (_queuedResults.TryGetValue(cronExpression, out var queue) && queue.Count > 0)
        {
            return queue.Dequeue();
        }

        throw new InvalidOperationException(
            $"FakeCronNextRunCalculator: no queued result for cron expression '{cronExpression}'. " +
            "Call Enqueue(...) before invoking Tick().");
    }
}
