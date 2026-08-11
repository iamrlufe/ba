using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>Testability seam over Process.GetProcesses().</summary>
public interface IProcessSnapshotProvider
{
    IReadOnlyList<ProcessSnapshotItem> GetSnapshot();
}
