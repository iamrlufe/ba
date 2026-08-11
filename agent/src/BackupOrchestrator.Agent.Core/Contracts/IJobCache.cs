using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// In-memory full-snapshot cache of the server's BackupJobs, replaced wholesale
/// by JobPollHostedService on every successful poll (no restart needed to
/// pick up job changes). SchedulerHostedService reads from this, never calls
/// the backend directly.
/// </summary>
public interface IJobCache
{
    /// <summary>Atomically replaces the entire snapshot.</summary>
    void ReplaceAll(IReadOnlyList<BackupJobDto> jobs);

    IReadOnlyList<BackupJobDto> GetAll();

    BackupJobDto? GetById(int backupJobId);
}
