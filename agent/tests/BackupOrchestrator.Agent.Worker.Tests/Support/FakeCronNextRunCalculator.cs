using BackupOrchestrator.Agent.Core.Scheduling;

namespace BackupOrchestrator.Agent.Worker.Tests.Support;

/// <summary>
/// Direct analogue of BackupOrchestrator.Agent.Core.Tests.Support.FakeCronNextRunCalculator
/// (duplicated here since test projects don't share a test-support-library
/// seam in this codebase yet -- see the equivalent note on this project's
/// TestClock/TestData). Results are queued per cron-expression string and
/// consumed FIFO.
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
