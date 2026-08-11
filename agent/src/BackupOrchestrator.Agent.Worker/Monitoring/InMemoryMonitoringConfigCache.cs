using BackupOrchestrator.Agent.Core.Contracts;

namespace BackupOrchestrator.Agent.Worker.Monitoring;

/// <summary>
/// Direct analogue of InMemoryJobCache, minus the dictionary keying (this
/// cache holds a flat list of service names, not entities looked up by id).
/// </summary>
public sealed class InMemoryMonitoringConfigCache : IMonitoringConfigCache
{
    private volatile IReadOnlyList<string>? _serviceNames;

    public IReadOnlyList<string>? CurrentServiceNames => _serviceNames;

    public void Replace(IReadOnlyList<string> serviceNames) => _serviceNames = serviceNames;
}
