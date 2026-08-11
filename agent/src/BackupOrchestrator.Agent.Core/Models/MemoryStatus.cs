namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>Result of IHostMemoryProvider.GetMemoryStatus().</summary>
public sealed class MemoryStatus
{
    public required long UsedBytes { get; init; }
    public required long TotalBytes { get; init; }
}
