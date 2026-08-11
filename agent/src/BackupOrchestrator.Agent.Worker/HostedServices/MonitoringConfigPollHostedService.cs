using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.HostedServices;

/// <summary>
/// Polls GET /api/agents/{server_id}/monitoring-config on
/// MonitoringConfigPollIntervalSeconds (much less frequent than jobs -- this
/// config only changes on rare manual admin edits) and full-snapshot-replaces
/// IMonitoringConfigCache. HeartbeatHostedService always reads the latest
/// cache, never calls the backend directly.
/// </summary>
public sealed class MonitoringConfigPollHostedService : BackgroundService
{
    private readonly IBackendApiClient _backendApiClient;
    private readonly IMonitoringConfigCache _monitoringConfigCache;
    private readonly AgentOptions _options;
    private readonly ILogger<MonitoringConfigPollHostedService> _logger;

    public MonitoringConfigPollHostedService(
        IBackendApiClient backendApiClient,
        IMonitoringConfigCache monitoringConfigCache,
        IOptions<AgentOptions> options,
        ILogger<MonitoringConfigPollHostedService> logger)
    {
        _backendApiClient = backendApiClient;
        _monitoringConfigCache = monitoringConfigCache;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(_options.MonitoringConfigPollIntervalSeconds));

        do
        {
            await PollOnceAsync(stoppingToken);
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    internal async Task PollOnceAsync(CancellationToken cancellationToken)
    {
        try
        {
            var result = await _backendApiClient.GetMonitoringConfigAsync(_options.ServerId, cancellationToken);
            _monitoringConfigCache.Replace(result.ServiceNames);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(ex, "Monitoring-config poll failed; continuing with last-known config");
        }
    }
}
