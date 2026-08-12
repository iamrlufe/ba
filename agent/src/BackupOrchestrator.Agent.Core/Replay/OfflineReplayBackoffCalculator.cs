namespace BackupOrchestrator.Agent.Core.Replay;

/// <summary>
/// Tracks consecutive offline-replay PASS failures (a pass that stopped
/// early on BackendUnavailableException, per OfflineReplayHostedService.
/// ReplayOnceAsync) and computes the delay before the next pass should
/// start. NOT the same as:
///   - RetryPolicyFactory: per-HTTP-call retry within one replay attempt.
///   - the fixed OfflineReplayBatchPauseSeconds pause between batches within
///     one pass (never escalates -- the pass stops at first failure
///     regardless).
/// Pure/no I/O; not thread-safe by design (one instance per hosted service).
/// </summary>
public sealed class OfflineReplayBackoffCalculator
{
    private readonly TimeSpan _baseInterval;
    private readonly double _multiplier;
    private readonly TimeSpan _maxDelay;

    public OfflineReplayBackoffCalculator(TimeSpan baseInterval, double multiplier, TimeSpan maxDelay)
    {
        _baseInterval = baseInterval;
        _multiplier = multiplier;
        _maxDelay = maxDelay;
    }

    public int ConsecutiveFailedPasses { get; private set; }

    /// <summary>Call exactly once after each ReplayOnceAsync completes
    /// (skip the call entirely on a shutdown-triggered early return -- see
    /// OfflineReplayHostedService.ExecuteAsync).</summary>
    public void RecordPassOutcome(bool completedFully)
    {
        ConsecutiveFailedPasses = completedFully ? 0 : ConsecutiveFailedPasses + 1;
    }

    /// <summary>Delay to wait before starting the next pass, given the
    /// current failure streak. Returns baseInterval when
    /// ConsecutiveFailedPasses == 0.</summary>
    public TimeSpan NextPassDelay()
    {
        if (ConsecutiveFailedPasses == 0)
        {
            return _baseInterval;
        }

        // Guard against TimeSpan/double overflow for a large exponent -- cap
        // the exponent itself before computing Math.Pow, since the result is
        // clamped to _maxDelay anyway.
        var exponent = Math.Min(ConsecutiveFailedPasses, 32);
        var scaled = _baseInterval.TotalSeconds * Math.Pow(_multiplier, exponent);
        if (double.IsInfinity(scaled) || scaled > _maxDelay.TotalSeconds)
        {
            return _maxDelay;
        }

        return TimeSpan.FromSeconds(scaled);
    }
}
