namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// In-memory cache of the server's configured monitored-service names,
/// replaced wholesale by MonitoringConfigPollHostedService on every
/// successful poll of GET /api/agents/{server_id}/monitoring-config.
/// HeartbeatHostedService reads from this, never calls the backend directly.
/// </summary>
public interface IMonitoringConfigCache
{
    /// <summary>
    /// Null = never successfully polled since process start (heartbeats
    /// must send services=null while this is null). Non-null (possibly
    /// empty) = last successfully polled snapshot.
    /// </summary>
    IReadOnlyList<string>? CurrentServiceNames { get; }

    void Replace(IReadOnlyList<string> serviceNames);
}
