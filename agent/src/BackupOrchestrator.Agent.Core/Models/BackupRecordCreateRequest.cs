namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>Mirrors app/schemas/backup_record.py::BackupRecordCreate.</summary>
public sealed class BackupRecordCreateRequest
{
    public required int BackupJobId { get; init; }
    public int? JobRunId { get; init; }
    public required string FileName { get; init; }
    public required string RemotePath { get; init; }
    public required long FileSizeBytes { get; init; }
    public string? Checksum { get; init; }
    public string? ChecksumAlgorithm { get; init; }
}
