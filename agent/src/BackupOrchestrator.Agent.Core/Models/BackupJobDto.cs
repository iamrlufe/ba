namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Mirrors app/schemas/backup_job.py::BackupJobRead field-for-field (snake_case
/// wire names, mapped via JsonPropertyName in the Worker's JSON options -- see
/// HttpBackendApiClient). Returned from GET /api/agents/{server_id}/jobs.
///
/// There is deliberately NO destination/remote-path field here -- the backend
/// BackupJob model has none. The agent decides the remote path itself; see
/// RemotePathBuilder.
/// </summary>
public sealed class BackupJobDto
{
    public required int Id { get; init; }
    public required int ServerId { get; init; }
    public required int DiskId { get; init; }
    public int? SqlInstanceId { get; init; }
    public required bool IsEnabled { get; init; }
    public required string Name { get; init; }
    public string? DatabaseName { get; init; }

    /// <summary>Required iff TriggerMode == "SCHEDULE"; null for WATCH.</summary>
    public string? SourcePath { get; init; }

    public required string BackupType { get; init; }

    /// <summary>Required iff TriggerMode == "SCHEDULE"; null for WATCH.</summary>
    public string? ScheduleCron { get; init; }

    public required string Timezone { get; init; }
    public required int RetentionDays { get; init; }
    public required int RetentionMinCopies { get; init; }
    public string? VerificationMethod { get; init; }

    /// <summary>"SCHEDULE" | "WATCH". Plain string, mirroring the existing BackupType convention.</summary>
    public required string TriggerMode { get; init; }

    /// <summary>Required iff TriggerMode == "WATCH"; a directory, not a file, unlike SourcePath.</summary>
    public string? WatchDirectory { get; init; }

    public int? CopyWindowStartHour { get; init; }
    public int? CopyWindowEndHour { get; init; }
    public required bool CopyWindowWeekendUnrestricted { get; init; }

    /// <summary>
    /// Non-null iff an operator has requested a manual fire that hasn't yet
    /// been claimed by any agent process. Only ever populated for
    /// TriggerMode == "SCHEDULE" jobs -- manual triggering is
    /// backend-forbidden (409) for WATCH-mode jobs, so this is never expected
    /// to be set on a WATCH job.
    /// </summary>
    public int? PendingManualRunId { get; init; }

    /// <summary>
    /// Non-null iff an operator has requested cancellation of the run with
    /// this id. Checked by BackupRunPipeline against the currently in-flight
    /// run's id at a few cooperative-cancellation points (copy-window wait,
    /// in-flight transfer poll).
    /// </summary>
    public int? CancelRequestedRunId { get; init; }

    public string? SqlInstanceHost { get; init; }
    public int? SqlInstancePort { get; init; }
    public string? SqlInstanceInstanceName { get; init; }
    public bool? SqlInstanceUseWindowsAuth { get; init; }

    /// <summary>
    /// Minutes, NOT seconds -- this is the watchdog timeout source. When
    /// null, the agent falls back to AgentOptions.DefaultJobTimeoutMinutes
    /// (see DECISIONS #3 in the spec: default 120).
    /// </summary>
    public int? ExpectedMaxDurationMinutes { get; init; }

    public required int MissedRunGraceMinutes { get; init; }
    public DateTimeOffset? LastRunAt { get; init; }
    public DateTimeOffset? NextRunAt { get; init; }
    public required DateTimeOffset CreatedAt { get; init; }
    public required DateTimeOffset UpdatedAt { get; init; }
}
