using System.Text.Json;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Monitoring;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.HostedServices;

/// <summary>
/// Reads local disk usage via DriveInfo.GetDrives(), CPU/memory metrics via
/// ICpuUsageSampler/IHostMemoryProvider, and monitored-service statuses via
/// IMonitoringConfigCache/IServiceStatusChecker, then POSTs it all to
/// /api/agents/{server_id}/heartbeat on HeartbeatIntervalSeconds. On backend
/// unavailability the payload is enqueued to the offline queue as a
/// Heartbeat event -- safe to lose/age-evict per spec, but still queued for
/// best-effort replay rather than silently dropped on the spot.
/// </summary>
public sealed class HeartbeatHostedService : BackgroundService
{
    private readonly IBackendApiClient _backendApiClient;
    private readonly IOfflineEventQueue _offlineQueue;
    private readonly ICpuUsageSampler _cpuUsageSampler;
    private readonly IHostMemoryProvider _hostMemoryProvider;
    private readonly IMonitoringConfigCache _monitoringConfigCache;
    private readonly IServiceStatusChecker _serviceStatusChecker;
    private readonly AgentOptions _options;
    private readonly ILogger<HeartbeatHostedService> _logger;

    public HeartbeatHostedService(
        IBackendApiClient backendApiClient,
        IOfflineEventQueue offlineQueue,
        ICpuUsageSampler cpuUsageSampler,
        IHostMemoryProvider hostMemoryProvider,
        IMonitoringConfigCache monitoringConfigCache,
        IServiceStatusChecker serviceStatusChecker,
        IOptions<AgentOptions> options,
        ILogger<HeartbeatHostedService> logger)
    {
        _backendApiClient = backendApiClient;
        _offlineQueue = offlineQueue;
        _cpuUsageSampler = cpuUsageSampler;
        _hostMemoryProvider = hostMemoryProvider;
        _monitoringConfigCache = monitoringConfigCache;
        _serviceStatusChecker = serviceStatusChecker;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(_options.HeartbeatIntervalSeconds));

        do
        {
            await SendHeartbeatOnceAsync(stoppingToken);
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    internal async Task SendHeartbeatOnceAsync(CancellationToken cancellationToken)
    {
        var request = BuildHeartbeatRequest();

        try
        {
            var result = await _backendApiClient.SendHeartbeatAsync(request, cancellationToken);
            _logger.LogDebug("Heartbeat sent successfully, server status = {ServerStatus}", result.ServerStatus);
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(ex, "Heartbeat failed (backend unavailable); enqueueing for offline replay");
            var payloadJson = JsonSerializer.Serialize(request, AgentJsonOptions.Default);
            await _offlineQueue.EnqueueAsync(QueuedEventType.Heartbeat, payloadJson, jobRunId: null, cancellationToken);
        }
    }

    internal HeartbeatRequest BuildHeartbeatRequest()
    {
        var disks = new List<DiskUsageItem>();

        foreach (var drive in DriveInfo.GetDrives())
        {
            if (!drive.IsReady)
            {
                continue;
            }

            try
            {
                disks.Add(new DiskUsageItem
                {
                    MountPath = drive.RootDirectory.FullName,
                    Label = drive.VolumeLabel,
                    TotalBytes = drive.TotalSize,
                    FreeBytes = drive.AvailableFreeSpace,
                });
            }
            catch (IOException)
            {
                // Drive became unready between the IsReady check and reading
                // its properties (e.g. removable media ejected) -- skip it,
                // not fatal to the whole heartbeat.
            }
        }

        MetricsPayload? metrics = null;
        try
        {
            // Memory is read BEFORE TakeAndReset() deliberately: TakeAndReset()
            // clears the CPU sampler's accumulator as a side effect, so if
            // memory collection fails, the CPU samples for this interval must
            // still be sitting in the accumulator for the NEXT heartbeat to
            // pick up -- calling TakeAndReset() first would silently discard
            // them even though this cycle's metrics end up null either way.
            var mem = _hostMemoryProvider.GetMemoryStatus();
            var cpu = _cpuUsageSampler.TakeAndReset();
            if (cpu is not null)
            {
                metrics = new MetricsPayload
                {
                    CpuUsagePct = cpu.MachineCpuUsagePct,
                    MemoryUsedBytes = mem.UsedBytes,
                    MemoryTotalBytes = mem.TotalBytes,
                    TopProcesses = TopProcessSelector.Select(cpu.LatestProcessSamples),
                };
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Metrics collection failed this heartbeat cycle; sending metrics=null");
            metrics = null;
        }

        IReadOnlyList<ServiceStatusItem>? services = null;
        try
        {
            var configuredNames = _monitoringConfigCache.CurrentServiceNames;
            if (configuredNames is not null)
            {
                var results = new List<ServiceStatusItem>(configuredNames.Count);
                foreach (var name in configuredNames)
                {
                    try
                    {
                        results.Add(_serviceStatusChecker.CheckStatus(name));
                    }
                    catch (Exception ex)
                    {
                        _logger.LogWarning(ex, "Service status check failed for {ServiceName}; skipping", name);
                    }
                }

                services = results;
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Service status collection failed this heartbeat cycle; sending services=null");
            services = null;
        }

        return new HeartbeatRequest { Reachable = true, Disks = disks, Metrics = metrics, Services = services };
    }
}
