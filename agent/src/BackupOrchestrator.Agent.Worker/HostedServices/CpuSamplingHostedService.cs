using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.HostedServices;

/// <summary>
/// Ticks ICpuUsageSampler.Sample() every CpuSamplingIntervalSeconds -- much
/// more frequently than the heartbeat interval, so several sub-interval
/// ticks accumulate for HeartbeatHostedService's TakeAndReset() to average.
/// </summary>
public sealed class CpuSamplingHostedService : BackgroundService
{
    private readonly ICpuUsageSampler _cpuUsageSampler;
    private readonly AgentOptions _options;
    private readonly ILogger<CpuSamplingHostedService> _logger;

    public CpuSamplingHostedService(
        ICpuUsageSampler cpuUsageSampler,
        IOptions<AgentOptions> options,
        ILogger<CpuSamplingHostedService> logger)
    {
        _cpuUsageSampler = cpuUsageSampler;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(_options.CpuSamplingIntervalSeconds));

        do
        {
            _cpuUsageSampler.Sample();
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
