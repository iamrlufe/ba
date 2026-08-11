using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// The seam between business logic (scheduler, hosted services) and the
/// real backend. HttpBackendApiClient (Worker project) is the only
/// implementation that touches a live HttpClient; everything in Core depends
/// on this interface only, so it's mockable for unit tests.
///
/// Every method may throw BackendUnavailableException once the bounded Polly
/// retry policy is exhausted -- callers must catch that specifically and
/// fall back to offline-queue behavior (see OfflineReplayHostedService /
/// IOfflineEventQueue), never let it crash the hosted service loop.
///
/// 409 responses on PATCH/complete job-run calls mean "already terminal" --
/// modeled as JobRunUpdateOutcome.AlreadyTerminal rather than an exception,
/// per spec: "this is expected, not an error... do not queue for replay."
/// </summary>
public interface IBackendApiClient
{
    Task<HeartbeatResult> SendHeartbeatAsync(HeartbeatRequest request, CancellationToken cancellationToken);

    Task<JobsPage> GetJobsAsync(int serverId, int limit, int offset, CancellationToken cancellationToken);

    /// <summary>
    /// GET /api/agents/{server_id}/connection-config. Never throws for the
    /// documented non-200 outcomes (404/409/403/500) -- those are all
    /// modeled in ConnectionConfigResult.Outcome. Only throws
    /// BackendUnavailableException for genuine connectivity failure.
    /// </summary>
    Task<ConnectionConfigResult> GetConnectionConfigAsync(int serverId, CancellationToken cancellationToken);

    Task<JobRunDto> CreateJobRunAsync(JobRunCreateRequest request, CancellationToken cancellationToken);

    Task<JobRunUpdateOutcome> PatchJobRunAsync(int jobRunId, JobRunPatch patch, CancellationToken cancellationToken);

    Task<JobRunUpdateOutcome> CompleteJobRunAsync(
        int jobRunId, JobRunCompleteRequest request, CancellationToken cancellationToken);

    Task CreateBackupRecordAsync(BackupRecordCreateRequest request, CancellationToken cancellationToken);
}

public sealed class JobsPage
{
    public required IReadOnlyList<BackupJobDto> Items { get; init; }
    public required int Total { get; init; }
}

public enum ConnectionConfigOutcome
{
    Success,

    /// <summary>404 -- server_id doesn't exist.</summary>
    ServerNotFound,

    /// <summary>
    /// 409 -- server soft-deleted OR no credentials configured. Per spec the
    /// agent doesn't need to distinguish the two: both mean "can't transfer
    /// right now, log and back off".
    /// </summary>
    Unavailable,

    /// <summary>
    /// 403 -- server administratively DISABLED. Asymmetric vs. heartbeat
    /// (which still succeeds for a disabled server): this is a legitimate
    /// steady state, NOT an auth failure. Callers must stop attempting
    /// transfers for this server until re-enabled, but keep heartbeat/job-poll
    /// running normally.
    /// </summary>
    ServerDisabled,

    /// <summary>500 -- decryption failed server-side. Treat as transient; retry next scheduled attempt.</summary>
    DecryptionFailed,
}

public sealed class ConnectionConfigResult
{
    public required ConnectionConfigOutcome Outcome { get; init; }
    public ConnectionConfigDto? Config { get; init; }

    public static ConnectionConfigResult Success(ConnectionConfigDto config) =>
        new() { Outcome = ConnectionConfigOutcome.Success, Config = config };

    public static ConnectionConfigResult Failed(ConnectionConfigOutcome outcome) =>
        new() { Outcome = outcome };
}

/// <summary>
/// Outcome of a PATCH/complete call against a job run. AlreadyTerminal
/// (backend 409) means the run's terminal status was already set by a
/// concurrent request -- per spec: stop sending further patches, log at
/// Information, do not retry, do not queue for replay (acknowledge/drop it).
/// </summary>
public enum JobRunUpdateOutcome
{
    Success,
    AlreadyTerminal,
}
