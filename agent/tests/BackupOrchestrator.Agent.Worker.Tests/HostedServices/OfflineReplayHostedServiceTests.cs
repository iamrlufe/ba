using System.Text.Json;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Worker;
using BackupOrchestrator.Agent.Worker.HostedServices;
using BackupOrchestrator.Agent.Worker.Tests.Support;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;

namespace BackupOrchestrator.Agent.Worker.Tests.HostedServices;

/// <summary>
/// Exercises OfflineReplayHostedService.ReplayOnceAsync directly (internal,
/// I/O-orchestration-free-of-timer method -- same convention as
/// SchedulerHostedServiceTests.TickOnce / WatchHostedServiceTests.TickOnceAsync)
/// against mocked IOfflineEventQueue/IBackendApiClient and the recording
/// IOfflineReplayPacer fake. Never the real ExecuteAsync timer loop, never a
/// real HTTP call or SQLite file -- OfflineReplayBackoffCalculator (the
/// inter-PASS escalation) is covered separately/purely in
/// OfflineReplayBackoffCalculatorTests; this file covers only the
/// within-a-single-pass batching/pause/fail-fast/cancellation behavior.
/// </summary>
public sealed class OfflineReplayHostedServiceTests
{
    private static AgentOptions Options(int batchSize = 20, int batchPauseSeconds = 3) => new()
    {
        ServerId = 1,
        AgentKey = "agent-key",
        ConnectionConfigKey = "connection-config-key",
        BackendBaseUrl = "https://backend.example.com",
        OfflineQueueDirectory = "/var/lib/agent/queue",
        OfflineReplayBatchSize = batchSize,
        OfflineReplayBatchPauseSeconds = batchPauseSeconds,
    };

    private sealed class Harness
    {
        public Mock<IOfflineEventQueue> OfflineQueue { get; } = new();
        public Mock<IBackendApiClient> BackendApiClient { get; } = new();
        public RecordingOfflineReplayPacer Pacer { get; } = new();
        public List<long> DeletedIdsInOrder { get; } = [];

