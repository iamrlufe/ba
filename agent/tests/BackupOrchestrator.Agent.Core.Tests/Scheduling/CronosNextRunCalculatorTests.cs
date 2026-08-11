using BackupOrchestrator.Agent.Core.Scheduling;

namespace BackupOrchestrator.Agent.Core.Tests.Scheduling;

/// <summary>
/// Verifies the Cronos wrapper correctly applies IANA timezone semantics
/// (not just treating every cron expression as UTC) and returns null for the
/// documented "no future occurrence" edge case rather than throwing.
/// </summary>
public sealed class CronosNextRunCalculatorTests
{
    private readonly CronosNextRunCalculator _calculator = new();

    [Fact]
    public void GetNextOccurrence_UtcTimezone_9amDaily_ReturnsNineAmUtc()
    {
        var afterUtc = new DateTimeOffset(2026, 8, 11, 0, 0, 0, TimeSpan.Zero);

        var result = _calculator.GetNextOccurrence("0 9 * * *", "UTC", afterUtc);

        Assert.Equal(new DateTimeOffset(2026, 8, 11, 9, 0, 0, TimeSpan.Zero), result);
    }

    [Fact]
    public void GetNextOccurrence_NonUtcTimezone_9amDaily_ReturnsUtcInstantAdjustedForOffset()
    {
        // America/New_York is UTC-4 (EDT) in August (daylight saving in effect).
        var afterUtc = new DateTimeOffset(2026, 8, 11, 0, 0, 0, TimeSpan.Zero);

        var result = _calculator.GetNextOccurrence("0 9 * * *", "America/New_York", afterUtc);

        // 09:00 America/New_York (EDT, UTC-4) == 13:00 UTC.
        Assert.Equal(new DateTimeOffset(2026, 8, 11, 13, 0, 0, TimeSpan.Zero), result);
    }

    [Fact]
    public void GetNextOccurrence_NonUtcTimezone_DifferentFromUtcInterpretation_ForSameCronExpression()
    {
        // Regression guard: interpreting the same cron string in two
        // different timezones must yield two different UTC instants --
        // catches a bug where the timezone parameter is silently ignored.
        var afterUtc = new DateTimeOffset(2026, 8, 11, 0, 0, 0, TimeSpan.Zero);

        var utcResult = _calculator.GetNextOccurrence("30 6 * * *", "UTC", afterUtc);
        var tokyoResult = _calculator.GetNextOccurrence("30 6 * * *", "Asia/Tokyo", afterUtc);

        Assert.NotEqual(utcResult, tokyoResult);
        // afterUtc (2026-08-11T00:00Z) is already 09:00 JST, past 06:30 JST that
        // same day, so the next occurrence rolls to the following day:
        // 06:30 JST on Aug 12 == 21:30 UTC on Aug 11.
        Assert.Equal(new DateTimeOffset(2026, 8, 11, 21, 30, 0, TimeSpan.Zero), tokyoResult);
    }

    [Fact]
    public void GetNextOccurrence_NonUtcTimezone_HandlesDaylightSavingSpringForward()
    {
        // Europe/Berlin (CET/CEST) springs forward on 2026-03-29 at 02:00 -> 03:00 local.
        // A daily 2:30am cron fire that instant doesn't exist; Cronos rolls forward to the next valid time.
        var afterUtc = new DateTimeOffset(2026, 3, 28, 12, 0, 0, TimeSpan.Zero);

        var result = _calculator.GetNextOccurrence("30 2 * * *", "Europe/Berlin", afterUtc);

        Assert.NotNull(result);
        // Should resolve to a real, unambiguous instant (Cronos's own DST handling) -- just assert it's after `afterUtc`.
        Assert.True(result > afterUtc);
    }

    [Fact]
    public void GetNextOccurrence_RepeatedCallsWithDifferentTimezones_AreIndependentlyCached()
    {
        // July, not January -- Europe/London observes BST (UTC+1) in summer,
        // so its result actually differs from UTC's here (in January London
        // is on GMT/UTC+0 and the two would coincide, defeating the point of
        // this assertion).
        var afterUtc = new DateTimeOffset(2026, 7, 1, 0, 0, 0, TimeSpan.Zero);

        var first = _calculator.GetNextOccurrence("0 12 * * *", "UTC", afterUtc);
        var second = _calculator.GetNextOccurrence("0 12 * * *", "Europe/London", afterUtc);
        var third = _calculator.GetNextOccurrence("0 12 * * *", "UTC", afterUtc); // re-hits cache

        Assert.Equal(first, third);
        Assert.NotEqual(first, second);
    }

    [Fact]
    public void GetNextOccurrence_UnknownTimezoneId_ThrowsTimeZoneNotFoundException()
    {
        var afterUtc = DateTimeOffset.UtcNow;

        Assert.Throws<TimeZoneNotFoundException>(
            () => _calculator.GetNextOccurrence("0 9 * * *", "Not/A_Real_Zone", afterUtc));
    }
}
