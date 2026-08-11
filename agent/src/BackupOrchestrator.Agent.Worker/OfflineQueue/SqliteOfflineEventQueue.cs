using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.OfflineQueue;

/// <summary>
/// SQLite-backed IOfflineEventQueue (DECISIONS #2). One file, one table:
///
///   CREATE TABLE queued_events (
///     id INTEGER PRIMARY KEY AUTOINCREMENT,
///     event_type TEXT NOT NULL,
///     payload_json TEXT NOT NULL,
///     job_run_id INTEGER NULL,
///     enqueued_at TEXT NOT NULL
///   )
///
/// Replay is oldest-first (ORDER BY id ASC); a successful replay issues a
/// real `DELETE WHERE id = ...` -- the file is never truncated/rewritten
/// wholesale. Age-eviction (EvictExpiredAsync) only ever targets Heartbeat/
/// JobRunPatch rows -- JobRunComplete/BackupRecordUpsert rows are excluded
/// from the eviction WHERE clause entirely, so they cannot be silently
/// dropped no matter how old they get.
/// </summary>
public sealed class SqliteOfflineEventQueue : IOfflineEventQueue, IDisposable
{
    private readonly string _connectionString;
    private readonly ILogger<SqliteOfflineEventQueue> _logger;
    private readonly SemaphoreSlim _initLock = new(1, 1);
    private bool _initialized;

    public SqliteOfflineEventQueue(IOptions<AgentOptions> options, ILogger<SqliteOfflineEventQueue> logger)
    {
        _logger = logger;
        var directory = options.Value.OfflineQueueDirectory;
        Directory.CreateDirectory(directory);
        var dbPath = Path.Combine(directory, "offline_queue.db");
        _connectionString = new SqliteConnectionStringBuilder { DataSource = dbPath }.ToString();
    }

    private async Task<SqliteConnection> OpenConnectionAsync(CancellationToken cancellationToken)
    {
        var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync(cancellationToken);

        if (!_initialized)
        {
            await _initLock.WaitAsync(cancellationToken);
            try
            {
                if (!_initialized)
                {
                    await using var createCmd = connection.CreateCommand();
                    createCmd.CommandText = """
                        CREATE TABLE IF NOT EXISTS queued_events (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            event_type TEXT NOT NULL,
                            payload_json TEXT NOT NULL,
                            job_run_id INTEGER NULL,
                            enqueued_at TEXT NOT NULL
                        );
                        """;
                    await createCmd.ExecuteNonQueryAsync(cancellationToken);
                    _initialized = true;
                }
            }
            finally
            {
                _initLock.Release();
            }
        }

        return connection;
    }

    public async Task EnqueueAsync(
        QueuedEventType eventType, string payloadJson, int? jobRunId, CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            INSERT INTO queued_events (event_type, payload_json, job_run_id, enqueued_at)
            VALUES ($eventType, $payloadJson, $jobRunId, $enqueuedAt);
            """;
        cmd.Parameters.AddWithValue("$eventType", eventType.ToString());
        cmd.Parameters.AddWithValue("$payloadJson", payloadJson);
        cmd.Parameters.AddWithValue("$jobRunId", (object?)jobRunId ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$enqueuedAt", DateTimeOffset.UtcNow.ToString("O"));
        await cmd.ExecuteNonQueryAsync(cancellationToken);

        _logger.LogInformation("Enqueued offline event {EventType} (job_run_id={JobRunId})", eventType, jobRunId);
    }

    public async Task<IReadOnlyList<QueuedEvent>> GetPendingAsync(CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            SELECT id, event_type, payload_json, job_run_id, enqueued_at
            FROM queued_events
            ORDER BY id ASC;
            """;

        var results = new List<QueuedEvent>();
        await using var reader = await cmd.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            results.Add(new QueuedEvent
            {
                Id = reader.GetInt64(0),
                EventType = Enum.Parse<QueuedEventType>(reader.GetString(1)),
                PayloadJson = reader.GetString(2),
                JobRunId = reader.IsDBNull(3) ? null : reader.GetInt32(3),
                EnqueuedAt = DateTimeOffset.Parse(reader.GetString(4)),
            });
        }

        return results;
    }

    public async Task DeleteAsync(long id, CancellationToken cancellationToken)
    {
        await using var connection = await OpenConnectionAsync(cancellationToken);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = "DELETE FROM queued_events WHERE id = $id;";
        cmd.Parameters.AddWithValue("$id", id);
        await cmd.ExecuteNonQueryAsync(cancellationToken);
    }

    public async Task<int> EvictExpiredAsync(TimeSpan maxAge, CancellationToken cancellationToken)
    {
        var cutoff = DateTimeOffset.UtcNow - maxAge;

        await using var connection = await OpenConnectionAsync(cancellationToken);

        // Only Heartbeat/JobRunPatch rows are eligible -- JobRunComplete and
        // BackupRecordUpsert are excluded from the WHERE clause by name, not
        // just by convention, so a future new event_type is never silently
        // caught by this eviction unless someone deliberately adds it here.
        await using var selectCmd = connection.CreateCommand();
        selectCmd.CommandText = """
            SELECT id FROM queued_events
            WHERE event_type IN ($heartbeat, $jobRunPatch) AND enqueued_at < $cutoff;
            """;
        selectCmd.Parameters.AddWithValue("$heartbeat", QueuedEventType.Heartbeat.ToString());
        selectCmd.Parameters.AddWithValue("$jobRunPatch", QueuedEventType.JobRunPatch.ToString());
        selectCmd.Parameters.AddWithValue("$cutoff", cutoff.ToString("O"));

        var idsToEvict = new List<long>();
        await using (var reader = await selectCmd.ExecuteReaderAsync(cancellationToken))
        {
            while (await reader.ReadAsync(cancellationToken))
            {
                idsToEvict.Add(reader.GetInt64(0));
            }
        }

        if (idsToEvict.Count == 0)
        {
            return 0;
        }

        foreach (var id in idsToEvict)
        {
            await using var deleteCmd = connection.CreateCommand();
            deleteCmd.CommandText = "DELETE FROM queued_events WHERE id = $id;";
            deleteCmd.Parameters.AddWithValue("$id", id);
            await deleteCmd.ExecuteNonQueryAsync(cancellationToken);
        }

        _logger.LogWarning(
            "Age-evicted {Count} offline queue events older than {MaxAgeDays} days", idsToEvict.Count, maxAge.TotalDays);

        return idsToEvict.Count;
    }

    public void Dispose() => _initLock.Dispose();
}
