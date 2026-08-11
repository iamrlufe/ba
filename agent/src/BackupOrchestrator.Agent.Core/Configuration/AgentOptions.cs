namespace BackupOrchestrator.Agent.Core.Configuration;

/// <summary>
/// Bound from appsettings.json's "Agent" section plus Agent__ prefixed
/// environment variable overrides (see Program.cs). Validated at startup
/// via AgentOptionsValidator + .ValidateOnStart().
///
/// SECRET FIELDS -- AgentKey and ConnectionConfigKey must NEVER be logged,
/// never included in ToString()/exception messages, never echoed back in
/// any diagnostic output. Every place in this codebase that touches
/// AgentOptions must be checked against that rule.
/// </summary>
public sealed class AgentOptions
{
    public const string SectionName = "Agent";

    /// <summary>Literally Server.id on the backend -- see app/schemas/agent.py.</summary>
    public int ServerId { get; set; }

    /// <summary>
    /// Sent as the X-Agent-Key header on heartbeat/jobs-poll/job-run calls.
    /// Checked by the backend via secrets.compare_digest against
    /// settings.AGENT_API_KEY. NEVER LOG THIS VALUE.
    /// </summary>
    public string AgentKey { get; set; } = string.Empty;

    /// <summary>
    /// Sent as the X-Connection-Config-Key header, ONLY for
    /// GET /api/agents/{server_id}/connection-config. A SEPARATE secret from
    /// AgentKey -- never the same value, never logged.
    ///
    /// CRITICAL, NOT PRODUCTION-SAFE YET: as of this writing the backend
    /// enforces this endpoint with a single GLOBAL (not per-agent) key --
    /// every agent in the fleet that knows this value can read every
    /// server's decrypted FTP/SFTP credentials. Per-server keys are planned
    /// as the very next backend task. Do not deploy this agent against
    /// production credentials until that ships.
    /// </summary>
    public string ConnectionConfigKey { get; set; } = string.Empty;

    /// <summary>Base URL of the FastAPI backend, e.g. https://backup-orchestrator.internal.</summary>
    public string BackendBaseUrl { get; set; } = string.Empty;

    public int HeartbeatIntervalSeconds { get; set; } = 60;

    public int JobPollIntervalSeconds { get; set; } = 30;

    /// <summary>
    /// Watchdog fallback when a BackupJob's expected_max_duration_minutes is
    /// null. See DECISIONS #3 in the spec.
    /// </summary>
    public int DefaultJobTimeoutMinutes { get; set; } = 120;

    /// <summary>Directory containing offline_queue.db (created if missing).</summary>
    public string OfflineQueueDirectory { get; set; } = string.Empty;

    /// <summary>
    /// Age-eviction cutoff for Heartbeat/JobRunPatch queued events (DECISIONS #4).
    /// JobRunComplete/BackupRecordUpsert events are NEVER age-evicted regardless
    /// of this value.
    /// </summary>
    public int OfflineQueueMaxAgeDays { get; set; } = 14;
}
