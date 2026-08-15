using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Worker.HostedServices;
using BackupOrchestrator.Agent.Worker.Pipeline;
using BackupOrchestrator.Agent.Worker.Tests.Support;
using Cronos;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;

namespace BackupOrchestrator.Agent.Worker.Tests.HostedServices;

/// <summary>
/// Exercises SchedulerHostedService.TickOnce's manual-dispatch loop (the
/// internal, I/O-orchestration-free-of-timer method -- same convention as
/// MonitoringConfigPollHostedServiceTests) against a mocked IBackendApiClient
/// and a real BackupRunPipeline wired to mocked interfaces (BackupRunPipeline
/// itself is a sealed concrete class, not behind an interface -- see
/// BackupRunPipelineTests for the same "mock at the actual dependency
/// boundary" approach). Never a real HTTP call, WinSCP session, or cron tick
/// -- the cron due-check path (JobScheduler.Tick) is scripted to never fire
/// via FakeCronNextRunCalculator so only the manual-dispatch loop is under
/// test here.
/// </summary>
public sealed class SchedulerHostedServiceTests
{
    private static readonly DateTimeOffset T = new(2026, 8, 12, 12, 0, 0, TimeSpan.Zero);

    private static AgentOptions Options() => new()
    {
        ServerId = 1,
        AgentKey = "agent-key",
        ConnectionConfigKey = "connection-config-key",
        BackendBaseUrl = "https://backend.example.com",
        OfflineQueueDirectory = "/var/lib/agent/queue",
    };

    private sealed class Harness
    {
        public Mock<IBackendApiClient> BackendApiClient { get; } = new();
        public Mock<IBackupTransferClient> TransferClient { get; } = new();
        public Mock<IOfflineEventQueue> OfflineQueue { get; } = new();
        public Mock<IJobCache> JobCache { get; } = new();
        public TestClock Clock { get; } = new(T);
        public FakeCronNextRunCalculator CronCalculator { get; } = new();

        /// <summary>
        /// A real Mock (not NullLogger) so schedule-error-logging tests can
        /// Verify(...) against it -- same pattern as
        /// HttpBackendApiClientTests' mockLogger.Verify(l => l.Log(...)).
        /// Harmless for tests that don't inspect it: an un-set-up Mock's Log
        /// method is a no-op, just like NullLogger.
        /// </summary>
        public Mock<ILogger<SchedulerHostedService>> Logger { get; } = new();

        public JobScheduler BuildScheduler() => new(CronCalculator, Clock);

        public BackupRunPipeline BuildPipeline() => new(
            BackendApiClient.Object,
            TransferClient.Object,
            OfflineQueue.Object,
            JobCache.Object,
            Clock,
            Microsoft.Extensions.Options.Options.Create(Options()),
            NullLogger<BackupRunPipeline>.Instance);

        public SchedulerHostedService BuildService(JobScheduler scheduler, BackupRunPipeline pipeline) => new(
            scheduler,
            JobCache.Object,
            pipeline,
            BackendApiClient.Object,
            Logger.Object);

        public void VerifyLog(LogLevel level, Times times)
        {
            Logger.Verify(
                l => l.Log(
                    level,
                    It.IsAny<EventId>(),
                    It.IsAny<It.IsAnyType>(),
                    It.IsAny<Exception>(),
                    It.IsAny<Func<It.IsAnyType, Exception?, string>>()),
                times);
        }
    }

    private static BackupJobDto NeverDueScheduleJob(FakeCronNextRunCalculator cron, int id, int? pendingManualRunId, string triggerMode = "SCHEDULE")
    {
        const string expr = "* * * * *";
        cron.Enqueue(expr, T + TimeSpan.FromDays(365)); // seed: far future -> never due via the cron path
        return TestData.Job(id: id, scheduleCron: triggerMode == "SCHEDULE" ? expr : null, triggerMode: triggerMode, pendingManualRunId: pendingManualRunId);
    }

    [Fact]
    public void TickOnce_ManualRunPending_JobAlreadyRunning_DoesNotClaim()
    {
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = NeverDueScheduleJob(h.CronCalculator, id: 1, pendingManualRunId: 999);
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);
        scheduler.MarkRunning(job.Id); // simulate an in-flight run for this job

        var service = h.BuildService(scheduler, h.BuildPipeline());

        service.TickOnce(CancellationToken.None);

