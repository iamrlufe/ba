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

    /// <summary>Polling cadence for GET /api/agents/{server_id}/monitoring-config.
    /// Deliberately much longer than JobPollIntervalSeconds -- this config
    /// changes only on rare manual admin edits.</summary>
    public int MonitoringConfigPollIntervalSeconds { get; set; } = 300;

    /// <summary>Sub-interval cadence for CpuSamplingHostedService's CPU-delta
    /// ticks -- see ICpuUsageSampler. Should be well under HeartbeatIntervalSeconds
    /// so several ticks accumulate per heartbeat window.</summary>
    public int CpuSamplingIntervalSeconds { get; set; } = 5;

    /// <summary>How often WatchHostedService rescans each WATCH job's directory
    /// contents against the ledger (defense-in-depth backstop for missed
    /// FileSystemWatcher events / agent downtime). Always runs once at startup too.</summary>
    public int WatchReconciliationIntervalSeconds { get; set; } = 300; // 5 min

    /// <summary>Retry cadence for the exclusive-open lock-check readiness probe.</summary>
    public int FileLockCheckIntervalSeconds { get; set; } = 15;

    /// <summary>Fallback lock-check timeout when the job's own ExpectedMaxDurationMinutes
    /// is null (if set, that value is used instead -- see MsdbBackupFinishDetector/
    /// ExclusiveOpenFileLockChecker usage in WatchHostedService).</summary>
    public int FileLockCheckTimeoutMinutes { get; set; } = 45;

    /// <summary>TCP-connect bound for the msdb SqlConnection.</summary>
    public int MsdbConnectTimeoutSeconds { get; set; } = 5;

    /// <summary>Command-execution bound for the msdb query itself.</summary>
    public int MsdbCommandTimeoutSeconds { get; set; } = 10;

    /// <summary>Cap on automatic re-offer-after-transfer-failure attempts for a single
    /// WATCH-detected file before the agent gives up on that specific file (only a
    /// newer file can supersede it after this).</summary>
    public int MaxWatchTransferAttempts { get; set; } = 5;

    /// <summary>Number of offline-queue events replayed back-to-back before pausing
    /// (DECISIONS: replay-storm mitigation). Breaks a large backlog into bursts
    /// small enough that a reconnect after a long outage doesn't fire hundreds of
    /// requests in one uninterrupted burst.</summary>
    public int OfflineReplayBatchSize { get; set; } = 20;

    /// <summary>Pause between batches within a single replay pass. Deliberately
    /// NOT a per-item delay -- see OfflineReplayHostedService.</summary>
    public int OfflineReplayBatchPauseSeconds { get; set; } = 3;

    /// <summary>Multiplier applied to the base 30s replay-pass cadence for every
    /// consecutive pass that stopped early on a BackendUnavailableException.
    /// Distinct from OfflineReplayBatchPauseSeconds (fixed, within-pass) and from
    /// RetryPolicyFactory (per-HTTP-call, within-request).</summary>
    public double OfflineReplayBackoffMultiplier { get; set; } = 2.0;

    /// <summary>Ceiling on the escalated inter-pass delay -- never wait longer
    /// than this between replay passes even after many consecutive failures.</summary>
    public int OfflineReplayMaxBackoffSeconds { get; set; } = 300; // 5 min
}
