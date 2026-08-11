namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>Mirrors app/schemas/job_run.py::JobRunRead.</summary>
public sealed class JobRunDto
{
    public required int Id { get; init; }
    public required int BackupJobId { get; init; }
    public required JobRunStatus Status { get; init; }
    public required string TriggeredBy { get; init; }
    public DateTimeOffset? StartedAt { get; init; }
    public DateTimeOffset? FinishedAt { get; init; }
    public string? FilePath { get; init; }
    public long? FileSizeBytes { get; init; }
    public int? DurationSeconds { get; init; }
    public string? VerificationStatus { get; init; }
    public string? VerificationDetails { get; init; }
    public string? ErrorMessage { get; init; }
    public int? Percent { get; init; }
    public string? CurrentFile { get; init; }
    public long? BytesDone { get; init; }
    public required DateTimeOffset CreatedAt { get; init; }
}
