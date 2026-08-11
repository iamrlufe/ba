namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>Mirrors app/schemas/disk.py::AgentDiskUsageItem, one sample per heartbeat.</summary>
public sealed class DiskUsageItem
{
    public required string MountPath { get; init; }
    public string? Label { get; init; }
    public required long TotalBytes { get; init; }
    public required long FreeBytes { get; init; }
}
