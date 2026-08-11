namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Everything IBackupTransferClient needs to perform one file transfer.
/// RemoteDirectory/RemoteFileName are pre-computed by the caller (see
/// RemotePathBuilder in Scheduling/) -- the transfer client itself has no
/// opinion on path convention, it just uploads to the path it's given.
/// </summary>
public sealed class TransferRequest
{
    public required int BackupJobId { get; init; }
    public required int JobRunId { get; init; }
    public required string LocalSourcePath { get; init; }
    public required string RemoteDirectory { get; init; }
    public required string RemoteFileName { get; init; }
    public required ConnectionConfigDto ConnectionConfig { get; init; }
}

/// <summary>Result of a completed (or failed/cancelled) transfer attempt.</summary>
public sealed class TransferResult
{
    public required bool Success { get; init; }
    public required JobRunStatus Status { get; init; }
    public string? RemotePath { get; init; }
    public long? FileSizeBytes { get; init; }
    public string? Sha256Checksum { get; init; }
    public string? ErrorMessage { get; init; }
}

/// <summary>
/// One progress sample. Raw samples arrive far more frequently than they
/// should be reported to the backend -- see ProgressThrottler, which decides
/// which samples actually get turned into a PATCH.
/// </summary>
public sealed class TransferProgress
{
    public required int PercentComplete { get; init; }
    public required long BytesTransferred { get; init; }
    public long? TotalBytes { get; init; }
    public string? CurrentFileName { get; init; }
}
