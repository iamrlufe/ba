namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>Result of IBackendApiClient.GetMonitoringConfigAsync.</summary>
public sealed class MonitoringConfigResult
{
    public required int ServerId { get; init; }
    public required IReadOnlyList<string> ServiceNames { get; init; }
}
