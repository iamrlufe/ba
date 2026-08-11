using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Transfer;
using BackupOrchestrator.Agent.Core.Tests.Support;

namespace BackupOrchestrator.Agent.Core.Tests.Transfer;

/// <summary>
/// Boundary-condition coverage for ProgressThrottler.ShouldReport: a sample is
/// reported iff elapsed-since-last-report >= minInterval OR
/// abs(percent-delta) >= minPercentDelta. Uses a controllable TestClock so
/// "just under" vs. "exactly at" the threshold is deterministic, not
/// wall-clock-flaky.
/// </summary>
public sealed class ProgressThrottlerTests
{
    private static TransferProgress Progress(int percent, long bytes = 0) =>
        new() { PercentComplete = percent, BytesTransferred = bytes };

    [Fact]
    public void ShouldReport_FirstSample_AlwaysReturnsTrue()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(3), minPercentDelta: 1);

        var result = throttler.ShouldReport(Progress(0));

        Assert.True(result);
    }

    [Fact]
    public void ShouldReport_ElapsedJustUnderInterval_AndPercentUnchanged_ReturnsFalse()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(3), minPercentDelta: 5);
        throttler.ShouldReport(Progress(10)); // baseline

        clock.Advance(TimeSpan.FromSeconds(3) - TimeSpan.FromMilliseconds(1));
        var result = throttler.ShouldReport(Progress(10)); // 0 percent delta

        Assert.False(result);
    }

    [Fact]
    public void ShouldReport_ElapsedExactlyAtInterval_ReturnsTrue()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(3), minPercentDelta: 5);
        throttler.ShouldReport(Progress(10)); // baseline

        clock.Advance(TimeSpan.FromSeconds(3)); // exactly at threshold, inclusive
        var result = throttler.ShouldReport(Progress(10));

        Assert.True(result);
    }

    [Fact]
    public void ShouldReport_ElapsedJustOverInterval_ReturnsTrue()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(3), minPercentDelta: 5);
        throttler.ShouldReport(Progress(10));

        clock.Advance(TimeSpan.FromSeconds(3) + TimeSpan.FromMilliseconds(1));
        var result = throttler.ShouldReport(Progress(10));

        Assert.True(result);
    }

    [Fact]
    public void ShouldReport_PercentDeltaJustUnderThreshold_AndElapsedBelowInterval_ReturnsFalse()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(5), minPercentDelta: 5);
        throttler.ShouldReport(Progress(10)); // baseline at 10%

        clock.Advance(TimeSpan.FromSeconds(1)); // well under the 5s interval
        var result = throttler.ShouldReport(Progress(14)); // delta = 4, just under 5

        Assert.False(result);
    }

    [Fact]
    public void ShouldReport_PercentDeltaExactlyAtThreshold_ReturnsTrueEvenIfElapsedBelowInterval()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(5), minPercentDelta: 5);
        throttler.ShouldReport(Progress(10));

        clock.Advance(TimeSpan.FromSeconds(1));
        var result = throttler.ShouldReport(Progress(15)); // delta = 5, exactly at threshold

        Assert.True(result);
    }

    [Fact]
    public void ShouldReport_PercentDeltaJustOverThreshold_ReturnsTrue()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(5), minPercentDelta: 5);
        throttler.ShouldReport(Progress(10));

        clock.Advance(TimeSpan.FromSeconds(1));
        var result = throttler.ShouldReport(Progress(16)); // delta = 6

        Assert.True(result);
    }

    [Fact]
    public void ShouldReport_NegativePercentDelta_UsesAbsoluteValue()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(5), minPercentDelta: 5);
        throttler.ShouldReport(Progress(50));

        clock.Advance(TimeSpan.FromSeconds(1));
        var result = throttler.ShouldReport(Progress(43)); // delta = -7, |delta| = 7 >= 5

        Assert.True(result);
    }

    [Fact]
    public void ShouldReport_AfterReporting_BaselineResets_SoImmediateNextSampleUnderBothThresholdsIsSuppressed()
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(3), minPercentDelta: 1);

        Assert.True(throttler.ShouldReport(Progress(10))); // first sample, always true
        clock.Advance(TimeSpan.FromSeconds(3));
        Assert.True(throttler.ShouldReport(Progress(11))); // elapsed threshold met -> reports, resets baseline

        // Immediately after, with no further elapsed time and no percent change, should be suppressed.
        var result = throttler.ShouldReport(Progress(11));

        Assert.False(result);
    }

    [Theory]
    [InlineData(2)]
    [InlineData(5)]
    public void ShouldReport_HonorsConfiguredIntervalBoundary_AcrossSpecRange(int intervalSeconds)
    {
        var clock = new TestClock(DateTimeOffset.UtcNow);
        var throttler = new ProgressThrottler(clock, TimeSpan.FromSeconds(intervalSeconds), minPercentDelta: 100);
        throttler.ShouldReport(Progress(0));

        clock.Advance(TimeSpan.FromSeconds(intervalSeconds) - TimeSpan.FromMilliseconds(1));
        Assert.False(throttler.ShouldReport(Progress(0)));

        clock.Advance(TimeSpan.FromMilliseconds(1)); // now exactly at interval
        Assert.True(throttler.ShouldReport(Progress(0)));
    }
}