        h.BackendApiClient.Verify(b => b.ClaimJobRunAsync(It.IsAny<int>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public void TickOnce_ManualRunPending_NoPendingManualRunId_DoesNotClaim()
    {
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = NeverDueScheduleJob(h.CronCalculator, id: 2, pendingManualRunId: null);
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);

        var service = h.BuildService(scheduler, h.BuildPipeline());

        service.TickOnce(CancellationToken.None);

        h.BackendApiClient.Verify(b => b.ClaimJobRunAsync(It.IsAny<int>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public void TickOnce_ManualRunPending_WatchModeJob_DoesNotClaim()
    {
        // Defensive-only branch per SchedulerHostedService's doc comment
        // (manual triggering is backend-forbidden/409 for WATCH jobs, so
        // PendingManualRunId is not expected to ever be set on one in
        // practice) -- still must hold if it somehow were.
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = NeverDueScheduleJob(h.CronCalculator, id: 3, pendingManualRunId: 999, triggerMode: "WATCH");
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);

        var service = h.BuildService(scheduler, h.BuildPipeline());

        service.TickOnce(CancellationToken.None);

        h.BackendApiClient.Verify(b => b.ClaimJobRunAsync(It.IsAny<int>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task TickOnce_ManualRunPending_ScheduleModeJobNotRunning_ClaimsTheRun()
    {
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = NeverDueScheduleJob(h.CronCalculator, id: 4, pendingManualRunId: 777);
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);
        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(job);

        var claimSignal = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        h.BackendApiClient
            .Setup(b => b.ClaimJobRunAsync(777, It.IsAny<CancellationToken>()))
            .ReturnsAsync((JobRunDto?)null)
            .Callback(() => claimSignal.TrySetResult());

        var service = h.BuildService(scheduler, h.BuildPipeline());

        service.TickOnce(CancellationToken.None);

        var completed = await Task.WhenAny(claimSignal.Task, Task.Delay(TimeSpan.FromSeconds(5)));
        Assert.Same(claimSignal.Task, completed);

        h.BackendApiClient.Verify(b => b.ClaimJobRunAsync(777, It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task TickOnce_ManualRunPending_ClaimReturnsRun_InvokesPipelineRunClaimedAsync()
    {
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = NeverDueScheduleJob(h.CronCalculator, id: 5, pendingManualRunId: 888);
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);
        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(job);

        var claimedRun = new JobRunDto
        {
            Id = 888,
            BackupJobId = job.Id,
            Status = JobRunStatus.PENDING,
            TriggeredBy = "manual",
            CreatedAt = DateTimeOffset.UtcNow,
        };

        h.BackendApiClient
            .Setup(b => b.ClaimJobRunAsync(888, It.IsAny<CancellationToken>()))
            .ReturnsAsync(claimedRun);

        var connectionConfigSignal = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Failed(ConnectionConfigOutcome.ServerNotFound))
            .Callback(() => connectionConfigSignal.TrySetResult());

        var service = h.BuildService(scheduler, h.BuildPipeline());

        service.TickOnce(CancellationToken.None);

        var completed = await Task.WhenAny(connectionConfigSignal.Task, Task.Delay(TimeSpan.FromSeconds(5)));
        Assert.Same(connectionConfigSignal.Task, completed);

        // Reaching GetConnectionConfigAsync proves RunClaimedAsync actually
        // ran the claimed JobRunDto through the shared pipeline (past the
        // copy-window wait and the RUNNING transition) rather than
        // re-creating a fresh run via CreateJobRunAsync.
        h.BackendApiClient.Verify(b => b.CreateJobRunAsync(It.IsAny<JobRunCreateRequest>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    // -----------------------------------------------------------------
    // Regression coverage for the "invalid cron in one BackupJob crashes the
    // whole agent process" bug fix, at the TickOnce/hosted-service layer:
    // logging throttle, backend-report dispatch, the in-flight guard, and
    // BackendUnavailableException retry behavior.
    // -----------------------------------------------------------------

    private const string BrokenCron = "not a valid cron expression";

    private static BackupJobDto BrokenScheduleJob(int id = 1) =>
        TestData.Job(id: id, scheduleCron: BrokenCron, triggerMode: "SCHEDULE");

    [Fact]
    public void TickOnce_PersistentlyBrokenJob_LogsErrorExactlyOnce_NotOncePerTick()
    {
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = BrokenScheduleJob();
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);
        h.BackendApiClient
            .Setup(b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask);

        var service = h.BuildService(scheduler, h.BuildPipeline());

        for (var i = 0; i < 3; i++)
        {
            h.CronCalculator.EnqueueThrow(BrokenCron, new CronFormatException($"bad cron attempt {i}"));
            service.TickOnce(CancellationToken.None);
        }

        h.VerifyLog(LogLevel.Error, Times.Once());
    }

    [Fact]
    public void TickOnce_BrokenJob_ReportsToBackendOnce_ThenDoesNotReportAgainOnceAcked()
    {
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = BrokenScheduleJob();
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);
        h.BackendApiClient
            .Setup(b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask); // synchronously-completed task -- the fire-and-forget
                                           // continuation inside TickOnce runs inline, so the ack
                                           // (MarkScheduleTransitionReportedToBackend) has already
                                           // happened by the time TickOnce returns.

        var service = h.BuildService(scheduler, h.BuildPipeline());

        h.CronCalculator.EnqueueThrow(BrokenCron, new CronFormatException("bad cron #1"));
        service.TickOnce(CancellationToken.None);

        h.BackendApiClient.Verify(
            b => b.ReportScheduleErrorAsync(It.Is<ScheduleErrorRequest>(r => r.BackupJobId == job.Id && r.Active), It.IsAny<CancellationToken>()),
            Times.Once);

        // Same broken cron/timezone (same fingerprint) on the next tick --
        // already fully acked (Logged + BackendAcked), so no second report.
        h.CronCalculator.EnqueueThrow(BrokenCron, new CronFormatException("bad cron #2"));
        service.TickOnce(CancellationToken.None);

        h.BackendApiClient.Verify(
            b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public async Task TickOnce_InFlightBackendReport_DoesNotStartASecondConcurrentReportForTheSameJob()
    {
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = BrokenScheduleJob();
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);

        var reportGate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        h.BackendApiClient
            .Setup(b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()))
            .Returns(reportGate.Task); // never completes until the test says so

        var service = h.BuildService(scheduler, h.BuildPipeline());

        h.CronCalculator.EnqueueThrow(BrokenCron, new CronFormatException("bad cron #1"));
        service.TickOnce(CancellationToken.None); // kicks off the in-flight report, still pending

        h.BackendApiClient.Verify(
            b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()),
            Times.Once);

        // NeedsBackendReport is still true (never acked -- the report above
        // is still pending), so without the in-flight guard this would fire
        // a second concurrent call for the same job.
        h.CronCalculator.EnqueueThrow(BrokenCron, new CronFormatException("bad cron #2"));
        service.TickOnce(CancellationToken.None);

        h.BackendApiClient.Verify(
            b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()),
            Times.Once);

        reportGate.SetResult();
        await reportGate.Task; // let the pending continuation drain before the test harness disposes
    }

    [Fact]
    public void TickOnce_BackendUnavailableOnReport_LogsWarning_DoesNotThrow_AndRetriesNextTick()
    {
        var h = new Harness();
        var scheduler = h.BuildScheduler();
        var job = BrokenScheduleJob();
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);
        h.BackendApiClient
            .Setup(b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new BackendUnavailableException("backend down")); // faulted-but-already-completed
                                                                            // task -- the await inside
                                                                            // ReportScheduleErrorAsync
                                                                            // propagates/handles it inline.

        var service = h.BuildService(scheduler, h.BuildPipeline());

        h.CronCalculator.EnqueueThrow(BrokenCron, new CronFormatException("bad cron #1"));
        var exception1 = Record.Exception(() => service.TickOnce(CancellationToken.None));
        Assert.Null(exception1);

        h.BackendApiClient.Verify(
            b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()),
            Times.Once);
        h.VerifyLog(LogLevel.Warning, Times.Once());

        // NeedsBackendReport was never acked (the report failed) -- the very
        // next tick must retry, not give up permanently.
        h.CronCalculator.EnqueueThrow(BrokenCron, new CronFormatException("bad cron #2"));
        var exception2 = Record.Exception(() => service.TickOnce(CancellationToken.None));
        Assert.Null(exception2);

        h.BackendApiClient.Verify(
            b => b.ReportScheduleErrorAsync(It.IsAny<ScheduleErrorRequest>(), It.IsAny<CancellationToken>()),
            Times.Exactly(2));
        h.VerifyLog(LogLevel.Warning, Times.Exactly(2));
    }
}
