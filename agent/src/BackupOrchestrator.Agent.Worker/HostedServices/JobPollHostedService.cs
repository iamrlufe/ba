using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.HostedServices;

/// <summary>
/// Polls GET /api/agents/{server_id}/jobs on JobPollIntervalSeconds and
/// full-snapshot-replaces IJobCache -- SchedulerHostedService always reads
/// the latest cache, so job add/edit/disable changes take effect on the next
/// poll with no restart needed. Pages through the full result set (limit/offset)
/// since a single server could plausibly have more than the 200-item default
/// page size of jobs.
/// </summary>
public sealed class JobPollHostedService : BackgroundService
{
    private const int PageSize = 200;

    private readonly IBackendApiClient _backendApiClient;
    private readonly IJobCache _jobCache;
    private readonly AgentOptions _options;
    private readonly ILogger<JobPollHostedService> _logger;

    public JobPollHostedService(
        IBackendApiClient backendApiClient,
        IJobCache jobCache,
        IOptions<AgentOptions> options,
        ILogger<JobPollHostedService> logger)
    {
        _backendApiClient = backendApiClient;
        _jobCache = jobCache;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(_options.JobPollIntervalSeconds));

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
            var allJobs = new List<BackupJobDto>();
            var offset = 0;

            while (true)
            {
                var page = await _backendApiClient.GetJobsAsync(_options.ServerId, PageSize, offset, cancellationToken);
                allJobs.AddRange(page.Items);

                if (allJobs.Count >= page.Total || page.Items.Count == 0)
                {
                    break;
                }

                offset += PageSize;
            }

            _jobCache.ReplaceAll(allJobs);
            _logger.LogDebug("Job poll succeeded: {Count} jobs cached", allJobs.Count);
        }
        catch (BackendUnavailableException ex)
        {
            // Per spec: continue operating on the last-known IJobCache
            // snapshot while offline -- do not clear/replace the cache on a
            // failed poll, and there's nothing to enqueue for a read-only
            // poll (jobs-list has no offline event type).
            _logger.LogWarning(ex, "Job poll failed (backend unavailable); continuing with last-known job snapshot");
        }
    }
}
