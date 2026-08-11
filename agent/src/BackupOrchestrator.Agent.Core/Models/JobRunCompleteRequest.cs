namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Mirrors app/schemas/job_run.py::JobRunCompleteRequest. Status must be one
/// of the terminal JobRunStatus values (SUCCESS, WARNING, FAILED, CANCELLED,
/// TIMEOUT) -- see POST /api/job-runs/{id}/complete.
/// </summary>
public sealed class JobRunCompleteRequest
{
    public required JobRunStatus Status { get; init; }
    public DateTimeOffset? FinishedAt { get; init; }
    public string? FilePath { get; init; }
    public long? FileSizeBytes { get; init; }
    public string? VerificationStatus { get; init; }
    public string? VerificationDetails { get; init; }
    public string? ErrorMessage { get; init; }
    public string? LogOutput { get; init; }
}

/// <summary>
/// Mirrors app/schemas/job_run.py::JobRunCreate, used for POST /api/job-runs
/// to open a new run before a transfer starts. Not explicitly named in the
/// original spec's Models/ list, but required to implement the run pipeline
/// end-to-end (create -> patch progress -> complete).
/// </summary>
public sealed class JobRunCreateRequest
{
    public required int BackupJobId { get; init; }
    public string TriggeredBy { get; init; } = "scheduler";
}
