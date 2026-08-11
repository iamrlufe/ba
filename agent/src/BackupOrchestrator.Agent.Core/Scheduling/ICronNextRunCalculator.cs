namespace BackupOrchestrator.Agent.Core.Scheduling;

/// <summary>
/// Seam over Cronos so JobScheduler's overlap/dispatch logic is unit
/// testable without depending on wall-clock cron parsing directly.
/// </summary>
public interface ICronNextRunCalculator
{
    /// <summary>
    /// Returns the next occurrence strictly after <paramref name="afterUtc"/>
    /// for the given 5-field cron expression, interpreted in
    /// <paramref name="ianaTimeZoneId"/>, or null if the expression has no
    /// future occurrence (should not happen for standard 5-field cron, but
    /// Cronos can return null in edge cases -- callers must handle it).
    /// </summary>
    DateTimeOffset? GetNextOccurrence(string cronExpression, string ianaTimeZoneId, DateTimeOffset afterUtc);
}
