using BackupOrchestrator.Agent.Core.Replay;

namespace BackupOrchestrator.Agent.Core.Tests.Replay;

/// <summary>
/// Pure/no-mocks coverage of OfflineReplayBackoffCalculator: escalation of
/// the inter-pass delay after consecutive failed replay passes, reset on a
/// pass that completes fully, and the max-delay cap (including the overflow
/// guard for a large failure streak).
/// </summary>
public sealed class OfflineReplayBackoffCalculatorTests
{
    private static readonly TimeSpan BaseInterval = TimeSpan.FromSeconds(30);

    [Fact]
    public void NextPassDelay_NoFailuresRecorded_ReturnsBaseInterval()
    {
        var calculator = new OfflineReplayBackoffCalculator(BaseInterval, multiplier: 2.0, maxDelay: TimeSpan.FromSeconds(300));

        Assert.Equal(0, calculator.ConsecutiveFailedPasses);
        Assert.Equal(BaseInterval, calculator.NextPassDelay());
    }

    [Fact]
    public void RecordPassOutcome_ConsecutiveFailures_EscalatesByMultiplierEachTime()
    {
        var calculator = new OfflineReplayBackoffCalculator(BaseInterval, multiplier: 2.0, maxDelay: TimeSpan.FromSeconds(10_000));

        calculator.RecordPassOutcome(completedFully: false);
        Assert.Equal(1, calculator.ConsecutiveFailedPasses);
        Assert.Equal(TimeSpan.FromSeconds(60), calculator.NextPassDelay());

        calculator.RecordPassOutcome(completedFully: false);
        Assert.Equal(2, calculator.ConsecutiveFailedPasses);
        Assert.Equal(TimeSpan.FromSeconds(120), calculator.NextPassDelay());

        calculator.RecordPassOutcome(completedFully: false);
        Assert.Equal(3, calculator.ConsecutiveFailedPasses);
        Assert.Equal(TimeSpan.FromSeconds(240), calculator.NextPassDelay());
    }

    [Fact]
    public void RecordPassOutcome_EscalationExceedsMax_ReturnsCappedMaxDelay()
    {
        var maxDelay = TimeSpan.FromSeconds(300);
        var calculator = new OfflineReplayBackoffCalculator(BaseInterval, multiplier: 2.0, maxDelay: maxDelay);

        // 30 * 2^4 = 480s, which exceeds the 300s cap.
        for (var i = 0; i < 4; i++)
        {
            calculator.RecordPassOutcome(completedFully: false);
        }

        var delay = calculator.NextPassDelay();

        Assert.Equal(maxDelay, delay);
        Assert.True(delay >= TimeSpan.Zero);
    }

    [Fact]
    public void RecordPassOutcome_VeryLargeFailureStreak_DoesNotOverflowOrGoNegative_StaysAtCappedMaxDelay()
    {
        var maxDelay = TimeSpan.FromSeconds(300);
        var calculator = new OfflineReplayBackoffCalculator(BaseInterval, multiplier: 2.0, maxDelay: maxDelay);

        for (var i = 0; i < 10_000; i++)
        {
            calculator.RecordPassOutcome(completedFully: false);
        }

        var delay = calculator.NextPassDelay();

        Assert.Equal(maxDelay, delay);
        Assert.True(delay >= TimeSpan.Zero);
    }

    [Fact]
    public void RecordPassOutcome_SuccessAfterFailures_ResetsStreakToBaseInterval()
    {
        var calculator = new OfflineReplayBackoffCalculator(BaseInterval, multiplier: 2.0, maxDelay: TimeSpan.FromSeconds(300));

        calculator.RecordPassOutcome(completedFully: false);
        calculator.RecordPassOutcome(completedFully: false);
        calculator.RecordPassOutcome(completedFully: false);
        Assert.Equal(3, calculator.ConsecutiveFailedPasses);

        calculator.RecordPassOutcome(completedFully: true);

        Assert.Equal(0, calculator.ConsecutiveFailedPasses);
        Assert.Equal(BaseInterval, calculator.NextPassDelay());
    }
}