        public Harness()
        {
            OfflineQueue
                .Setup(q => q.EvictExpiredAsync(It.IsAny<TimeSpan>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(0);
            OfflineQueue
                .Setup(q => q.DeleteAsync(It.IsAny<long>(), It.IsAny<CancellationToken>()))
                .Callback<long, CancellationToken>((id, _) => DeletedIdsInOrder.Add(id))
                .Returns(Task.CompletedTask);
        }

        public OfflineReplayHostedService BuildService(AgentOptions options) => new(
            OfflineQueue.Object,
            BackendApiClient.Object,
            Pacer,
            Microsoft.Extensions.Options.Options.Create(options),
            NullLogger<OfflineReplayHostedService>.Instance);

        /// <summary>Unconditional success for all four IBackendApiClient methods
        /// TryReplayAsync's switch can dispatch to.</summary>
        public void SetupAllBackendCallsSucceed()
        {
            BackendApiClient
                .Setup(b => b.SendHeartbeatAsync(It.IsAny<HeartbeatRequest>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(new HeartbeatResult { Success = true });
            BackendApiClient
                .Setup(b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(JobRunUpdateOutcome.Success);
            BackendApiClient
                .Setup(b => b.CompleteJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(JobRunUpdateOutcome.Success);
            BackendApiClient
                .Setup(b => b.CreateBackupRecordAsync(It.IsAny<BackupRecordCreateRequest>(), It.IsAny<CancellationToken>()))
                .Returns(Task.CompletedTask);
        }
    }

    /// <summary>
    /// Deterministic id -> EventType assignment used to build a mixed-type
    /// backlog whose payloads all deserialize cleanly via TryReplayAsync's
    /// switch. JobRunPatch/JobRunComplete events carry JobRunId == their own
    /// id, so tests can target a specific queued event by the jobRunId
    /// argument the mocked backend call receives.
    /// </summary>
    private static QueuedEventType EventTypeFor(long id) => (QueuedEventType)(id % 4);

    private static string PayloadFor(QueuedEventType type, long id) => type switch
    {
        QueuedEventType.Heartbeat => JsonSerializer.Serialize(
            new HeartbeatRequest { Reachable = true }, AgentJsonOptions.Default),
        QueuedEventType.JobRunPatch => JsonSerializer.Serialize(
            new JobRunPatch { Status = JobRunStatus.RUNNING, Percent = 50 }, AgentJsonOptions.Default),
        QueuedEventType.JobRunComplete => JsonSerializer.Serialize(
            new JobRunCompleteRequest { Status = JobRunStatus.SUCCESS }, AgentJsonOptions.Default),
        QueuedEventType.BackupRecordUpsert => JsonSerializer.Serialize(
            new BackupRecordCreateRequest
            {
                BackupJobId = 1,
                FileName = $"backup-{id}.bak",
                RemotePath = $"/remote/backup-{id}.bak",
                FileSizeBytes = 1000,
            },
            AgentJsonOptions.Default),
        _ => throw new ArgumentOutOfRangeException(nameof(type)),
    };

    private static QueuedEvent Event(long id)
    {
        var type = EventTypeFor(id);
        return new QueuedEvent
        {
            Id = id,
            EventType = type,
            PayloadJson = PayloadFor(type, id),
            EnqueuedAt = DateTimeOffset.UtcNow,
            JobRunId = type is QueuedEventType.JobRunPatch or QueuedEventType.JobRunComplete ? (int)id : null,
        };
    }

    private static List<QueuedEvent> Events(int count, long startId = 1) =>
        Enumerable.Range(0, count).Select(i => Event(startId + i)).ToList();

    [Fact]
    public async Task ReplayOnceAsync_LargeBacklog_RepliesInBatchesWithFixedPauseBetweenBatches()
    {
        var h = new Harness();
        var events = Events(899);
        h.OfflineQueue.Setup(q => q.GetPendingAsync(It.IsAny<CancellationToken>())).ReturnsAsync(events);
        h.SetupAllBackendCallsSucceed();

        var service = h.BuildService(Options(batchSize: 20, batchPauseSeconds: 3));

        var result = await service.ReplayOnceAsync(CancellationToken.None);

        Assert.True(result);
        Assert.Equal(Enumerable.Range(1, 899).Select(i => (long)i), h.DeletedIdsInOrder);

        foreach (var e in events)
        {
            h.OfflineQueue.Verify(q => q.DeleteAsync(e.Id, It.IsAny<CancellationToken>()), Times.Once);
        }

        // ceil(899 / 20) = 45 batches -> 44 inter-batch pauses.
        Assert.Equal(44, h.Pacer.Requested.Count);
        Assert.All(h.Pacer.Requested, d => Assert.Equal(TimeSpan.FromSeconds(3), d));
    }

    [Fact]
    public async Task ReplayOnceAsync_FailureMidBatch_StopsPassImmediately_PreservesFifoAndSkipsRemainingBatches()
    {
        var h = new Harness();
        const int failingId = 25; // 25 % 4 == 1 -> JobRunPatch, JobRunId == 25
        var events = Events(50);
        h.OfflineQueue.Setup(q => q.GetPendingAsync(It.IsAny<CancellationToken>())).ReturnsAsync(events);
        h.SetupAllBackendCallsSucceed();
        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(failingId, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new BackendUnavailableException("simulated backend outage"));

        var service = h.BuildService(Options(batchSize: 20, batchPauseSeconds: 3));

        var result = await service.ReplayOnceAsync(CancellationToken.None);

        Assert.False(result);

        // Events 1-24 replayed and deleted, in order.
        Assert.Equal(Enumerable.Range(1, 24).Select(i => (long)i), h.DeletedIdsInOrder);

        // Event 25's backend call was attempted (it's how the failure was
        // observed), but never deleted -- must remain queued for retry.
        h.BackendApiClient.Verify(
            b => b.PatchJobRunAsync(failingId, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Once);
        h.OfflineQueue.Verify(q => q.DeleteAsync(failingId, It.IsAny<CancellationToken>()), Times.Never);

        // Events 26-50 never attempted at all.
        for (var id = 26; id <= 50; id++)
        {
            var type = EventTypeFor(id);
            switch (type)
            {
                case QueuedEventType.Heartbeat:
                    // Heartbeat carries no id -- verified via aggregate count below instead.
                    break;
                case QueuedEventType.JobRunPatch:
                    h.BackendApiClient.Verify(
                        b => b.PatchJobRunAsync(id, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Never);
                    break;
                case QueuedEventType.JobRunComplete:
                    h.BackendApiClient.Verify(
                        b => b.CompleteJobRunAsync(id, It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()), Times.Never);
                    break;
                case QueuedEventType.BackupRecordUpsert:
                    // BackupRecordUpsert carries no id -- verified via aggregate count below instead.
                    break;
            }

            h.OfflineQueue.Verify(q => q.DeleteAsync(id, It.IsAny<CancellationToken>()), Times.Never);
        }

        // Aggregate counts: only events 1-24 (6 Heartbeat, 6 JobRunComplete,
        // 6 BackupRecordUpsert -- ids 1-24 split evenly 4 ways) plus the one
        // failing Patch attempt for id 25 should have reached the backend.
        h.BackendApiClient.Verify(
            b => b.SendHeartbeatAsync(It.IsAny<HeartbeatRequest>(), It.IsAny<CancellationToken>()), Times.Exactly(6));
        h.BackendApiClient.Verify(
            b => b.CompleteJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()), Times.Exactly(6));
        h.BackendApiClient.Verify(
            b => b.CreateBackupRecordAsync(It.IsAny<BackupRecordCreateRequest>(), It.IsAny<CancellationToken>()), Times.Exactly(6));
        h.BackendApiClient.Verify(
            b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Exactly(7)); // ids 1,5,9,13,17,21 succeed, 25 throws

        // Only 1 batch boundary reached (batch1 [1-20] -> batch2 [21-40]) before the
        // failure inside batch2 stopped the pass; batch2 -> batch3 pause never fires.
        Assert.Single(h.Pacer.Requested);
        Assert.Equal(TimeSpan.FromSeconds(3), h.Pacer.Requested[0]);
    }

    [Fact]
    public async Task ReplayOnceAsync_EmptyQueue_ReturnsTrue_NoBackendCallsNoPause()
    {
        var h = new Harness();
        h.OfflineQueue.Setup(q => q.GetPendingAsync(It.IsAny<CancellationToken>())).ReturnsAsync([]);

        var service = h.BuildService(Options());

        var result = await service.ReplayOnceAsync(CancellationToken.None);

        Assert.True(result);
        h.BackendApiClient.Verify(b => b.SendHeartbeatAsync(It.IsAny<HeartbeatRequest>(), It.IsAny<CancellationToken>()), Times.Never);
        h.BackendApiClient.Verify(b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Never);
        h.BackendApiClient.Verify(b => b.CompleteJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()), Times.Never);
        h.BackendApiClient.Verify(b => b.CreateBackupRecordAsync(It.IsAny<BackupRecordCreateRequest>(), It.IsAny<CancellationToken>()), Times.Never);
        Assert.Empty(h.Pacer.Requested);
    }

    [Fact]
    public async Task ReplayOnceAsync_BacklogSmallerThanOneBatch_NoPauseFires()
    {
        var h = new Harness();
        var events = Events(5);
        h.OfflineQueue.Setup(q => q.GetPendingAsync(It.IsAny<CancellationToken>())).ReturnsAsync(events);
        h.SetupAllBackendCallsSucceed();

        var service = h.BuildService(Options(batchSize: 20, batchPauseSeconds: 3));

        var result = await service.ReplayOnceAsync(CancellationToken.None);

        Assert.True(result);
        Assert.Equal(Enumerable.Range(1, 5).Select(i => (long)i), h.DeletedIdsInOrder);
        Assert.Empty(h.Pacer.Requested);
    }

    [Fact]
    public async Task ReplayOnceAsync_Cancelled_ReturnsFalseWithoutThrowing()
    {
        var h = new Harness();
        var events = Events(10);
        h.OfflineQueue.Setup(q => q.GetPendingAsync(It.IsAny<CancellationToken>())).ReturnsAsync(events);
        h.SetupAllBackendCallsSucceed();

        var cts = new CancellationTokenSource();

        // Event id=1 is JobRunPatch (1 % 4 == 1), JobRunId == 1 -- cancel
        // right after its backend call completes successfully, simulating a
        // shutdown request arriving mid-pass.
        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(1, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .Callback(() => cts.Cancel())
            .ReturnsAsync(JobRunUpdateOutcome.Success);

        var service = h.BuildService(Options(batchSize: 20, batchPauseSeconds: 3));

        var result = await service.ReplayOnceAsync(cts.Token);

        Assert.False(result);

        // Event 1 completed (replayed + deleted) before cancellation was observed.
        Assert.Equal([1L], h.DeletedIdsInOrder);

        // Nothing after the cancellation point was attempted: only 1 total
        // Patch call (event 1's own), and the other three event types (2, 3, 4)
        // never invoked at all.
        h.BackendApiClient.Verify(b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Once);
        h.BackendApiClient.Verify(b => b.CompleteJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()), Times.Never);
        h.BackendApiClient.Verify(b => b.CreateBackupRecordAsync(It.IsAny<BackupRecordCreateRequest>(), It.IsAny<CancellationToken>()), Times.Never);
        h.BackendApiClient.Verify(b => b.SendHeartbeatAsync(It.IsAny<HeartbeatRequest>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    /// <summary>
    /// Regression test for a fixed bug: cancellation surfacing as an actual
    /// thrown OperationCanceledException FROM WITHIN the awaited
    /// _pacer.PauseAsync(...) call (e.g. a real Task.Delay observing the
    /// token mid-wait), as opposed to IsCancellationRequested being checked
    /// and found true beforehand. Unlike
    /// ReplayOnceAsync_Cancelled_ReturnsFalseWithoutThrowing (which only
    /// exercises the `if (cancellationToken.IsCancellationRequested) return
    /// false;` loop-boundary check), this exercises the
    /// `catch (OperationCanceledException) when
    /// (cancellationToken.IsCancellationRequested) { return false; }` guard
    /// wrapped specifically around the inter-batch PauseAsync call. Before
    /// the fix, this exception propagated out of ReplayOnceAsync unhandled.
    /// </summary>
    [Fact]
    public async Task ReplayOnceAsync_CancelledDuringInterBatchPause_ReturnsFalseWithoutThrowing()
    {
        var h = new Harness();
        // batchSize 20 -> batch1 [1-20], batch2 [21-25]; exactly one
        // inter-batch pause is attempted (after batch1), which is where the
        // pacer is configured to throw.
        var events = Events(25);
        h.OfflineQueue.Setup(q => q.GetPendingAsync(It.IsAny<CancellationToken>())).ReturnsAsync(events);
        h.SetupAllBackendCallsSucceed();

        var cts = new CancellationTokenSource();
        h.Pacer.ThrowOnCallNumber = 1;
        h.Pacer.CancellationSourceToCancel = cts;

        var service = h.BuildService(Options(batchSize: 20, batchPauseSeconds: 3));

        var result = await service.ReplayOnceAsync(cts.Token);

        Assert.False(result);

        // Batch 1 (events 1-20) fully replayed and deleted, in order, before
        // the pause -- and its thrown cancellation -- was reached.
        Assert.Equal(Enumerable.Range(1, 20).Select(i => (long)i), h.DeletedIdsInOrder);

        // The single pause attempt threw rather than completing, so nothing
        // was ever recorded as a successful pause.
        Assert.Empty(h.Pacer.Requested);

        // Batch 2 (events 21-25) was never attempted at all -- the pause
        // failure stopped the pass before entering it.
        for (var id = 21; id <= 25; id++)
        {
            h.OfflineQueue.Verify(q => q.DeleteAsync(id, It.IsAny<CancellationToken>()), Times.Never);
        }
    }

    /// <summary>
    /// Regression test for a fixed bug: cancellation surfacing as an actual
    /// thrown OperationCanceledException FROM WITHIN an awaited
    /// IBackendApiClient call inside TryReplayAsync (mirroring how
    /// HttpBackendApiClient.ExecuteAsync behaves on caller-initiated
    /// cancellation), as opposed to IsCancellationRequested being checked
    /// beforehand. Exercises the
    /// `catch (OperationCanceledException) when
    /// (cancellationToken.IsCancellationRequested) { return false; }` guard
    /// wrapped around the `await TryReplayAsync(...)` call in the per-event
    /// loop. Same FIFO-stop shape as
    /// ReplayOnceAsync_FailureMidBatch_StopsPassImmediately_PreservesFifoAndSkipsRemainingBatches,
    /// but triggered by a raw OperationCanceledException instead of
    /// BackendUnavailableException. Before the fix, this exception
    /// propagated out of ReplayOnceAsync unhandled.
    /// </summary>
    [Fact]
    public async Task ReplayOnceAsync_CancelledMidFlightBackendCall_ReturnsFalseWithoutThrowing()
    {
        var h = new Harness();
        const int cancelId = 25; // 25 % 4 == 1 -> JobRunPatch, JobRunId == 25
        var events = Events(50);
        h.OfflineQueue.Setup(q => q.GetPendingAsync(It.IsAny<CancellationToken>())).ReturnsAsync(events);
        h.SetupAllBackendCallsSucceed();

        var cts = new CancellationTokenSource();
        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(cancelId, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .Callback(() => cts.Cancel())
            .ThrowsAsync(new OperationCanceledException("simulated cancellation mid backend call"));

        var service = h.BuildService(Options(batchSize: 20, batchPauseSeconds: 3));

        var result = await service.ReplayOnceAsync(cts.Token);

        Assert.False(result);

        // Events 1-24 replayed and deleted, in order, before the cancellation.
        Assert.Equal(Enumerable.Range(1, 24).Select(i => (long)i), h.DeletedIdsInOrder);

        // Event 25's backend call was attempted -- that's how the
        // cancellation surfaced -- but never deleted, so it remains queued
        // for retry on the next pass.
        h.BackendApiClient.Verify(
            b => b.PatchJobRunAsync(cancelId, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Once);
        h.OfflineQueue.Verify(q => q.DeleteAsync(cancelId, It.IsAny<CancellationToken>()), Times.Never);

        // Events 26-50 never attempted at all.
        for (var id = 26; id <= 50; id++)
        {
            h.OfflineQueue.Verify(q => q.DeleteAsync(id, It.IsAny<CancellationToken>()), Times.Never);
        }

        // ids 1,5,9,13,17,21 succeed normally; 25 is the one that throws.
        h.BackendApiClient.Verify(
            b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Exactly(7));

        // No inter-batch pause is reached -- the cancellation happens inside
        // batch2 (events 21-40), before batch2 completes.
        Assert.Single(h.Pacer.Requested);
    }
}
