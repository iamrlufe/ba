using System.Text.Json;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.HostedServices;

/// <summary>
/// Reads local disk usage via DriveInfo.GetDrives() and POSTs it to
/// /api/agents/{server_id}/heartbeat on HeartbeatIntervalSeconds. On backend
/// unavailability the payload is enqueued to the offline queue as a
/// Heartbeat event -- safe to lose/age-evict per spec, but still queued for
/// best-effort replay rather than silently dropped on the spot.
/// </summary>
public sealed class HeartbeatHostedService : BackgroundService
{
    private readonly IBackendApiClient _backendApiClient;
    private readonly IOfflineEventQueue _offlineQueue;
    private readonly AgentOptions _options;
    private readonly ILogger<HeartbeatHostedService> _logger;

    public HeartbeatHostedService(
        IBackendApiClient backendApiClient,
        IOfflineEventQueue offlineQueue,
        IOptions<AgentOptions> options,
        ILogger<HeartbeatHostedService> logger)
    {
        _backendApiClient = backendApiClient;
        _offlineQueue = offlineQueue;
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

    internal static HeartbeatRequest BuildHeartbeatRequest()
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

        return new HeartbeatRequest { Reachable = true, Disks = disks };
    }
}
