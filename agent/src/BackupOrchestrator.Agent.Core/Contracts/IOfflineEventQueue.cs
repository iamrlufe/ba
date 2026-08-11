using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// The seam over the SQLite offline_queue.db file. SqliteOfflineEventQueue
/// (Worker project) is the only implementation that touches
/// Microsoft.Data.Sqlite directly.
///
/// Replay contract (DECISIONS #2): oldest-first, real row DELETE on
/// successful ack -- never truncate/rewrite the whole file. Age-eviction
/// (DECISIONS #4) applies only to Heartbeat/JobRunPatch events; JobRunComplete
/// and BackupRecordUpsert are never evicted by EvictExpiredAsync regardless
/// of age.
/// </summary>
public interface IOfflineEventQueue
{
    Task EnqueueAsync(QueuedEventType eventType, string payloadJson, int? jobRunId, CancellationToken cancellationToken);

    /// <summary>Oldest-first. Does not delete -- callers ack individually via DeleteAsync after a successful replay.</summary>
    Task<IReadOnlyList<QueuedEvent>> GetPendingAsync(CancellationToken cancellationToken);

    Task DeleteAsync(long id, CancellationToken cancellationToken);

    /// <summary>
    /// Deletes queued Heartbeat/JobRunPatch events older than maxAge, logging each
    /// at Warning. JobRunComplete/BackupRecordUpsert events are never touched by
    /// this method regardless of age -- see class doc comment.
    /// </summary>
    Task<int> EvictExpiredAsync(TimeSpan maxAge, CancellationToken cancellationToken);
}
