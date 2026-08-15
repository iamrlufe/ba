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
///
/// Queued entries can be either a normal DateTimeOffset? result (Enqueue) or
/// an exception to throw (EnqueueThrow) -- the latter is what lets tests
/// exercise JobScheduler.Tick()'s per-job try/catch (CronFormatException /
/// TimeZoneNotFoundException / InvalidTimeZoneException isolation) without
/// depending on Cronos actually parsing a malformed string.
/// </summary>
public sealed class FakeCronNextRunCalculator : ICronNextRunCalculator
{
    private readonly Dictionary<string, Queue<object?>> _queuedResults = new();
    public List<(string CronExpression, string TimeZone, DateTimeOffset AfterUtc)> Calls { get; } = new();

    public void Enqueue(string cronExpression, DateTimeOffset? result)
    {
        GetQueue(cronExpression).Enqueue(result);
    }

    /// <summary>
    /// Queues an exception to be thrown (instead of a value returned) the
    /// next time GetNextOccurrence is called for this exact cron-expression
    /// string. Consumed FIFO alongside any Enqueue(...) calls for the same
    /// key -- mix and match freely to script e.g. "seed call throws, then a
    /// later call after a fix succeeds".
    /// </summary>
    public void EnqueueThrow(string cronExpression, Exception exceptionToThrow)
    {
        GetQueue(cronExpression).Enqueue(exceptionToThrow);
    }

    private Queue<object?> GetQueue(string cronExpression)
    {
        if (!_queuedResults.TryGetValue(cronExpression, out var queue))
        {
            queue = new Queue<object?>();
            _queuedResults[cronExpression] = queue;
        }

        return queue;
    }

    public DateTimeOffset? GetNextOccurrence(string cronExpression, string ianaTimeZoneId, DateTimeOffset afterUtc)
    {
        Calls.Add((cronExpression, ianaTimeZoneId, afterUtc));

        if (_queuedResults.TryGetValue(cronExpression, out var queue) && queue.Count > 0)
        {
            var item = queue.Dequeue();
            if (item is Exception exceptionToThrow)
            {
                throw exceptionToThrow;
            }

            return (DateTimeOffset?)item;
        }

        throw new InvalidOperationException(
            $"FakeCronNextRunCalculator: no queued result for cron expression '{cronExpression}'. " +
            "Call Enqueue(...)/EnqueueThrow(...) before invoking Tick().");
    }
}
