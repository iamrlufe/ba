using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Worker.Pipeline;
using BackupOrchestrator.Agent.Worker.Tests.Support;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;

namespace BackupOrchestrator.Agent.Worker.Tests.Pipeline;

/// <summary>
/// Exercises BackupRunPipeline against mocked IBackendApiClient/
/// IBackupTransferClient/IOfflineEventQueue/IJobCache/IClock -- never a real
/// HTTP call or a real WinSCP session. BackupRunPipeline itself is a sealed
/// concrete class (not behind an interface), so these tests construct the
/// real pipeline wired to mocks at its actual dependency boundary, matching
/// this project's "mock at the interface seam" convention.
/// </summary>
public sealed class BackupRunPipelineTests
{
    private static AgentOptions Options(int defaultJobTimeoutMinutes = 120) => new()
    {
        ServerId = 1,
        AgentKey = "agent-key",
        ConnectionConfigKey = "connection-config-key",
        BackendBaseUrl = "https://backend.example.com",
        OfflineQueueDirectory = "/var/lib/agent/queue",
        DefaultJobTimeoutMinutes = defaultJobTimeoutMinutes,
    };

    private static ConnectionConfigDto Config() => new()
    {
        ServerId = 1,
        Host = "ftp.example.com",
        Port = 21,
        Protocol = "FTP",
        Username = "user",
        Password = "pass",
    };

    private static JobRunDto Run(int id, int backupJobId) => new()
    {
        Id = id,
        BackupJobId = backupJobId,
        Status = JobRunStatus.PENDING,
        TriggeredBy = "scheduler",
        CreatedAt = DateTimeOffset.UtcNow,
    };

    private sealed class Harness
    {
        public Mock<IBackendApiClient> BackendApiClient { get; } = new();
        public Mock<IBackupTransferClient> TransferClient { get; } = new();
        public Mock<IOfflineEventQueue> OfflineQueue { get; } = new();
        public Mock<IJobCache> JobCache { get; } = new();
        public TestClock Clock { get; } = new(DateTimeOffset.UtcNow);

        public BackupRunPipeline BuildPipeline(AgentOptions? options = null) => new(
            BackendApiClient.Object,
            TransferClient.Object,
            OfflineQueue.Object,
            JobCache.Object,
            Clock,
            Microsoft.Extensions.Options.Options.Create(options ?? Options()),
            NullLogger<BackupRunPipeline>.Instance);
    }

    // ------------------------------------------------------------------
    // RUNNING-transition-abort logic: PATCH to RUNNING returning
    // AlreadyTerminal (backend 409) must abort BEFORE the connection-config
    // fetch or any transfer attempt, and the run outcome must be Cancelled.
    // ------------------------------------------------------------------

    [Fact]
    public async Task RunClaimedAsync_RunningPatchAlreadyTerminal_AbortsBeforeConnectionConfigFetchAndTransfer()
    {
        var h = new Harness();
        var job = TestData.Job(id: 10);
        var run = Run(id: 555, backupJobId: job.Id);

        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(job); // enabled, no cancel requested, always-open window

        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(
                run.Id,
                It.Is<JobRunPatch>(p => p.Status == JobRunStatus.RUNNING),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.AlreadyTerminal);

        var pipeline = h.BuildPipeline();

        var outcome = await pipeline.RunClaimedAsync(job, run, _ => Task.FromResult<string?>(job.SourcePath), CancellationToken.None);

        Assert.Equal(BackupRunOutcome.Cancelled, outcome);
        h.BackendApiClient.Verify(b => b.GetConnectionConfigAsync(It.IsAny<int>(), It.IsAny<CancellationToken>()), Times.Never);
        h.TransferClient.Verify(
            t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()),
            Times.Never);
        // Only the one RUNNING-transition PATCH -- no completion/backup-record
        // calls, since the pipeline aborts before ever reaching CompleteAsync
        // for this path (AlreadyTerminal means the backend already owns the
        // terminal state; the pipeline must not try to set one itself).
        h.BackendApiClient.Verify(
            b => b.PatchJobRunAsync(run.Id, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Once);
        h.BackendApiClient.Verify(
            b => b.CompleteJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task RunClaimedAsync_RunningPatchSucceeds_ProceedsToConnectionConfigFetch()
    {
        var h = new Harness();
        var job = TestData.Job(id: 11);
        var run = Run(id: 556, backupJobId: job.Id);

        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(job);

        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(run.Id, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);

        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Failed(ConnectionConfigOutcome.ServerNotFound));

        var pipeline = h.BuildPipeline();

        var outcome = await pipeline.RunClaimedAsync(job, run, _ => Task.FromResult<string?>(job.SourcePath), CancellationToken.None);

        Assert.Equal(BackupRunOutcome.Failed, outcome);
        h.BackendApiClient.Verify(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()), Times.Once);
    }

