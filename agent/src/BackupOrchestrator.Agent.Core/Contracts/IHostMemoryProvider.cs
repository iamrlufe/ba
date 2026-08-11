using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>Testability seam over the Win32 GlobalMemoryStatusEx call.</summary>
public interface IHostMemoryProvider
{
    MemoryStatus GetMemoryStatus();
}
