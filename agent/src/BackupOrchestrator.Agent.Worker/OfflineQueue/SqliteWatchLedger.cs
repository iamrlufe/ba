using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.OfflineQueue;

/// <summary>
/// SQLite-backed IWatchLedger. Adds a new watch_ledger table to the SAME
/// offline_queue.db file used by SqliteOfflineEventQueue -- mirrors that
/// class's exact per-call-connection / CREATE TABLE IF NOT EXISTS pattern.
///
///   CREATE TABLE IF NOT EXISTS watch_ledger (
///     id INTEGER PRIMARY KEY AUTOINCREMENT,
///     backup_job_id INTEGER NOT NULL,
///     file_path TEXT NOT NULL,
///     file_size_bytes INTEGER NULL,
///     ordering_timestamp_utc TEXT NULL,
///     detection_method TEXT NULL,
///     readiness_state TEXT NOT NULL,
///     first_seen_at_utc TEXT NOT NULL,
///     last_checked_at_utc TEXT NOT NULL,
///     transferred_at_utc TEXT NULL,
///     remote_path TEXT NULL,
///     transfer_attempt_count INTEGER NOT NULL DEFAULT 0,
///     lock_timeout_alert_active INTEGER NOT NULL DEFAULT 0,
///     UNIQUE (backup_job_id, file_path)
///   );
/// </summary>
public sealed class SqliteWatchLedger : IWatchLedger
{
    private readonly string _connectionString;
    private readonly ILogger<SqliteWatchLedger> _logger;
    private readonly SemaphoreSlim _initLock = new(1, 1);
    private bool _initialized;

    public SqliteWatchLedger(IOptions<AgentOptions> options, ILogger<SqliteWatchLedger> logger)
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
                    await using var createTableCmd = connection.CreateCommand();
                    createTableCmd.CommandText = """
                        CREATE TABLE IF NOT EXISTS watch_ledger (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            backup_job_id INTEGER NOT NULL,
                            file_path TEXT NOT NULL,
                            file_size_bytes INTEGER NULL,
                            ordering_timestamp_utc TEXT NULL,
                            detection_method TEXT NULL,
                            readiness_state TEXT NOT NULL,
                            first_seen_at_utc TEXT NOT NULL,
                            last_checked_at_utc TEXT NOT NULL,
                            transferred_at_utc TEXT NULL,
                            remote_path TEXT NULL,
                            transfer_attempt_count INTEGER NOT NULL DEFAULT 0,
                            lock_timeout_alert_active INTEGER NOT NULL DEFAULT 0,
                            UNIQUE (backup_job_id, file_path)
                        );
                        """;
                    await createTableCmd.ExecuteNonQueryAsync(cancellationToken);

                    await using var createIndexCmd = connection.CreateCommand();
                    createIndexCmd.CommandText =
                        "CREATE INDEX IF NOT EXISTS ix_watch_ledger_job_state ON watch_ledger (backup_job_id, readiness_state);";
                    await createIndexCmd.ExecuteNonQueryAsync(cancellationToken);

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

    public async Task<bool> IsKnownAsync(int backupJobId, string filePath, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = "SELECT 1 FROM watch_ledger WHERE backup_job_id = $jobId AND file_path = $filePath LIMIT 1;";
        cmd.Parameters.AddWithValue("$jobId", backupJobId);
        cmd.Parameters.AddWithValue("$filePath", filePath);
        var result = await cmd.ExecuteScalarAsync(ct);
        return result is not null;
    }