    // ------------------------------------------------------------------
    // WaitForCopyWindowAsync's cancel-check branch: an operator cancel
    // observed while waiting for the copy window to open must complete the
    // run CANCELLED and return Cancelled, without ever attempting the
    // RUNNING transition or a transfer.
    // ------------------------------------------------------------------

    [Fact]
    public async Task RunClaimedAsync_CancelRequestedWhileWaitingForCopyWindow_CompletesCancelledAndSkipsRunningTransition()
    {
        var h = new Harness();
        var job = TestData.Job(id: 12);
        var run = Run(id: 557, backupJobId: job.Id);

        // Job snapshot already carries a CancelRequestedRunId matching this
        // run -- the very first WaitForCopyWindowAsync check must observe
        // this and bail out before ever checking the copy window itself.
        var cancelledJob = TestData.Job(id: job.Id, cancelRequestedRunId: run.Id);
        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(cancelledJob);

        h.BackendApiClient
            .Setup(b => b.CompleteJobRunAsync(run.Id, It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);

        var pipeline = h.BuildPipeline();

        var outcome = await pipeline.RunClaimedAsync(job, run, _ => Task.FromResult<string?>(job.SourcePath), CancellationToken.None);

        Assert.Equal(BackupRunOutcome.Cancelled, outcome);
        h.BackendApiClient.Verify(
            b => b.CompleteJobRunAsync(
                run.Id,
                It.Is<JobRunCompleteRequest>(r => r.Status == JobRunStatus.CANCELLED
                    && r.ErrorMessage == "Cancelled by operator while waiting for the copy window to open"),
                It.IsAny<CancellationToken>()),
            Times.Once);
        h.BackendApiClient.Verify(
            b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()), Times.Never);
        h.TransferClient.Verify(
            t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()),
            Times.Never);
    }

    [Fact]
    public async Task RunClaimedAsync_JobDisabledWhileWaitingForCopyWindow_CompletesCancelledWithDisabledMessage()
    {
        var h = new Harness();
        var job = TestData.Job(id: 13);
        var run = Run(id: 558, backupJobId: job.Id);

        var disabledJob = TestData.Job(id: job.Id, isEnabled: false);
        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(disabledJob);

        h.BackendApiClient
            .Setup(b => b.CompleteJobRunAsync(run.Id, It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);

        var pipeline = h.BuildPipeline();

        var outcome = await pipeline.RunClaimedAsync(job, run, _ => Task.FromResult<string?>(job.SourcePath), CancellationToken.None);

        Assert.Equal(BackupRunOutcome.Cancelled, outcome);
        h.BackendApiClient.Verify(
            b => b.CompleteJobRunAsync(
                run.Id,
                It.Is<JobRunCompleteRequest>(r => r.Status == JobRunStatus.CANCELLED
                    && r.ErrorMessage == "Backup job disabled or removed while waiting for the copy window to open"),
                It.IsAny<CancellationToken>()),
            Times.Once);
    }

    // ------------------------------------------------------------------
    // Transfer-cancellation reclassification: TIMEOUT -> CANCELLED only
    // when the dedicated operator-cancel source specifically fired (never
    // just because the watchdog/shutdown token cancelled the transfer).
    //
    // NOTE ON TEST SPEED: BackupRunPipeline.CancelPollInterval (the interval
    // on which the in-flight-transfer operator-cancel poll loop checks
    // IJobCache) is a hardcoded `private static readonly TimeSpan
    // TimeSpan.FromSeconds(10)` field using a raw Task.Delay -- NOT threaded
    // through IClock and not exposed via AgentOptions. There is therefore no
    // seam to fast-forward this wait; the test below genuinely waits out one
    // real ~10s poll tick rather than faking it via reflection on a private
    // static field (which would be brittle, not a real behavioral test).
    // It is slow but deterministic, not flaky.
    // ------------------------------------------------------------------

    [Fact(Timeout = 30000)]
    public async Task RunClaimedAsync_TransferCancelledSpecificallyByOperatorPoll_ReclassifiesTimeoutAsCancelled()
    {
        var h = new Harness();
        var job = TestData.Job(id: 14, expectedMaxDurationMinutes: 60); // watchdog far longer than the ~10s poll tick
        var run = Run(id: 559, backupJobId: job.Id);

        var cancelRequested = false;
        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(() => cancelRequested
            ? TestData.Job(id: job.Id, expectedMaxDurationMinutes: 60, cancelRequestedRunId: run.Id)
            : job);

        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(run.Id, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);
        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Success(Config()));

        JobRunCompleteRequest? completeRequest = null;
        h.BackendApiClient
            .Setup(b => b.CompleteJobRunAsync(run.Id, It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()))
            .Callback<int, JobRunCompleteRequest, CancellationToken>((_, r, _) => completeRequest = r)
            .ReturnsAsync(JobRunUpdateOutcome.Success);

        // Real WinScpTransferClient can't distinguish watchdog timeout from
        // operator cancel -- it only sees "the token was cancelled" and
        // always reports TIMEOUT for a cancelled transfer (see its doc
        // comment referenced in BackupRunPipeline). Mimic that here: block
        // until cancelled, then report TIMEOUT.
        h.TransferClient
            .Setup(t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()))
            .Returns(async (TransferRequest _, IProgress<TransferProgress> _, CancellationToken ct) =>
            {
                // Flip the flag AFTER the transfer has actually started so the
                // very first poll tick (which fires after CancelPollInterval)
                // observes CancelRequestedRunId set.
                cancelRequested = true;
                try
                {
                    await Task.Delay(Timeout.Infinite, ct);
                }
                catch (OperationCanceledException)
                {
                    // fall through to the TIMEOUT result below
                }

                return new TransferResult { Success = false, Status = JobRunStatus.TIMEOUT, ErrorMessage = "cancelled" };
            });

        var pipeline = h.BuildPipeline();

        var outcome = await pipeline.RunClaimedAsync(job, run, _ => Task.FromResult<string?>(job.SourcePath), CancellationToken.None);

        Assert.Equal(BackupRunOutcome.Cancelled, outcome);
        Assert.NotNull(completeRequest);
        Assert.Equal(JobRunStatus.CANCELLED, completeRequest!.Status);
        Assert.Equal("Cancelled by operator", completeRequest.ErrorMessage);
    }

    [Fact]
    public async Task RunClaimedAsync_WatchdogTimeoutWithoutOperatorCancel_StaysTimeoutNotCancelled()
    {
        // Negative case for the same reclassification logic: a TIMEOUT
        // transfer result with NO operator cancel observed must stay TIMEOUT
        // (i.e. map to BackupRunOutcome.Failed), never silently become
        // Cancelled just because the underlying token happened to be
        // cancelled by the watchdog/shutdown source instead.
        var h = new Harness();
        var job = TestData.Job(id: 15, expectedMaxDurationMinutes: 60);
        var run = Run(id: 560, backupJobId: job.Id);

        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(job); // CancelRequestedRunId never set

        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(run.Id, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);
        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Success(Config()));

        JobRunCompleteRequest? completeRequest = null;
        h.BackendApiClient
            .Setup(b => b.CompleteJobRunAsync(run.Id, It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()))
            .Callback<int, JobRunCompleteRequest, CancellationToken>((_, r, _) => completeRequest = r)
            .ReturnsAsync(JobRunUpdateOutcome.Success);

        h.TransferClient
            .Setup(t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new TransferResult { Success = false, Status = JobRunStatus.TIMEOUT, ErrorMessage = "watchdog timeout" });

        var pipeline = h.BuildPipeline();

        var outcome = await pipeline.RunClaimedAsync(job, run, _ => Task.FromResult<string?>(job.SourcePath), CancellationToken.None);

        Assert.Equal(BackupRunOutcome.Failed, outcome);
        Assert.NotNull(completeRequest);
        Assert.Equal(JobRunStatus.TIMEOUT, completeRequest!.Status);
    }

    // ------------------------------------------------------------------
    // Shutdown-races-transfer guard (checked BEFORE the operator-cancel
    // reclassification above): a transfer that comes back TIMEOUT because
    // the application itself is shutting down (shutdownToken already
    // cancelled) must NOT get CompleteAsync/CreateBackupRecordAsync called --
    // the run must stay RUNNING on the backend for the backend's own
    // missed-run/timeout recovery logic. A transfer that already succeeded,
    // even if shutdownToken also happens to be cancelled by the time control
    // returns, must still get those "must survive" calls -- shutdown must
    // never silently drop a completed transfer's result.
    // ------------------------------------------------------------------

    [Fact]
    public async Task RunClaimedAsync_TransferTimesOutWithShutdownRequested_ReturnsFailed_NeverCallsCompleteOrCreateBackupRecord()
    {
        var h = new Harness();
        var job = TestData.Job(id: 16, expectedMaxDurationMinutes: 60);
        var run = Run(id: 561, backupJobId: job.Id);

        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(job); // enabled, no cancel requested, always-open window

        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(run.Id, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);
        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Success(Config()));

        // Mimics WinScpTransferClient's real behavior: it cannot distinguish
        // an application-shutdown-triggered cancellation from a genuine
        // watchdog timeout or operator cancel -- it always reports TIMEOUT
        // for any cancelled transfer. Only RunFromExistingRunAsync, holding
        // shutdownToken directly, can tell shutdown apart from the other two.
        h.TransferClient
            .Setup(t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new TransferResult { Success = false, Status = JobRunStatus.TIMEOUT, ErrorMessage = "cancelled" });

        var pipeline = h.BuildPipeline();

        using var shutdownCts = new CancellationTokenSource();
        shutdownCts.Cancel(); // shutdown already in progress by the time the transfer comes back

        var outcome = await pipeline.RunClaimedAsync(job, run, _ => Task.FromResult<string?>(job.SourcePath), shutdownCts.Token);

        Assert.Equal(BackupRunOutcome.Failed, outcome);
        h.BackendApiClient.Verify(
            b => b.CompleteJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()), Times.Never);
        h.BackendApiClient.Verify(
            b => b.CreateBackupRecordAsync(It.IsAny<BackupRecordCreateRequest>(), It.IsAny<CancellationToken>()), Times.Never);
        h.OfflineQueue.Verify(
            q => q.EnqueueAsync(It.IsAny<QueuedEventType>(), It.IsAny<string>(), It.IsAny<int?>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task RunClaimedAsync_TransferSucceedsEvenWithShutdownRequested_StillCompletesAndCreatesBackupRecord()
    {
        // The critical must-survive-event guarantee the shutdown-race fix
        // must not break: a transfer that already finished successfully is
        // reported regardless of shutdownToken's state by the time control
        // returns from TransferAsync -- the new TIMEOUT-specific check above
        // must never accidentally swallow a SUCCESS result.
        var h = new Harness();
        var job = TestData.Job(id: 17, expectedMaxDurationMinutes: 60);
        var run = Run(id: 562, backupJobId: job.Id);

        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(job);

        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(run.Id, It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);
        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Success(Config()));
        h.BackendApiClient
            .Setup(b => b.CompleteJobRunAsync(run.Id, It.IsAny<JobRunCompleteRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);
        h.BackendApiClient
            .Setup(b => b.CreateBackupRecordAsync(It.IsAny<BackupRecordCreateRequest>(), It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask);

        h.TransferClient
            .Setup(t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new TransferResult
            {
                Success = true,
                Status = JobRunStatus.SUCCESS,
                RemotePath = "/remote/db.bak",
                FileSizeBytes = 12345,
                Sha256Checksum = "deadbeef",
            });

        var pipeline = h.BuildPipeline();

        using var shutdownCts = new CancellationTokenSource();
        shutdownCts.Cancel(); // shutdown already requested by the time the (successful) transfer comes back

        var outcome = await pipeline.RunClaimedAsync(job, run, _ => Task.FromResult<string?>(job.SourcePath), shutdownCts.Token);

        Assert.Equal(BackupRunOutcome.Success, outcome);
        h.BackendApiClient.Verify(
            b => b.CreateBackupRecordAsync(It.IsAny<BackupRecordCreateRequest>(), It.IsAny<CancellationToken>()), Times.Once);
        h.BackendApiClient.Verify(
            b => b.CompleteJobRunAsync(
                run.Id, It.Is<JobRunCompleteRequest>(r => r.Status == JobRunStatus.SUCCESS), It.IsAny<CancellationToken>()),
            Times.Once);
    }

    // NOTE: the pre-existing regression case for "TIMEOUT with operatorCancelCts
    // cancelled but shutdownToken NOT cancelled -> unchanged CANCELLED
    // reclassification" is already exercised end-to-end by
    // RunClaimedAsync_TransferCancelledSpecificallyByOperatorPoll_ReclassifiesTimeoutAsCancelled
    // above: that test passes CancellationToken.None as shutdownToken (never
    // cancelled), so the new shutdown-race check added before the
    // operator-cancel check is guaranteed false there and falls through to the
    // existing reclassification logic unchanged. No separate test needed --
    // re-verified passing as part of this run.
}
