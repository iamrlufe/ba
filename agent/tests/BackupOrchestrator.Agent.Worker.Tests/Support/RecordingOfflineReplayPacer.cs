using BackupOrchestrator.Agent.Core.Contracts;

namespace BackupOrchestrator.Agent.Worker.Tests.Support;

/// <summary>
/// Test double for IOfflineReplayPacer -- completes PauseAsync immediately
/// (no real Task.Delay) while recording every requested duration, so a
/// simulated large offline-queue backlog can be replayed in a test in
/// milliseconds instead of real wall-clock minutes. Honors cancellation the
/// same way the real TaskDelayOfflineReplayPacer would (throws
/// OperationCanceledException if the token is already cancelled), so tests
/// exercising OfflineReplayHostedService's cancellation handling behave
/// realistically.
/// </summary>
public sealed class RecordingOfflineReplayPacer : IOfflineReplayPacer
{
    public List<TimeSpan> Requested { get; } = [];

    /// <summary>
    /// When set, the call to PauseAsync at this 1-indexed call number cancels
    /// <see cref="CancellationSourceToCancel"/> and then throws
    /// OperationCanceledException directly from within PauseAsync itself --
    /// simulating cancellation surfacing *during* the wait (as the real
    /// TaskDelayOfflineReplayPacer's Task.Delay would when the token fires
    /// mid-await) rather than being pre-checked via IsCancellationRequested
    /// before PauseAsync is even called. Requires
    /// <see cref="CancellationSourceToCancel"/> to be set. Leaving this null
    /// preserves the original behavior (immediate completion, recording the
    /// requested duration).
    /// </summary>
    public int? ThrowOnCallNumber { get; set; }

    public CancellationTokenSource? CancellationSourceToCancel { get; set; }

    private int _callCount;

    public Task PauseAsync(TimeSpan duration, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        _callCount++;

        if (ThrowOnCallNumber == _callCount)
        {
            CancellationSourceToCancel!.Cancel();
            throw new OperationCanceledException("simulated cancellation during pause", cancellationToken);
        }

        Requested.Add(duration);
        return Task.CompletedTask;
    }
}
