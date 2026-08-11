namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// One entry of MetricsPayload.TopProcesses on the wire, and also the
/// per-process result shape produced by CpuDeltaCalculator/CpuUsageSampler
/// internally.
/// </summary>
public sealed class TopProcessItem
{
    public required string ProcessName { get; init; }
    public int? Pid { get; init; }

    /// <summary>Unbounded above 100 (per-process, multi-core) -- do not clamp.</summary>
    public required double CpuPct { get; init; }

    public required long MemoryBytes { get; init; }
}
