using System.Text.Json.Serialization;

namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Mirrors the body of POST /api/backup-jobs/{backup_job_id}/schedule-errors.
/// Active=true means "cannot parse this job's cron expression/timezone right
/// now" (severity CRITICAL on the backend side); Active=false is the one-shot
/// recovery report once the expression parses again.
/// </summary>
public sealed record ScheduleErrorRequest
{
    /// <summary>
    /// Used ONLY to build the URL path segment in
    /// HttpBackendApiClient.ReportScheduleErrorAsync -- [JsonIgnore] is
    /// load-bearing here, not decorative, exactly as for
    /// WatchEventRequest.BackupJobId: the backend's schedule-error Pydantic
    /// schema has no backup_job_id field and uses extra="forbid", so
    /// serializing this into the JSON body would make every call fail with a
    /// 422.
    /// </summary>
    [JsonIgnore]
    public required int BackupJobId { get; init; }

    public required bool Active { get; init; }

    public string? Detail { get; init; }
}
