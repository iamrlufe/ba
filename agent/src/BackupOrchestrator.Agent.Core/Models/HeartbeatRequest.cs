namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>Mirrors app/schemas/agent.py::AgentHeartbeatRequest.</summary>
public sealed class HeartbeatRequest
{
    public required bool Reachable { get; init; }
    public IReadOnlyList<DiskUsageItem> Disks { get; init; } = [];
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
