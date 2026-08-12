using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Scheduling;

/// <summary>
/// Pure copy-window logic, applying to BOTH trigger modes (SCHEDULE and
/// WATCH) -- see BackupJobDto.CopyWindow* doc comments. Deferring a transfer
/// past a closed window is purely a matter of delaying WHEN the transfer
/// happens; it never affects file detection/discovery for WATCH jobs.
/// </summary>
public static class CopyWindowCalculator
{
    public static bool IsWithinCopyWindow(BackupJobDto job, DateTimeOffset nowUtc)
    {
        if (job.CopyWindowStartHour is null || job.CopyWindowEndHour is null)
        {
            return true;
        }

        var timeZone = TimeZoneInfo.FindSystemTimeZoneById(job.Timezone);
        var localNow = TimeZoneInfo.ConvertTime(nowUtc, timeZone);

        if (job.CopyWindowWeekendUnrestricted
            && localNow.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday)
        {
            return true;
        }

        var start = job.CopyWindowStartHour.Value;
        var end = job.CopyWindowEndHour.Value;
        var hour = localNow.Hour;

        return start < end
            ? hour >= start && hour < end          // same-day window
            : hour >= start || hour < end;          // wraps past midnight, e.g. 18 -> 9
    }

    /// <summary>
    /// Returns nowUtc unchanged if already within the window. Otherwise
    /// scans forward hour-by-hour in local time (bounded to 8 days as a safety
    /// cap) re-evaluating IsWithinCopyWindow, returning the first UTC instant it
    /// becomes true. Deliberately implemented by REUSING IsWithinCopyWindow
    /// (never a hand-derived closed-form computation) so the two functions can
    /// never disagree, and DST-transition correctness falls out of
    /// TimeZoneInfo.ConvertTime for free. Runs once per dispatch decision, not
    /// in a hot loop -- the O(hours) scan cost is irrelevant.
    /// </summary>
    public static DateTimeOffset NextWindowOpenUtc(BackupJobDto job, DateTimeOffset nowUtc)
    {
        if (IsWithinCopyWindow(job, nowUtc))
        {
            return nowUtc;
        }

        var candidate = nowUtc;
        var limit = nowUtc.AddDays(8);
        while (candidate < limit)
        {
            candidate = candidate.AddHours(1);
            if (IsWithinCopyWindow(job, candidate))
            {
                return candidate;
            }
        }

        throw new InvalidOperationException(
            $"Could not find a copy window opening for job {job.Id} within 8 days -- check its configuration.");
    }
}
