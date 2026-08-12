namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>How a WatchCandidateFile's OrderingTimestampUtc was established.</summary>
public enum WatchDetectionMethod
{
    Msdb,
    LockCheck,
}

/// <summary>
/// A file that has been confirmed READY (fully written) for a WATCH-mode
/// backup job -- see WatchCandidateTracker/WatchCandidateComparer. Produced
/// by WatchHostedService's per-file readiness-detection loop once either
/// ISqlBackupFinishDetector or IFileLockChecker confirms the file is done.
/// </summary>
public sealed class WatchCandidateFile
{
    public required int BackupJobId { get; init; }
    public required string LocalFilePath { get; init; }
    public required DateTimeOffset OrderingTimestampUtc { get; init; }
    public required WatchDetectionMethod DetectionMethod { get; init; }
    public required long FileSizeBytes { get; init; }
}
