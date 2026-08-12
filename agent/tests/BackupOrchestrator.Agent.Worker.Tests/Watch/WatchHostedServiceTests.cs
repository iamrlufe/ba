using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Worker.Pipeline;
using BackupOrchestrator.Agent.Worker.Tests.Support;
using BackupOrchestrator.Agent.Worker.Watch;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;

namespace BackupOrchestrator.Agent.Worker.Tests.Watch;

/// <summary>
/// Exercises WatchHostedService.TickOnceAsync's dispatch-outcome handling
/// against mocked IWatchLedger/IBackendApiClient/IBackupTransferClient
/// (BackupRunPipeline is a sealed concrete class, wired here to mocks at its
/// actual dependency boundary -- same convention as BackupRunPipelineTests/
/// SchedulerHostedServiceTests). Never a real WinSCP session, HTTP call, or
/// filesystem watcher.
///
/// *** FIXED, PARTIALLY -- SEE REMAINING SCOPE NOTE BELOW ***
/// WatchHostedService.RunWaitAndDispatchAsync's outer `finally` block used
/// to call TryStartDispatchCycle(job) UNCONDITIONALLY on every exit path
/// (including "nothing left to dispatch"), and TryStartDispatchCycle's own
/// guards (JobScheduler.IsRunning / WatchCandidateTracker.TryBeginDispatchCycle)
/// were freshly cleared by that same finally block just before the call --
/// so nothing stopped it from immediately re-entering. When every awaited
/// call in the chain completed synchronously (i.e. WATCH job's copy window
/// open right now, wait loop's `break` reached with zero awaits), this was
/// a direct, unbounded, SYNCHRONOUS recursive call chain on a single thread
/// stack -- confirmed via an isolated repro to crash with an uncatchable
/// StackOverflowException after ~1300 frames.
///
/// FIXED for the "nothing held" case: the finally block now calls
/// WatchCandidateTracker.HasHeldCandidate(job.Id) AFTER EndDispatchCycle and
/// only restarts if true (see WatchCandidateTracker.HasHeldCandidate and the
/// finally block in RunWaitAndDispatchAsync). See
/// TickOnce_TransferSucceeds_NothingHeldAfterward_DispatchCycleDoesNotRestart
/// below for the regression test -- it uses an always-open window (not the
/// closed-window parking workaround the other tests need) specifically
/// because it must observe that no second dispatch cycle starts.
///
/// REMAINING, NARROWER SCOPE (not fixed, flagged by review, accepted as a
/// separate lower-severity concern -- not a crash risk in production): the
/// Cancelled/Skipped/Failed-under-max-attempts paths re-offer the same
/// candidate into the tracker BEFORE the finally block runs, so
/// HasHeldCandidate legitimately sees `true` and restarts immediately, with
/// no backoff between attempts. In production this is masked by genuine
/// async I/O (a real HTTP/WinSCP call always yields the thread at least
/// once before the next restart), so it manifests as a zero-backoff hot-
/// retry loop under a persistently-failing destination, not a stack
/// overflow -- but it is why the tests below still can't rely on Moq's
/// synchronously-completing ReturnsAsync tasks alone for those scenarios.
///
/// To test the BackupRunOutcome switch for the Cancelled/Skipped/Failed
/// scenarios WITHOUT that hot-retry loop spinning the (mocked, synchronous)
/// test itself, those tests give the job an OPEN copy window only for the
/// exact number of IJobCache.GetById calls needed to reach the switch
/// statement once (scripted per scenario below), then flip to a CLOSED,
/// far-from-reopening window for all calls after that. This makes the
/// re-offered candidate's next dispatch attempt hit a genuine
/// `await Task.Delay(...)` suspension instead of completing synchronously --
/// it safely parks in the background (using CancellationToken.None, since
/// TickOnceAsync is invoked directly here rather than through
/// StartAsync/ExecuteAsync) rather than spinning. This is a workaround for
/// observing the switch's side effects safely in THIS test file, not a
/// claim that the remaining hot-retry-loop behavior itself is fixed.
/// </summary>
public sealed class WatchHostedServiceTests
{
    private static readonly DateTimeOffset T = new(2026, 8, 12, 12, 0, 0, TimeSpan.Zero); // noon UTC