    public async Task InsertNotReadyAsync(int backupJobId, string filePath, long? fileSizeBytes, DateTimeOffset nowUtc, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            INSERT INTO watch_ledger
                (backup_job_id, file_path, file_size_bytes, readiness_state, first_seen_at_utc, last_checked_at_utc)
            VALUES
                ($jobId, $filePath, $fileSizeBytes, 'NOT_READY', $now, $now)
            ON CONFLICT (backup_job_id, file_path) DO NOTHING;
            """;
        cmd.Parameters.AddWithValue("$jobId", backupJobId);
        cmd.Parameters.AddWithValue("$filePath", filePath);
        cmd.Parameters.AddWithValue("$fileSizeBytes", (object?)fileSizeBytes ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$now", nowUtc.ToString("O"));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task MarkReadyAsync(int backupJobId, string filePath, DateTimeOffset orderingTimestampUtc, string detectionMethod, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            UPDATE watch_ledger
            SET readiness_state = 'READY',
                ordering_timestamp_utc = $orderingTimestamp,
                detection_method = $detectionMethod,
                last_checked_at_utc = $now
            WHERE backup_job_id = $jobId AND file_path = $filePath;
            """;
        cmd.Parameters.AddWithValue("$jobId", backupJobId);
        cmd.Parameters.AddWithValue("$filePath", filePath);
        cmd.Parameters.AddWithValue("$orderingTimestamp", orderingTimestampUtc.ToString("O"));
        cmd.Parameters.AddWithValue("$detectionMethod", detectionMethod);
        cmd.Parameters.AddWithValue("$now", DateTimeOffset.UtcNow.ToString("O"));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public Task MarkSupersededAsync(int backupJobId, string filePath, CancellationToken ct) =>
        SetTerminalStateAsync(backupJobId, filePath, "SUPERSEDED", ct);

    public Task MarkVanishedAsync(int backupJobId, string filePath, CancellationToken ct) =>
        SetTerminalStateAsync(backupJobId, filePath, "VANISHED", ct);

    public Task MarkFailedPermanentAsync(int backupJobId, string filePath, CancellationToken ct) =>
        SetTerminalStateAsync(backupJobId, filePath, "FAILED_PERMANENT", ct);

    private async Task SetTerminalStateAsync(int backupJobId, string filePath, string state, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            UPDATE watch_ledger
            SET readiness_state = $state, last_checked_at_utc = $now
            WHERE backup_job_id = $jobId AND file_path = $filePath;
            """;
        cmd.Parameters.AddWithValue("$jobId", backupJobId);
        cmd.Parameters.AddWithValue("$filePath", filePath);
        cmd.Parameters.AddWithValue("$state", state);
        cmd.Parameters.AddWithValue("$now", DateTimeOffset.UtcNow.ToString("O"));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task MarkTransferredAsync(int backupJobId, string filePath, DateTimeOffset transferredAtUtc, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            UPDATE watch_ledger
            SET readiness_state = 'TRANSFERRED', transferred_at_utc = $transferredAt, last_checked_at_utc = $transferredAt
            WHERE backup_job_id = $jobId AND file_path = $filePath;
            """;
        cmd.Parameters.AddWithValue("$jobId", backupJobId);
        cmd.Parameters.AddWithValue("$filePath", filePath);
        cmd.Parameters.AddWithValue("$transferredAt", transferredAtUtc.ToString("O"));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<int> IncrementAttemptCountAsync(int backupJobId, string filePath, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            UPDATE watch_ledger
            SET transfer_attempt_count = transfer_attempt_count + 1, last_checked_at_utc = $now
            WHERE backup_job_id = $jobId AND file_path = $filePath
            RETURNING transfer_attempt_count;
            """;
        cmd.Parameters.AddWithValue("$jobId", backupJobId);
        cmd.Parameters.AddWithValue("$filePath", filePath);
        cmd.Parameters.AddWithValue("$now", DateTimeOffset.UtcNow.ToString("O"));
        var result = await cmd.ExecuteScalarAsync(ct);

        if (result is null or DBNull)
        {
            _logger.LogWarning(
                "IncrementAttemptCountAsync found no ledger row for job {JobId} / {FilePath}; returning 1", backupJobId, filePath);
            return 1;
        }

        return Convert.ToInt32(result);
    }

    public async Task<bool> TrySetLockTimeoutAlertActiveAsync(int backupJobId, string filePath, bool active, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        // Only actually updates (and thus reports "changed") when the flag's
        // current value differs from the requested one -- this is what makes
        // the dedup work: a caller invoking this repeatedly with the same
        // `active` value gets `false` back every time after the first.
        cmd.CommandText = """
            UPDATE watch_ledger
            SET lock_timeout_alert_active = $active, last_checked_at_utc = $now
            WHERE backup_job_id = $jobId AND file_path = $filePath
              AND lock_timeout_alert_active != $active;
            """;
        cmd.Parameters.AddWithValue("$jobId", backupJobId);
        cmd.Parameters.AddWithValue("$filePath", filePath);
        cmd.Parameters.AddWithValue("$active", active ? 1 : 0);
        cmd.Parameters.AddWithValue("$now", DateTimeOffset.UtcNow.ToString("O"));
        var rowsAffected = await cmd.ExecuteNonQueryAsync(ct);
        return rowsAffected > 0;
    }

    public async Task<IReadOnlyList<string>> GetKnownFilePathsAsync(int backupJobId, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = "SELECT file_path FROM watch_ledger WHERE backup_job_id = $jobId;";
        cmd.Parameters.AddWithValue("$jobId", backupJobId);

        var results = new List<string>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            results.Add(reader.GetString(0));
        }

        return results;
    }

    public async Task<IReadOnlyList<WatchLedgerEntry>> GetNotReadyEntriesAsync(int backupJobId, CancellationToken ct)
    {
        await using var connection = await OpenConnectionAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            SELECT id, backup_job_id, file_path, file_size_bytes, ordering_timestamp_utc,
                   detection_method, readiness_state, first_seen_at_utc, last_checked_at_utc,
                   transferred_at_utc, remote_path, transfer_attempt_count, lock_timeout_alert_active
            FROM watch_ledger
            WHERE backup_job_id = $jobId AND readiness_state = 'NOT_READY';
            """;
        cmd.Parameters.AddWithValue("$jobId", backupJobId);

        var results = new List<WatchLedgerEntry>();
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            results.Add(new WatchLedgerEntry
            {
                Id = reader.GetInt64(0),
                BackupJobId = reader.GetInt32(1),
                FilePath = reader.GetString(2),
                FileSizeBytes = reader.IsDBNull(3) ? null : reader.GetInt64(3),
                OrderingTimestampUtc = reader.IsDBNull(4) ? null : DateTimeOffset.Parse(reader.GetString(4)),
                DetectionMethod = reader.IsDBNull(5) ? null : reader.GetString(5),
                ReadinessState = Enum.Parse<WatchReadinessState>(reader.GetString(6)),
                FirstSeenAtUtc = DateTimeOffset.Parse(reader.GetString(7)),
                LastCheckedAtUtc = DateTimeOffset.Parse(reader.GetString(8)),
                TransferredAtUtc = reader.IsDBNull(9) ? null : DateTimeOffset.Parse(reader.GetString(9)),
                RemotePath = reader.IsDBNull(10) ? null : reader.GetString(10),
                TransferAttemptCount = reader.GetInt32(11),
                LockTimeoutAlertActive = reader.GetInt32(12) != 0,
            });
        }

        return results;
    }
}
