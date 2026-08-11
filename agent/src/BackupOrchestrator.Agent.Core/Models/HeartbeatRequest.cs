namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>Mirrors app/schemas/agent.py::AgentHeartbeatRequest.</summary>
public sealed class HeartbeatRequest
{
    public required bool Reachable { get; init; }
    public IReadOnlyList<DiskUsageItem> Disks { get; init; } = [];

    /// <summary>
    /// Null if metrics collection failed or nothing to report this cycle --
    /// NEVER a zeroed-out object in that case. See MetricsPayload.
    /// </summary>
    public MetricsPayload? Metrics { get; init; }

    /// <summary>
    /// Null = "don't touch stored data" (nothing successfully polled from
    /// monitoring-config yet, or collection failed this cycle). Empty list =
    /// "explicitly overwrite to empty" (monitoring-config was polled and
    /// returned zero service names). These are NOT equivalent -- see
    /// IMonitoringConfigCache.
    /// </summary>
    public IReadOnlyList<ServiceStatusItem>? Services { get; init; }
}

/// <summary>
/// Deliberately minimal reflection of app/schemas/agent.py::AgentHeartbeatResponse
/// -- the agent only needs to know the call succeeded and the server's
/// resulting status (e.g. to notice it's been administratively DISABLED),
/// it does not need to parse alerts_raised/alerts_resolved.
/// </summary>
public sealed class HeartbeatResult
{
    public required bool Success { get; init; }
    public string? ServerStatus { get; init; }
}
