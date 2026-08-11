using System.Collections.Concurrent;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Scheduling;

/// <summary>
/// Thread-safe IJobCache backed by a plain dictionary swap -- ReplaceAll
/// builds a new dictionary and atomically publishes it via Volatile.Write,
/// so readers (SchedulerHostedService's polling loop) never observe a
/// partially-updated snapshot and never block on a lock held by the poller.
/// </summary>
public sealed class InMemoryJobCache : IJobCache
{
    private volatile IReadOnlyDictionary<int, BackupJobDto> _jobsById =
        new Dictionary<int, BackupJobDto>();

    public void ReplaceAll(IReadOnlyList<BackupJobDto> jobs)
    {
        var next = new ConcurrentDictionary<int, BackupJobDto>();
        foreach (var job in jobs)
        {
            next[job.Id] = job;
        }

        _jobsById = next;
    }

    public IReadOnlyList<BackupJobDto> GetAll() => _jobsById.Values.ToList();

    public BackupJobDto? GetById(int backupJobId) =>
        _jobsById.TryGetValue(backupJobId, out var job) ? job : null;
}
