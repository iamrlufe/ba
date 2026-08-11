using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Monitoring;

namespace BackupOrchestrator.Agent.Worker.Monitoring;

/// <summary>
/// Accumulates CPU-delta ticks (one per CpuSamplingHostedService tick, every
/// CpuSamplingIntervalSeconds) between heartbeats, and hands
/// HeartbeatHostedService an averaged snapshot via TakeAndReset(). All state
/// is protected by _gate since Sample() and TakeAndReset() run on
/// independent hosted-service timers and can race.
/// </summary>
public sealed class CpuUsageSampler : ICpuUsageSampler
{
    private readonly IProcessSnapshotProvider _processSnapshotProvider;
    private readonly IClock _clock;
    private readonly ILogger<CpuUsageSampler> _logger;

    private readonly object _gate = new();

    // Delta-tracking baseline -- persists across TakeAndReset() calls.
    private IReadOnlyList<ProcessSnapshotItem>? _lastSnapshot;
    private DateTimeOffset? _lastSnapshotAt;

    // Accumulated since the last TakeAndReset() -- reset by TakeAndReset().
    private readonly List<double> _subIntervalCpuPercentages = [];
    private readonly Dictionary<string, TopProcessItem> _latestProcessSamples = [];

    public CpuUsageSampler(
        IProcessSnapshotProvider processSnapshotProvider,
        IClock clock,
        ILogger<CpuUsageSampler> logger)
    {
        _processSnapshotProvider = processSnapshotProvider;
        _clock = clock;
        _logger = logger;
    }

    public void Sample()
    {
        IReadOnlyList<ProcessSnapshotItem> currentSnapshot;
        try
        {
            currentSnapshot = _processSnapshotProvider.GetSnapshot();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Process snapshot collection failed; skipping this CPU sampling tick");
            return;
        }

        var now = _clock.UtcNow;

        lock (_gate)
        {
            if (_lastSnapshot is null || _lastSnapshotAt is null)
            {
                // First-tick warm-up -- no delta available yet, just establish baseline.
                _lastSnapshot = currentSnapshot;
                _lastSnapshotAt = now;
                return;
            }

            var elapsed = now - _lastSnapshotAt.Value;
            if (elapsed <= TimeSpan.Zero)
            {
                // Clock skew -- skip without mutating baseline.
                _logger.LogDebug("CPU sampling tick skipped: non-positive elapsed time ({Elapsed}), possible clock skew", elapsed);
                return;
            }

            var (machineCpuPct, perProcess) = CpuDeltaCalculator.Compute(
                _lastSnapshot, currentSnapshot, elapsed, Environment.ProcessorCount);

            _subIntervalCpuPercentages.Add(machineCpuPct);
            foreach (var item in perProcess)
            {
                _latestProcessSamples[item.ProcessName] = item;
            }

            // Baseline advances every successful tick, unconditionally.
            _lastSnapshot = currentSnapshot;
            _lastSnapshotAt = now;
        }
    }

    public CpuUsageSnapshot? TakeAndReset()
    {
        lock (_gate)
        {
            if (_subIntervalCpuPercentages.Count == 0)
            {
                return null;
            }

            var snapshot = new CpuUsageSnapshot
            {
                MachineCpuUsagePct = _subIntervalCpuPercentages.Average(),
                LatestProcessSamples = _latestProcessSamples.Values.ToList(),
            };

            _subIntervalCpuPercentages.Clear();
            _latestProcessSamples.Clear();

            return snapshot;
        }
    }
}
