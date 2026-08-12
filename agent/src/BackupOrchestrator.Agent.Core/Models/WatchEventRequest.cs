using System.Text.Json.Serialization;

namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Mirrors the body of POST /api/backup-jobs/{backup_job_id}/watch-events.
/// Today the only EventType is "FILE_LOCK_TIMEOUT" -- see
/// WatchHostedService's per-file readiness-detection loop (timeout branch).
/// </summary>
public sealed class WatchEventRequest
{
    /// <summary>
    /// Used ONLY to build the URL path segment in
    /// HttpBackendApiClient.ReportWatchEventAsync -- [JsonIgnore] is
    /// load-bearing here, not decorative: the backend's WatchEventRequest
    /// Pydantic schema (app/schemas/watch_event.py) has no backup_job_id
    /// field and uses extra="forbid", so serializing this into the JSON
    /// body would make every single watch-event call fail with a 422.
    /// </summary>
    [JsonIgnore]
    public required int BackupJobId { get; init; }

    /// <summary>"FILE_LOCK_TIMEOUT" today.</summary>
    public required string EventType { get; init; }

    public required bool Active { get; init; }
    public required string FilePath { get; init; }
    public string? Detail { get; init; }
}