    private static AgentOptions Options(int maxWatchTransferAttempts = 5) => new()
    {
        ServerId = 1,
        AgentKey = "agent-key",
        ConnectionConfigKey = "connection-config-key",
        BackendBaseUrl = "https://backend.example.com",
        OfflineQueueDirectory = "/var/lib/agent/queue",
        MaxWatchTransferAttempts = maxWatchTransferAttempts,
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

    private sealed class Harness
    {
        public Mock<IJobCache> JobCache { get; } = new();
        public Mock<IWatchLedger> Ledger { get; } = new();
        public Mock<IBackendApiClient> BackendApiClient { get; } = new();
        public Mock<IBackupTransferClient> TransferClient { get; } = new();
        public Mock<IOfflineEventQueue> OfflineQueue { get; } = new();
        public Mock<ISqlBackupFinishDetector> SqlDetector { get; } = new();
        public Mock<IFileLockChecker> LockChecker { get; } = new();
        public Mock<IWatchDirectoryMonitor> Monitor { get; } = new();
        public TestClock Clock { get; } = new(T);
        public WatchCandidateTracker Tracker { get; } = new();
        public JobScheduler JobScheduler { get; }

        public Harness()
        {
            JobScheduler = new JobScheduler(new FakeCronNextRunCalculator(), Clock);
        }

        public BackupRunPipeline BuildPipeline(AgentOptions options) => new(
            BackendApiClient.Object,
            TransferClient.Object,
            OfflineQueue.Object,
            JobCache.Object,
            Clock,
            Microsoft.Extensions.Options.Options.Create(options),
            NullLogger<BackupRunPipeline>.Instance);

        public WatchHostedService BuildService(AgentOptions options) => new(
            JobCache.Object,
            () => Monitor.Object,
            SqlDetector.Object,
            LockChecker.Object,
            Ledger.Object,
            Tracker,
            JobScheduler,
            BuildPipeline(options),
            BackendApiClient.Object,
            Clock,
            Microsoft.Extensions.Options.Options.Create(options),
            NullLogger<WatchHostedService>.Instance);
    }

    /// <summary>
    /// See class doc comment: returns a job with an unrestricted (open)
    /// copy window for the first `openCallCount` IJobCache.GetById calls,
    /// then a currently-closed (won't reopen for hours) window for every
    /// call after that -- so the post-dispatch recursive re-entry safely
    /// parks in a real Task.Delay instead of recursing synchronously.
    /// </summary>
    private static void SetupSingleDispatchThenPark(Harness h, BackupJobDto baseJob, int openCallCount)
    {
        var callCount = 0;
        h.JobCache.Setup(c => c.GetAll()).Returns([baseJob]);
        h.JobCache.Setup(c => c.GetById(baseJob.Id)).Returns(() =>
        {
            callCount++;
            if (callCount <= openCallCount)
            {
                return baseJob; // CopyWindowStartHour/EndHour null -> always open
            }

            // Closed right now (noon is outside 02:00-03:00) and many hours
            // from reopening -- forces a genuine async wait instead of a
            // synchronous `break`.
            return TestData.Job(
                id: baseJob.Id, triggerMode: "WATCH", watchDirectory: baseJob.WatchDirectory,
                copyWindowStartHour: 2, copyWindowEndHour: 3);
        });
    }

    private static WatchCandidateFile Candidate(int jobId) => new()
    {
        BackupJobId = jobId,
        LocalFilePath = @"C:\watched\backup-001.bak",
        OrderingTimestampUtc = T,
        DetectionMethod = WatchDetectionMethod.LockCheck,
        FileSizeBytes = 1000,
    };

    private static BackupJobDto WatchJob(int id) =>
        TestData.Job(id: id, triggerMode: "WATCH", watchDirectory: Directory.CreateTempSubdirectory().FullName);

    /// <summary>
    /// TickOnceAsync's dispatch work is kicked off fire-and-forget
    /// (TryStartDispatchCycle -> `_ = RunWaitAndDispatchAsync(job)`, never
    /// awaited by the caller) -- awaiting TickOnceAsync itself is NOT
    /// sufficient to guarantee the dispatch's ledger side effects have
    /// happened yet (confirmed empirically: without this settle, the
    /// ledger call is sometimes still in flight on a different
    /// continuation when the test's own assertions run, even though every
    /// mocked call individually completes synchronously -- some genuine
    /// async handoff exists in the real chain, e.g. around the operator-cancel
    /// poll task's cancellation/await in BackupRunPipeline's `finally`).
    /// Bounded settle instead of a hard assumption of full synchronicity.
    /// </summary>
    private static Task SettleAsync() => Task.Delay(300);

    [Fact]
    public async Task TickOnce_TransferSucceeds_MarksTransferred_NeverIncrementsAttemptCount()
    {
        var h = new Harness();
        var job = WatchJob(1);
        SetupSingleDispatchThenPark(h, job, openCallCount: 2); // 1 WatchHostedService wait-loop check + 1 pipeline WaitForCopyWindowAsync check
        h.Tracker.OfferCandidate(Candidate(job.Id), out _);
        h.Ledger.Setup(l => l.GetKnownFilePathsAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<string>());
        h.Ledger.Setup(l => l.GetNotReadyEntriesAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<WatchLedgerEntry>());

        h.BackendApiClient
            .Setup(b => b.CreateJobRunAsync(It.IsAny<JobRunCreateRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new JobRunDto { Id = 1, BackupJobId = job.Id, Status = JobRunStatus.PENDING, TriggeredBy = "watch", CreatedAt = DateTimeOffset.UtcNow });
        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);
        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Success(Config()));
        h.TransferClient
            .Setup(t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new TransferResult { Success = true, Status = JobRunStatus.SUCCESS, RemotePath = "/remote/backup-001.bak", FileSizeBytes = 1000, Sha256Checksum = "abc" });

        var service = h.BuildService(Options());

        await service.TickOnceAsync(CancellationToken.None);
        await SettleAsync();

        h.Ledger.Verify(l => l.MarkTransferredAsync(job.Id, Candidate(job.Id).LocalFilePath, It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Once);
        h.Ledger.Verify(l => l.IncrementAttemptCountAsync(It.IsAny<int>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        h.Ledger.Verify(l => l.MarkFailedPermanentAsync(It.IsAny<int>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task TickOnce_TransferFails_IncrementsAttemptCount_NeverMarksTransferred()
    {
        var h = new Harness();
        var job = WatchJob(2);
        SetupSingleDispatchThenPark(h, job, openCallCount: 2);
        h.Tracker.OfferCandidate(Candidate(job.Id), out _);
        h.Ledger.Setup(l => l.GetKnownFilePathsAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<string>());
        h.Ledger.Setup(l => l.GetNotReadyEntriesAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<WatchLedgerEntry>());
        h.Ledger.Setup(l => l.IncrementAttemptCountAsync(job.Id, It.IsAny<string>(), It.IsAny<CancellationToken>())).ReturnsAsync(1);

        h.BackendApiClient
            .Setup(b => b.CreateJobRunAsync(It.IsAny<JobRunCreateRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new JobRunDto { Id = 2, BackupJobId = job.Id, Status = JobRunStatus.PENDING, TriggeredBy = "watch", CreatedAt = DateTimeOffset.UtcNow });
        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);
        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Success(Config()));
        h.TransferClient
            .Setup(t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new TransferResult { Success = false, Status = JobRunStatus.FAILED, ErrorMessage = "disk full" });

        // MaxWatchTransferAttempts=1 so the one recorded attempt (1) is not
        // < max, giving MarkFailedPermanentAsync instead of a re-offer --
        // keeps the held candidate empty afterward (matches
        // SetupSingleDispatchThenPark's assumption that nothing more should
        // legitimately dispatch).
        var service = h.BuildService(Options(maxWatchTransferAttempts: 1));

        await service.TickOnceAsync(CancellationToken.None);
        await SettleAsync();

        h.Ledger.Verify(l => l.IncrementAttemptCountAsync(job.Id, Candidate(job.Id).LocalFilePath, It.IsAny<CancellationToken>()), Times.Once);
        h.Ledger.Verify(l => l.MarkTransferredAsync(It.IsAny<int>(), It.IsAny<string>(), It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Never);
        h.Ledger.Verify(l => l.MarkFailedPermanentAsync(job.Id, Candidate(job.Id).LocalFilePath, It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task TickOnce_RunAlreadyTerminalOnRunningTransition_CancelledOutcome_NeverIncrementsAttemptCount()
    {
        // Drives BackupRunOutcome.Cancelled via the RUNNING-transition
        // AlreadyTerminal path (see BackupRunPipelineTests for that logic in
        // isolation) rather than the operator-cancel-poll path, to keep this
        // test's GetById call-count scripting simple and fast.
        var h = new Harness();
        var job = WatchJob(3);
        SetupSingleDispatchThenPark(h, job, openCallCount: 2);
        h.Tracker.OfferCandidate(Candidate(job.Id), out _);
        h.Ledger.Setup(l => l.GetKnownFilePathsAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<string>());
        h.Ledger.Setup(l => l.GetNotReadyEntriesAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<WatchLedgerEntry>());

        h.BackendApiClient
            .Setup(b => b.CreateJobRunAsync(It.IsAny<JobRunCreateRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new JobRunDto { Id = 3, BackupJobId = job.Id, Status = JobRunStatus.PENDING, TriggeredBy = "watch", CreatedAt = DateTimeOffset.UtcNow });
        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(It.IsAny<int>(), It.Is<JobRunPatch>(p => p.Status == JobRunStatus.RUNNING), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.AlreadyTerminal);

        var service = h.BuildService(Options());

        await service.TickOnceAsync(CancellationToken.None);
        await SettleAsync();

        h.TransferClient.Verify(
            t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()), Times.Never);
        h.Ledger.Verify(l => l.IncrementAttemptCountAsync(It.IsAny<int>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        h.Ledger.Verify(l => l.MarkTransferredAsync(It.IsAny<int>(), It.IsAny<string>(), It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Never);
        // Cancelled re-offers the candidate without consuming an attempt --
        // it must still be held (not discarded) for a future dispatch cycle.
        Assert.NotNull(h.Tracker.ClaimForDispatch(job.Id));
    }

    [Fact]
    public async Task TickOnce_BackendRejectsJobRunCreationWith409_SkippedOutcome_NeverIncrementsAttemptCount()
    {
        // Drives BackupRunOutcome.Skipped via CreateJobRunAsync returning
        // null (backend 409) -- reaches the switch statement after only ONE
        // IJobCache.GetById call (WaitForCopyWindowAsync inside the pipeline
        // is never reached, since RunAsync returns Skipped before ever
        // calling RunFromExistingRunAsync).
        var h = new Harness();
        var job = WatchJob(4);
        SetupSingleDispatchThenPark(h, job, openCallCount: 1);
        h.Tracker.OfferCandidate(Candidate(job.Id), out _);
        h.Ledger.Setup(l => l.GetKnownFilePathsAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<string>());
        h.Ledger.Setup(l => l.GetNotReadyEntriesAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<WatchLedgerEntry>());

        h.BackendApiClient
            .Setup(b => b.CreateJobRunAsync(It.IsAny<JobRunCreateRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((JobRunDto?)null);

        var service = h.BuildService(Options());

        await service.TickOnceAsync(CancellationToken.None);
        await SettleAsync();

        h.Ledger.Verify(l => l.IncrementAttemptCountAsync(It.IsAny<int>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        h.Ledger.Verify(l => l.MarkTransferredAsync(It.IsAny<int>(), It.IsAny<string>(), It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Never);
        Assert.NotNull(h.Tracker.ClaimForDispatch(job.Id)); // re-offered, still held
    }

    // ------------------------------------------------------------------
    // Regression test for the StackOverflowException-causing bug described
    // in the class doc comment above: RunWaitAndDispatchAsync's outer
    // `finally` block used to call TryStartDispatchCycle unconditionally on
    // every exit path. It now only does so if WatchCandidateTracker.
    // HasHeldCandidate is true post-EndDispatchCycle. This test drives the
    // "nothing held afterward" case (a successful transfer, whose one and
    // only candidate was consumed by ClaimForDispatch and never re-offered)
    // and confirms the finally block does NOT restart a dispatch cycle --
    // instead of the closed-window "park" trick used by the other tests
    // above (which exists specifically to survive an unconditional-restart
    // bug), this uses an UNRESTRICTED, always-open copy window throughout:
    // if the fix ever regresses, a synchronous restart under an always-open
    // window is exactly what would recurse and crash with
    // StackOverflowException, so this setup is deliberately load-bearing,
    // not incidental. It stays safe against a hypothetical regression only
    // because it's exercised against the current, fixed implementation.
    // ------------------------------------------------------------------

    [Fact]
    public async Task TickOnce_TransferSucceeds_NothingHeldAfterward_DispatchCycleDoesNotRestart()
    {
        var h = new Harness();
        var job = WatchJob(5);
        var getByIdCallCount = 0;
        h.JobCache.Setup(c => c.GetAll()).Returns([job]);
        h.JobCache.Setup(c => c.GetById(job.Id)).Returns(() =>
        {
            getByIdCallCount++;
            return job; // CopyWindowStartHour/EndHour both null -> always open, no park needed
        });
        h.Tracker.OfferCandidate(Candidate(job.Id), out _);
        h.Ledger.Setup(l => l.GetKnownFilePathsAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<string>());
        h.Ledger.Setup(l => l.GetNotReadyEntriesAsync(job.Id, It.IsAny<CancellationToken>())).ReturnsAsync(Array.Empty<WatchLedgerEntry>());

        h.BackendApiClient
            .Setup(b => b.CreateJobRunAsync(It.IsAny<JobRunCreateRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new JobRunDto { Id = 5, BackupJobId = job.Id, Status = JobRunStatus.PENDING, TriggeredBy = "watch", CreatedAt = DateTimeOffset.UtcNow });
        h.BackendApiClient
            .Setup(b => b.PatchJobRunAsync(It.IsAny<int>(), It.IsAny<JobRunPatch>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(JobRunUpdateOutcome.Success);
        h.BackendApiClient
            .Setup(b => b.GetConnectionConfigAsync(job.ServerId, It.IsAny<CancellationToken>()))
            .ReturnsAsync(ConnectionConfigResult.Success(Config()));
        h.TransferClient
            .Setup(t => t.TransferAsync(It.IsAny<TransferRequest>(), It.IsAny<IProgress<TransferProgress>>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new TransferResult { Success = true, Status = JobRunStatus.SUCCESS, RemotePath = "/remote/backup-001.bak", FileSizeBytes = 1000, Sha256Checksum = "abc" });

        var service = h.BuildService(Options());

        await service.TickOnceAsync(CancellationToken.None);
        await SettleAsync();

        h.Ledger.Verify(l => l.MarkTransferredAsync(job.Id, Candidate(job.Id).LocalFilePath, It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Once);

        // Exactly the 2 GetById calls needed for one pass (1 from
        // RunWaitAndDispatchAsync's own wait loop + 1 from the pipeline's
        // WaitForCopyWindowAsync) -- if the finally block had restarted a
        // second dispatch cycle, this would be higher (or, pre-fix, the
        // process would already have crashed with StackOverflowException
        // before this assertion ever ran).
        Assert.Equal(2, getByIdCallCount);

        // Weaker, independently-meaningful confirmation: no dispatch cycle is
        // left in flight for this job -- TryBeginDispatchCycle must succeed
        // (it would fail/return false if a restarted cycle were still
        // running/had re-marked itself in-flight).
        Assert.True(h.Tracker.TryBeginDispatchCycle(job.Id));
        h.Tracker.EndDispatchCycle(job.Id);
    }
}
