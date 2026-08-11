using System.Text.Json;
using System.Text.Json.Serialization;

namespace BackupOrchestrator.Agent.Worker;

/// <summary>
/// Shared JSON options for both live HTTP calls (HttpBackendApiClient) and
/// offline-queue payload serialization (OfflineReplayHostedService /
/// hosted services enqueueing on failure) -- payloads enqueued while offline
/// must deserialize identically to what would have been sent live, so both
/// paths use the exact same options. snake_case matches the FastAPI/Pydantic
/// backend's wire format (e.g. "server_id", "schedule_cron").
/// </summary>
public static class AgentJsonOptions
{
    public static readonly JsonSerializerOptions Default = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
}
