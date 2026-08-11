using System.Text.Json;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.HostedServices;

/// <summary>
/// Replays the SQLite offline queue FIFO (oldest-first) whenever the backend
/// is reachable again. Stops at the first replay failure within a pass to
/// preserve strict ordering -- a later event must never be acknowledged
/// while an earlier one is still stuck (DECISIONS #2: real row DELETE on
/// success, never reordered/rewritten). Also runs age-eviction
/// (DECISIONS #4) once per pass before attempting any replay.
/// </summary>
public sealed class OfflineReplayHostedService : BackgroundService
{
    /// <summary>
    /// Not exposed via AgentOptions in the spec -- kept independent of
    /// HeartbeatIntervalSeconds/JobPollIntervalSeconds so replay cadence can
    /// be tuned separately if needed later.
    /// </summary>
    private static readonly TimeSpan ReplayInterval = TimeSpan.FromSeconds(30);

    private readonly IOfflineEventQueue _offlineQueue;
    private readonly IBackendApiClient _backendApiClient;
    private readonly AgentOptions _options;
    private readonly ILogger<OfflineReplayHostedService> _logger;

    public OfflineReplayHostedService(
        IOfflineEventQueue offlineQueue,
        IBackendApiClient backendApiClient,
        IOptions<AgentOptions> options,
        ILogger<OfflineReplayHostedService> logger)
    {
        _offlineQueue = offlineQueue;
        _backendApiClient = backendApiClient;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(ReplayInterval);

        do
        {
            await ReplayOnceAsync(stoppingToken);
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    internal async Task ReplayOnceAsync(CancellationToken cancellationToken)
    {
        await _offlineQueue.EvictExpiredAsync(TimeSpan.FromDays(_options.OfflineQueueMaxAgeDays), cancellationToken);

        var pending = await _offlineQueue.GetPendingAsync(cancellationToken);
        if (pending.Count == 0)
        {
            return;
        }

        _logger.LogInformation("Replaying {Count} queued offline events", pending.Count);

        foreach (var queuedEvent in pending)
        {
            if (cancellationToken.IsCancellationRequested)
            {
                return;
            }

            var replayed = await TryReplayAsync(queuedEvent, cancellationToken);
            if (!replayed)
            {
                // Preserve FIFO order: stop this pass rather than skip ahead
                // to a later event while an earlier one is still stuck.
                break;
            }
        }
    }

    private async Task<bool> TryReplayAsync(QueuedEvent queuedEvent, CancellationToken cancellationToken)
    {
        try
        {
            switch (queuedEvent.EventType)
            {
                case QueuedEventType.Heartbeat:
                    var heartbeat = Deserialize<HeartbeatRequest>(queuedEvent);
                    await _backendApiClient.SendHeartbeatAsync(heartbeat, cancellationToken);
                    break;

                case QueuedEventType.JobRunPatch:
                    var patch = Deserialize<JobRunPatch>(queuedEvent);
                    await _backendApiClient.PatchJobRunAsync(queuedEvent.JobRunId ?? 0, patch, cancellationToken);
                    break;

                case QueuedEventType.JobRunComplete:
                    var complete = Deserialize<JobRunCompleteRequest>(queuedEvent);
                    await _backendApiClient.CompleteJobRunAsync(queuedEvent.JobRunId ?? 0, complete, cancellationToken);
                    break;

                case QueuedEventType.BackupRecordUpsert:
                    var record = Deserialize<BackupRecordCreateRequest>(queuedEvent);
                    await _backendApiClient.CreateBackupRecordAsync(record, cancellationToken);
                    break;

                default:
                    _logger.LogError(
                        "Unknown queued event type {EventType} for id {Id}; dropping to avoid a permanently stuck queue",
                        queuedEvent.EventType, queuedEvent.Id);
                    break;
            }

            // Real row DELETE, per DECISIONS #2. Applies equally to a
            // successful send AND a 409 "already terminal" outcome on
            // JobRunPatch/JobRunComplete -- both mean this queued update no
            // longer needs to be (re)applied, so it's acknowledged/dropped
            // either way (see IBackendApiClient.JobRunUpdateOutcome).
            await _offlineQueue.DeleteAsync(queuedEvent.Id, cancellationToken);
            _logger.LogDebug("Replayed and dropped offline event {Id} ({EventType})", queuedEvent.Id, queuedEvent.EventType);
            return true;
        }
        catch (BackendUnavailableException ex)
        {
            _logger.LogWarning(
                ex, "Replay of offline event {Id} ({EventType}) failed; backend still unavailable, will retry next pass",
                queuedEvent.Id, queuedEvent.EventType);
            return false;
        }
    }

    private static T Deserialize<T>(QueuedEvent queuedEvent) =>
        JsonSerializer.Deserialize<T>(queuedEvent.PayloadJson, AgentJsonOptions.Default)
        ?? throw new InvalidOperationException(
            $"Queued event {queuedEvent.Id} ({queuedEvent.EventType}) has a payload that deserialized to null");
}
