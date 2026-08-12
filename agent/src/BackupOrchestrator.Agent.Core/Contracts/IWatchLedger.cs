using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// The seam over the watch_ledger SQLite table (a new table added to the
/// same offline_queue.db file used by IOfflineEventQueue -- see
/// SqliteWatchLedger). Reconciliation/dedup bookkeeping for WATCH-mode file
/// discovery and readiness tracking; deliberately NOT derived from
/// GET /api/backup-records -- the backend's stored remote name is
/// agent-chosen/timestamp-prefixed, not reversibly mappable to a local file.
/// </summary>
public interface IWatchLedger
{
    Task<bool> IsKnownAsync(int backupJobId, string filePath, CancellationToken ct);

    Task InsertNotReadyAsync(int backupJobId, string filePath, long? fileSizeBytes, DateTimeOffset nowUtc, CancellationToken ct);

    Task MarkReadyAsync(int backupJobId, string filePath, DateTimeOffset orderingTimestampUtc, string detectionMethod, CancellationToken ct);

    Task MarkSupersededAsync(int backupJobId, string filePath, CancellationToken ct);

    Task MarkVanishedAsync(int backupJobId, string filePath, CancellationToken ct);

    Task MarkTransferredAsync(int backupJobId, string filePath, DateTimeOffset transferredAtUtc, CancellationToken ct);

    Task<int> IncrementAttemptCountAsync(int backupJobId, string filePath, CancellationToken ct);

    Task MarkFailedPermanentAsync(int backupJobId, string filePath, CancellationToken ct);

    /// <summary>Returns true if the state actually changed (for dedup -- see WatchHostedService's timeout-alert flow).</summary>
    Task<bool> TrySetLockTimeoutAlertActiveAsync(int backupJobId, string filePath, bool active, CancellationToken ct);

    /// <summary>For reconciliation diffing (Directory.GetFiles(...) vs. what the ledger already knows about).</summary>
    Task<IReadOnlyList<string>> GetKnownFilePathsAsync(int backupJobId, CancellationToken ct);

    /// <summary>
    /// Full entries (including FirstSeenAtUtc, needed to preserve the correct
    /// lock-check timeout countdown) for every NOT_READY row of this job.
    /// Used by WatchHostedService's reconciliation pass to respawn a
    /// readiness-detection loop for any ledger row whose in-memory loop died
    /// with a prior agent process (crash/restart/deploy) -- otherwise such a
    /// row would sit NOT_READY forever, since a plain path-known check alone
    /// (GetKnownFilePathsAsync) can't distinguish "still being actively
    /// tracked in-memory" from "known to the ledger but orphaned".
    /// </summary>
    Task<IReadOnlyList<WatchLedgerEntry>> GetNotReadyEntriesAsync(int backupJobId, CancellationToken ct);
}
