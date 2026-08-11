using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// Accumulates sub-interval CPU-delta ticks (see CpuSamplingHostedService)
/// and hands the averaged result to HeartbeatHostedService once per
/// heartbeat. The Worker-project implementation (CpuUsageSampler) must be
/// thread-safe: Sample() and TakeAndReset() run on independent hosted-service
/// timers and can race.
/// </summary>
public interface ICpuUsageSampler
{
    /// <summary>
    /// Called every CpuSamplingIntervalSeconds by CpuSamplingHostedService.
    /// Thread-safe against concurrent TakeAndReset().
    /// </summary>
    void Sample();

    /// <summary>
    /// Called once per heartbeat by HeartbeatHostedService. Returns null if
    /// zero Sample() ticks completed since the last reset -- caller must
    /// send Metrics: null in that case, never a zeroed object.
    /// </summary>
    CpuUsageSnapshot? TakeAndReset();
}

public sealed class CpuUsageSnapshot
{
    /// <summary>Already averaged across the accumulated ticks, clamped [0,100].</summary>
    public required double MachineCpuUsagePct { get; init; }

    /// <summary>CpuPct is unbounded above 100 (per-process, multi-core).</summary>
    public required IReadOnlyList<TopProcessItem> LatestProcessSamples { get; init; }
}
