using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Core.Tests.Support;

namespace BackupOrchestrator.Agent.Core.Tests.Scheduling;

public sealed class CopyWindowCalculatorTests
{
    // Tuesday -- deliberately NOT a weekend, so CopyWindowWeekendUnrestricted
    // never accidentally masks the window logic under test unless a case
    // explicitly opts into it.
    private static readonly DateTimeOffset Weekday = new(2026, 8, 11, 0, 0, 0, TimeSpan.Zero);
    private static readonly DateTimeOffset Saturday = new(2026, 8, 15, 0, 0, 0, TimeSpan.Zero);
    private static readonly DateTimeOffset Sunday = new(2026, 8, 16, 0, 0, 0, TimeSpan.Zero);

    private static DateTimeOffset AtHourUtc(DateTimeOffset day, int hour) => day.AddHours(hour);

    [Theory]
    [InlineData(0)]
    [InlineData(9)]
    [InlineData(12)]
    [InlineData(17)]
    [InlineData(23)]
    public void IsWithinCopyWindow_NullStartAndEndHour_AlwaysTrueRegardlessOfTime(int hour)
    {
        var job = TestData.Job(timezone: "UTC", copyWindowStartHour: null, copyWindowEndHour: null);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, AtHourUtc(Weekday, hour));

        Assert.True(result);
    }

    [Theory]
    [InlineData(8, false)] // just before start
    [InlineData(9, true)] // start hour -- inclusive
    [InlineData(12, true)] // inside
    [InlineData(16, true)] // just before end
    [InlineData(17, false)] // end hour -- exclusive
    [InlineData(18, false)] // just after end
    public void IsWithinCopyWindow_SameDayWindow_MatchesInclusiveStartExclusiveEnd(int hour, bool expected)
    {
        var job = TestData.Job(timezone: "UTC", copyWindowStartHour: 9, copyWindowEndHour: 17);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, AtHourUtc(Weekday, hour));

        Assert.Equal(expected, result);
    }

    [Theory]
    [InlineData(17, false)] // just before start (daytime, outside)
    [InlineData(18, true)] // start hour -- inclusive
    [InlineData(20, true)] // evening, past start
    [InlineData(23, true)] // late night
    [InlineData(0, true)] // past midnight, still in window
    [InlineData(2, true)] // still in window
    [InlineData(8, true)] // just before end
    [InlineData(9, false)] // end hour -- exclusive
    [InlineData(10, false)] // daytime, outside
    [InlineData(12, false)] // daytime, outside
    public void IsWithinCopyWindow_MidnightWraparoundWindow_MatchesInclusiveStartExclusiveEnd(int hour, bool expected)
    {
        var job = TestData.Job(timezone: "UTC", copyWindowStartHour: 18, copyWindowEndHour: 9);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, AtHourUtc(Weekday, hour));

        Assert.Equal(expected, result);
    }

    [Fact]
    public void IsWithinCopyWindow_WeekendUnrestrictedTrue_SaturdayOutsideWindowHour_StillTrue()
    {
        var job = TestData.Job(
            timezone: "UTC", copyWindowStartHour: 9, copyWindowEndHour: 17, copyWindowWeekendUnrestricted: true);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, AtHourUtc(Saturday, 3)); // 03:00, well outside 9-17

        Assert.True(result);
    }

    [Fact]
    public void IsWithinCopyWindow_WeekendUnrestrictedTrue_SundayOutsideWindowHour_StillTrue()
    {
        var job = TestData.Job(
            timezone: "UTC", copyWindowStartHour: 9, copyWindowEndHour: 17, copyWindowWeekendUnrestricted: true);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, AtHourUtc(Sunday, 3));

        Assert.True(result);
    }

    [Fact]
    public void IsWithinCopyWindow_WeekendUnrestrictedTrue_WeekdayOutsideWindowHour_NormalLogicApplies()
    {
        var job = TestData.Job(
            timezone: "UTC", copyWindowStartHour: 9, copyWindowEndHour: 17, copyWindowWeekendUnrestricted: true);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, AtHourUtc(Weekday, 3)); // Tuesday, outside window

        Assert.False(result);
    }

    [Fact]
    public void IsWithinCopyWindow_WeekendUnrestrictedFalse_SaturdayOutsideWindowHour_StillFalse()
    {
        var job = TestData.Job(
            timezone: "UTC", copyWindowStartHour: 9, copyWindowEndHour: 17, copyWindowWeekendUnrestricted: false);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, AtHourUtc(Saturday, 3));

        Assert.False(result);
    }

    [Fact]
    public void IsWithinCopyWindow_NonUtcTimezone_ConvertsBeforeApplyingWindow_InsideAfterConversion()
    {
        // Asia/Tashkent is UTC+5, no DST. 05:00 UTC -> 10:00 local, inside a 9-17 window.
        // If the implementation forgot to convert (used the raw UTC hour of 5), this would
        // wrongly evaluate to false -- so this genuinely exercises the ConvertTime call.
        var job = TestData.Job(timezone: "Asia/Tashkent", copyWindowStartHour: 9, copyWindowEndHour: 17);
        var nowUtc = AtHourUtc(Weekday, 5);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, nowUtc);

        Assert.True(result);
    }

    [Fact]
    public void IsWithinCopyWindow_NonUtcTimezone_ConvertsBeforeApplyingWindow_OutsideAfterConversion()
    {
        // 13:00 UTC -> 18:00 local in Asia/Tashkent (UTC+5), outside a 9-17 window.
        // If the implementation forgot to convert (used the raw UTC hour of 13), this would
        // wrongly evaluate to true.
        var job = TestData.Job(timezone: "Asia/Tashkent", copyWindowStartHour: 9, copyWindowEndHour: 17);
        var nowUtc = AtHourUtc(Weekday, 13);

        var result = CopyWindowCalculator.IsWithinCopyWindow(job, nowUtc);

        Assert.False(result);
    }

    [Fact]
    public void NextWindowOpenUtc_AlreadyWithinWindow_ReturnsNowUnchanged()
    {
        var job = TestData.Job(timezone: "UTC", copyWindowStartHour: 9, copyWindowEndHour: 17);
        var nowUtc = AtHourUtc(Weekday, 12);

        var result = CopyWindowCalculator.NextWindowOpenUtc(job, nowUtc);

        Assert.Equal(nowUtc, result);
    }

    [Fact]
    public void NextWindowOpenUtc_OutsideSameDayWindow_ReturnsEarliestOpeningInstant()
    {
        var job = TestData.Job(timezone: "UTC", copyWindowStartHour: 9, copyWindowEndHour: 17);
        var nowUtc = AtHourUtc(Weekday, 20); // evening, outside the 9-17 window

        var result = CopyWindowCalculator.NextWindowOpenUtc(job, nowUtc);

        Assert.True(result > nowUtc);
        Assert.True(CopyWindowCalculator.IsWithinCopyWindow(job, result));
        // Earliest: the hour immediately before must still be outside the window.
        Assert.False(CopyWindowCalculator.IsWithinCopyWindow(job, result.AddHours(-1)));
        // Exact expected value for this deterministic case: next day 09:00 UTC.
        Assert.Equal(AtHourUtc(Weekday.AddDays(1), 9), result);
    }

    [Fact]
    public void NextWindowOpenUtc_OutsideMidnightWraparoundWindow_ReturnsEarliestOpeningInstant()
    {
        var job = TestData.Job(timezone: "UTC", copyWindowStartHour: 18, copyWindowEndHour: 9);
        var nowUtc = AtHourUtc(Weekday, 12); // daytime, outside the 18-9 wraparound window

        var result = CopyWindowCalculator.NextWindowOpenUtc(job, nowUtc);

        Assert.True(result > nowUtc);
        Assert.True(CopyWindowCalculator.IsWithinCopyWindow(job, result));
        Assert.False(CopyWindowCalculator.IsWithinCopyWindow(job, result.AddHours(-1)));
        Assert.Equal(AtHourUtc(Weekday, 18), result);
    }
}
