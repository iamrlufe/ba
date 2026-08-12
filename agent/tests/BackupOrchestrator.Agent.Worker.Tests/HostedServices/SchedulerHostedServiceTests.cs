using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Worker.HostedServices;
using BackupOrchestrator.Agent.Worker.Pipeline;
using BackupOrchestrator.Agent.Worker.Tests.Support;
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
            NullLogger<SchedulerHostedService>.Instance);
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
}
