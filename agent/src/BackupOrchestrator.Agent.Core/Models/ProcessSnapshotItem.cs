namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Internal-only raw process sample used as input to CpuDeltaCalculator --
/// never serialized to the wire.
/// </summary>
public sealed class ProcessSnapshotItem
{
    public required int Pid { get; init; }
    public required string ProcessName { get; init; }
    public required TimeSpan TotalProcessorTime { get; init; }
    public required long WorkingSetBytes { get; init; }
}
