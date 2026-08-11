namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Mirrors app/schemas/agent.py's metrics sub-object on
/// AgentHeartbeatRequest. Sent as null (never a zeroed instance) when
/// collection fails or nothing was sampled this cycle -- see
/// ICpuUsageSampler.TakeAndReset.
/// </summary>
public sealed class MetricsPayload
{
    /// <summary>0-100, already averaged over the heartbeat interval.</summary>
    public required double CpuUsagePct { get; init; }

    public required long MemoryUsedBytes { get; init; }
    public required long MemoryTotalBytes { get; init; }

    /// <summary>
    /// Max 10 items. Per-process CpuPct is UNBOUNDED above 100 (per-process,
    /// multi-core) -- see TopProcessItem.
    /// </summary>
    public IReadOnlyList<TopProcessItem> TopProcesses { get; init; } = [];
}
