namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Mirrors app/schemas/job_run.py::JobRunUpdate, used for PATCH /api/job-runs/{id}.
///
/// Design note: the backend uses Pydantic's exclude_unset=True semantics --
/// a field absent from the JSON body means "leave untouched", not "set to
/// null". This agent never needs to explicitly null out a JobRun field via
/// PATCH (only ever sets status/started_at, or percent/current_file/bytes_done
/// progress fields), so HttpBackendApiClient serializes this DTO with
/// JsonIgnoreCondition.WhenWritingNull -- any property left null here is
/// simply omitted from the request body, which is equivalent to "unset" for
/// every use this agent makes of it.
/// </summary>
public sealed class JobRunPatch
{
    public JobRunStatus? Status { get; init; }
    public DateTimeOffset? StartedAt { get; init; }
    public DateTimeOffset? FinishedAt { get; init; }
    public string? FilePath { get; init; }
    public long? FileSizeBytes { get; init; }
    public string? VerificationStatus { get; init; }
    public string? VerificationDetails { get; init; }
    public string? ErrorMessage { get; init; }
    public string? LogOutput { get; init; }
    public int? Percent { get; init; }
    public string? CurrentFile { get; init; }
    public long? BytesDone { get; init; }
}
