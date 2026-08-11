namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Discriminates the kind of event held in the SQLite offline queue. See
/// DECISIONS #2 (offline queue format) and #4 (age-eviction rules) in the
/// spec: Heartbeat and JobRunPatch are safe to lose/age-evict; JobRunComplete
/// and BackupRecordUpsert must never be silently dropped.
/// </summary>
public enum QueuedEventType
{
    Heartbeat,
    JobRunPatch,
    JobRunComplete,
    BackupRecordUpsert,
}

/// <summary>
/// One row of the offline_queue.db SQLite table (see IOfflineEventQueue /
/// SqliteOfflineEventQueue). Id is 0/unset until persisted (auto-increment
/// assigned by SQLite on insert). PayloadJson is the serialized request body
/// (HeartbeatRequest / JobRunPatch / JobRunCompleteRequest / BackupRecordCreateRequest)
/// exactly as it would have been sent live.
/// </summary>
public sealed class QueuedEvent
{
    public long Id { get; init; }
    public required QueuedEventType EventType { get; init; }
    public required string PayloadJson { get; init; }
    public required DateTimeOffset EnqueuedAt { get; init; }

    /// <summary>
    /// For JobRunPatch/JobRunComplete events, the job_run_id the payload
    /// targets -- used only for logging context on replay, never for
    /// routing (the payload itself is replayed verbatim to whichever
    /// endpoint EventType implies).
    /// </summary>
    public int? JobRunId { get; init; }
}
