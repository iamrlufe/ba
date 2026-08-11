using System.Collections.Concurrent;
using Cronos;

namespace BackupOrchestrator.Agent.Core.Scheduling;

/// <summary>
/// Cronos-backed ICronNextRunCalculator. Parsed CronExpression/TimeZoneInfo
/// instances are cached by their source string -- BackupJob.schedule_cron
/// rarely changes and re-parsing on every scheduler tick for every job would
/// be wasteful.
/// </summary>
public sealed class CronosNextRunCalculator : ICronNextRunCalculator
{
    private readonly ConcurrentDictionary<string, CronExpression> _expressionCache = new();
    private readonly ConcurrentDictionary<string, TimeZoneInfo> _timeZoneCache = new();

    public DateTimeOffset? GetNextOccurrence(string cronExpression, string ianaTimeZoneId, DateTimeOffset afterUtc)
    {
        var expression = _expressionCache.GetOrAdd(
            cronExpression, static expr => CronExpression.Parse(expr, CronFormat.Standard));

        var timeZone = _timeZoneCache.GetOrAdd(
            ianaTimeZoneId, static tz => TimeZoneInfo.FindSystemTimeZoneById(tz));

        return expression.GetNextOccurrence(afterUtc, timeZone);
    }
}
