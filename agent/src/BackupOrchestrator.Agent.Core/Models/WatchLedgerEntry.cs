namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>Mirrors the readiness_state column of the watch_ledger SQLite table (see IWatchLedger / SqliteWatchLedger).</summary>
public enum WatchReadinessState
{
    NOT_READY,
    READY,
    TRANSFERRED,
    SUPERSEDED,
    VANISHED,
    FAILED_PERMANENT,
}

/// <summary>
/// One row of the watch_ledger SQLite table -- the reconciliation/dedup
/// record for a single file discovered under a WATCH job's watch_directory.
/// Deliberately NOT derived from GET /api/backup-records (see IWatchLedger
/// doc comment): this is purely agent-local bookkeeping, RemotePath is
/// informational only and never a backend dedup source of truth.
/// </summary>
public sealed class WatchLedgerEntry
{
    public required long Id { get; init; }
    public required int BackupJobId { get; init; }
    public required string FilePath { get; init; }
    public long? FileSizeBytes { get; init; }
    public DateTimeOffset? OrderingTimestampUtc { get; init; }

    /// <summary>"Msdb" | "LockCheck" -- WatchDetectionMethod.ToString(), or null before READY.</summary>
    public string? DetectionMethod { get; init; }

    public required WatchReadinessState ReadinessState { get; init; }
    public required DateTimeOffset FirstSeenAtUtc { get; init; }
    public required DateTimeOffset LastCheckedAtUtc { get; init; }
    public DateTimeOffset? TransferredAtUtc { get; init; }
    public string? RemotePath { get; init; }
    public required int TransferAttemptCount { get; init; }
    public required bool LockTimeoutAlertActive { get; init; }
}
