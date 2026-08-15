using Cronos;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Core.Tests.Support;

namespace BackupOrchestrator.Agent.Core.Tests.Scheduling;

public sealed class JobSchedulerTests
{
    private static readonly DateTimeOffset T = new(2026, 8, 11, 12, 0, 0, TimeSpan.Zero);
    private const string Cron = "* * * * *";

    [Fact]
    public void Tick_JobDueForTheFirstTime_IsIncludedInDueJobs()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        fake.Enqueue(Cron, T); // seed (now - 1s) resolves to exactly "now" -> due
        fake.Enqueue(Cron, T + TimeSpan.FromHours(1)); // bookkeeping advance once fired
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(id: 1, scheduleCron: Cron);

        var result = scheduler.Tick([job]);

        Assert.Single(result.DueJobs);
        Assert.Equal(job.Id, result.DueJobs[0].Id);
        Assert.Empty(result.SkippedOverlapJobs);
    }

    [Fact]
    public void Tick_JobNotYetDue_IsNotIncludedAndOverlapCalculatorNotConsultedForBookkeeping()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        fake.Enqueue(Cron, T + TimeSpan.FromHours(1)); // seed resolves to the future -> not due
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(scheduleCron: Cron);

        var result = scheduler.Tick([job]);

        Assert.Empty(result.DueJobs);
        Assert.Empty(result.SkippedOverlapJobs);
        Assert.Single(fake.Calls); // only the seed call -- no "following" bookkeeping call for a non-due job
    }

    [Fact]
    public void Tick_DisabledJob_IsSkippedEntirelyAndCronCalculatorIsNeverConsulted()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator(); // nothing enqueued -- any call throws
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(scheduleCron: Cron, isEnabled: false);

        var result = scheduler.Tick([job]);

        Assert.Empty(result.DueJobs);
        Assert.Empty(result.SkippedOverlapJobs);
        Assert.Empty(fake.Calls);
    }

    [Fact]
    public void Tick_JobDueButAlreadyRunning_IsReportedAsSkippedOverlap_NotDue()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        fake.Enqueue(Cron, T);
        fake.Enqueue(Cron, T + TimeSpan.FromHours(1));
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(scheduleCron: Cron);
        scheduler.MarkRunning(job.Id);

        var result = scheduler.Tick([job]);

        Assert.Empty(result.DueJobs);
        Assert.Single(result.SkippedOverlapJobs);
        Assert.Equal(job.Id, result.SkippedOverlapJobs[0].Id);
    }

    [Fact]
    public void Tick_SkippedOverlapFire_DoesNotCauseImmediateCatchUpOnTheVeryNextTick()
    {
        // Core guarantee of skip-and-log: a skipped fire must not be queued
        // up to fire again the instant the overlapping run finishes -- the
        // job's bookkeeping already advanced past this slot.
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        fake.Enqueue(Cron, T); // due at T
        fake.Enqueue(Cron, T + TimeSpan.FromHours(1)); // next real fire an hour later
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(scheduleCron: Cron);
        scheduler.MarkRunning(job.Id);

        var firstTick = scheduler.Tick([job]);
        Assert.Single(firstTick.SkippedOverlapJobs);

        scheduler.MarkFinished(job.Id);

        // Same instant, run just finished -- must NOT fire again immediately.
        var secondTick = scheduler.Tick([job]);

        Assert.Empty(secondTick.DueJobs);
        Assert.Empty(secondTick.SkippedOverlapJobs);
        Assert.Equal(2, fake.Calls.Count); // no further calculator calls -- next-fire time is cached at T+1h
    }

    [Fact]
    public void Tick_AfterSkippedOverlapAndFinish_FiresAgainOnceItsNextRealCronOccurrenceArrives()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        fake.Enqueue(Cron, T);
        fake.Enqueue(Cron, T + TimeSpan.FromHours(1));
        fake.Enqueue(Cron, T + TimeSpan.FromHours(2));
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(scheduleCron: Cron);
        scheduler.MarkRunning(job.Id);

        scheduler.Tick([job]); // skipped-overlap
        scheduler.MarkFinished(job.Id);
        clock.Advance(TimeSpan.FromHours(1)); // now == the previously-computed next fire time

        var result = scheduler.Tick([job]);

        Assert.Single(result.DueJobs);
        Assert.Empty(result.SkippedOverlapJobs);
    }

    [Fact]
    public void Tick_MultipleJobs_EvaluatesEachIndependently()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        fake.Enqueue("dueCron", T);
        fake.Enqueue("dueCron", T + TimeSpan.FromHours(1));
        fake.Enqueue("futureCron", T + TimeSpan.FromDays(1));
        var scheduler = new JobScheduler(fake, clock);
        var dueJob = TestData.Job(id: 1, scheduleCron: "dueCron");
        var futureJob = TestData.Job(id: 2, scheduleCron: "futureCron");
        var disabledJob = TestData.Job(id: 3, scheduleCron: "dueCron", isEnabled: false);

        var result = scheduler.Tick([dueJob, futureJob, disabledJob]);

        Assert.Single(result.DueJobs);
        Assert.Equal(1, result.DueJobs[0].Id);
        Assert.Empty(result.SkippedOverlapJobs);
    }

    [Fact]
    public void Tick_CronExpressionWithNoFutureOccurrence_JobNeverBecomesDueAgain()
    {
        // JobScheduler.Tick removes (rather than caches MaxValue) the
        // bookkeeping entry when GetNextOccurrence returns null, so the very
        // next tick re-seeds via GetOrComputeNextFire -- which itself falls
        // back to DateTimeOffset.MaxValue only once ITS seed call also comes
        // back null. Both calls must be scripted for this scenario.
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        fake.Enqueue(Cron, T); // tick 1 seed: due once
        fake.Enqueue(Cron, null); // tick 1 "following" bookkeeping call: no further occurrences
        fake.Enqueue(Cron, null); // tick 2 re-seed (cache entry was removed): still none -> falls back to MaxValue
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(scheduleCron: Cron);

        var firstTick = scheduler.Tick([job]);
        Assert.Single(firstTick.DueJobs);

        clock.Advance(TimeSpan.FromDays(3650)); // far future -- should still never be due again
        var secondTick = scheduler.Tick([job]);

        Assert.Empty(secondTick.DueJobs);
        Assert.Empty(secondTick.SkippedOverlapJobs);
        Assert.Equal(3, fake.Calls.Count);

        // From here on the MaxValue fallback IS cached, so a third tick must not call the calculator again.
        clock.Advance(TimeSpan.FromDays(3650));
        var thirdTick = scheduler.Tick([job]);
        Assert.Empty(thirdTick.DueJobs);
        Assert.Equal(3, fake.Calls.Count);
    }

    [Fact]
    public void Tick_WatchModeJob_IsFilteredOutEntirely_NeverEvaluatedAsDueOrOverlap()
    {
        // Placed before ScheduleCron is ever touched in Tick() -- required since
        // ScheduleCron is nullable (null for WATCH). FakeCronNextRunCalculator has
        // nothing enqueued, so any call into it (which would throw ArgumentNullException
        // on a null cron string before even reaching the fake) proves the filter fired.
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator(); // nothing enqueued -- any call throws
        var scheduler = new JobScheduler(fake, clock);
        var watchJob = TestData.Job(triggerMode: "WATCH", scheduleCron: null);
        SchedulerTickResult? result = null;

        var exception = Record.Exception(() => result = scheduler.Tick([watchJob]));

        Assert.Null(exception);
        Assert.Empty(result!.DueJobs);
        Assert.Empty(result.SkippedOverlapJobs);
        Assert.Empty(fake.Calls);
    }

    [Fact]
    public void Tick_MixOfScheduleAndWatchJobs_OnlyScheduleJobIsEvaluated()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        fake.Enqueue(Cron, T);
        fake.Enqueue(Cron, T + TimeSpan.FromHours(1));
        var scheduler = new JobScheduler(fake, clock);
        var scheduleJob = TestData.Job(id: 1, scheduleCron: Cron, triggerMode: "SCHEDULE");
        var watchJob = TestData.Job(id: 2, triggerMode: "WATCH", scheduleCron: null);

        var result = scheduler.Tick([scheduleJob, watchJob]);

        Assert.Single(result.DueJobs);
        Assert.Equal(1, result.DueJobs[0].Id);
        Assert.Empty(result.SkippedOverlapJobs);
    }

    [Theory]
    [InlineData(45, 120, 45)]
    [InlineData(null, 120, 120)]
    public void GetWatchdogTimeout_UsesJobOwnValueOrFallsBackToDefault(
        int? expectedMaxDurationMinutes, int defaultMinutes, int expectedMinutes)
    {
        var job = TestData.Job(expectedMaxDurationMinutes: expectedMaxDurationMinutes);

        var timeout = JobScheduler.GetWatchdogTimeout(job, defaultMinutes);

        Assert.Equal(TimeSpan.FromMinutes(expectedMinutes), timeout);
    }

    // -----------------------------------------------------------------
    // Regression coverage for the ConcurrentDictionary<int,byte> fix:
    // MarkRunning/IsRunning/MarkFinished were previously backed by a plain
    // HashSet<int>, unsafe under concurrent access from the timer thread
    // (Tick/MarkRunning) and arbitrary thread-pool threads
    // (MarkFinished via ContinueWith). This must survive heavy concurrent
    // load without throwing and without leaving IsRunning permanently
    // (and wrongly) true for a job whose run has actually finished.
    // -----------------------------------------------------------------

    [Fact]
    public async Task ConcurrentMarkRunningIsRunningMarkFinished_AcrossManyJobsAndThreads_DoesNotThrow()
    {
        var clock = new TestClock(T);
        var scheduler = new JobScheduler(new FakeCronNextRunCalculator(), clock);
        const int jobCount = 200;
        const int iterationsPerJob = 300;

        var tasks = new List<Task>();
        for (var jobId = 0; jobId < jobCount; jobId++)
        {
            var capturedId = jobId;
            // Three concurrent roles per job id, matching the real access
            // pattern: Tick's thread calling MarkRunning/IsRunning, and an
            // arbitrary thread-pool continuation calling MarkFinished.
            tasks.Add(Task.Run(() =>
            {
                for (var i = 0; i < iterationsPerJob; i++)
                {
                    scheduler.MarkRunning(capturedId);
                }
            }));
            tasks.Add(Task.Run(() =>
            {
                for (var i = 0; i < iterationsPerJob; i++)
                {
                    _ = scheduler.IsRunning(capturedId);
                }
            }));
            tasks.Add(Task.Run(() =>
            {
                for (var i = 0; i < iterationsPerJob; i++)
                {
                    scheduler.MarkFinished(capturedId);
                }
            }));
        }

        var exception = await Record.ExceptionAsync(() => Task.WhenAll(tasks));
        Assert.Null(exception);

        // Deterministic cleanup pass (sequential, no more races) then verify
        // every job id converges to "not running" -- i.e. TryRemove/ContainsKey
        // still behave correctly after the concurrent storm, no corrupted state.
        for (var jobId = 0; jobId < jobCount; jobId++)
        {
            scheduler.MarkFinished(jobId);
            Assert.False(scheduler.IsRunning(jobId));
        }
    }

    [Fact]
    public async Task ConcurrentMarkRunningAndMarkFinished_OnTheSameSingleJobId_NeverThrowsAndConvergesCorrectly()
    {
        // Tighter variant: hammer the exact same key from many threads
        // simultaneously (worst case for a non-thread-safe HashSet) rather
        // than spreading load across distinct keys.
        var clock = new TestClock(T);
        var scheduler = new JobScheduler(new FakeCronNextRunCalculator(), clock);
        const int jobId = 42;
        const int taskCount = 32;
        const int iterations = 2000;

        var tasks = Enumerable.Range(0, taskCount).Select(t => Task.Run(() =>
        {
            for (var i = 0; i < iterations; i++)
            {
                if (i % 2 == 0)
                {
                    scheduler.MarkRunning(jobId);
                }
                else
                {
                    scheduler.MarkFinished(jobId);
                }

                _ = scheduler.IsRunning(jobId);
            }
        })).ToList();

        var exception = await Record.ExceptionAsync(() => Task.WhenAll(tasks));
        Assert.Null(exception);

        scheduler.MarkFinished(jobId);
        Assert.False(scheduler.IsRunning(jobId));
    }

    // -----------------------------------------------------------------
    // Regression coverage for the "invalid cron in one BackupJob crashes the
    // whole agent process" bug fix. These are the tests reviewer flagged as
    // the load-bearing ones -- without EnqueueThrow actually driving a real
    // exception through Tick()'s try/catch, nothing here proves the crash is
    // actually fixed.
    // -----------------------------------------------------------------

    [Fact]
    public void Tick_OneJobHasInvalidCron_DoesNotThrow_AndHealthyJobsStillProcessedSameTick()
    {
        // THE regression test for the bug this whole fix exists for: a
        // CronFormatException thrown while evaluating one BackupJob must not
        // escape Tick() (which would crash the whole BackgroundService host,
        // since BackgroundServiceExceptionBehavior.StopHost is left in place
        // for everything else) and must not prevent OTHER, healthy jobs in
        // the very same Tick() call from being correctly evaluated as due.
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        const string brokenCron = "not a cron expression";
        fake.EnqueueThrow(brokenCron, new CronFormatException("Invalid cron expression."));
        fake.Enqueue(Cron, T); // healthy job: due now
        fake.Enqueue(Cron, T + TimeSpan.FromHours(1)); // healthy job: bookkeeping advance
        var scheduler = new JobScheduler(fake, clock);
        var brokenJob = TestData.Job(id: 1, scheduleCron: brokenCron);
        var healthyJob = TestData.Job(id: 2, scheduleCron: Cron);

        SchedulerTickResult? result = null;
        var exception = Record.Exception(() => result = scheduler.Tick([brokenJob, healthyJob]));

        Assert.Null(exception);
        Assert.NotNull(result);
        Assert.Single(result!.DueJobs);
        Assert.Equal(healthyJob.Id, result.DueJobs[0].Id);
        Assert.Empty(result.SkippedOverlapJobs);

        var notification = Assert.Single(result.ScheduleErrorNotifications);
        Assert.Equal(brokenJob.Id, notification.JobId);
        Assert.True(notification.IsBroken);
        Assert.True(notification.NeedsLogging);
        Assert.True(notification.NeedsBackendReport);
        Assert.Equal("Invalid cron expression.", notification.ErrorMessage);
    }

    [Fact]
    public void Tick_PersistentlyBrokenJob_NeedsLoggingStaysTrueUntilAcked_AndNeverDuplicatesAcrossTicks()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        const string brokenCron = "still not a cron expression";
        // Broken on every one of the 3 ticks below (nothing ever gets
        // cached in _nextFireUtcByJobId on a throw, so GetOrComputeNextFire
        // re-consults the calculator every single Tick()).
        fake.EnqueueThrow(brokenCron, new CronFormatException("bad #1"));
        fake.EnqueueThrow(brokenCron, new CronFormatException("bad #2"));
        fake.EnqueueThrow(brokenCron, new CronFormatException("bad #3"));
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(id: 1, scheduleCron: brokenCron);

        var tick1 = scheduler.Tick([job]);
        var notification1 = Assert.Single(tick1.ScheduleErrorNotifications);
        Assert.True(notification1.NeedsLogging);

        // No Mark* ack yet -- must still say NeedsLogging=true, and there
        // must still be exactly ONE notification for this job (not a growing
        // pile of duplicates for repeated failures of the same fingerprint).
        var tick2 = scheduler.Tick([job]);
        var notification2 = Assert.Single(tick2.ScheduleErrorNotifications);
        Assert.Equal(job.Id, notification2.JobId);
        Assert.True(notification2.NeedsLogging);

        scheduler.MarkScheduleTransitionLogged(job.Id);

        var tick3 = scheduler.Tick([job]);
        var notification3 = Assert.Single(tick3.ScheduleErrorNotifications);
        Assert.False(notification3.NeedsLogging);
        // Backend report was never acked in this test -- stays true throughout.
        Assert.True(notification3.NeedsBackendReport);
    }

    [Fact]
    public void Tick_JobRecoversAfterBeingBroken_ProducesExactlyOneRecoveryNotification()
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        const string cron = "recoverable-cron";
        fake.EnqueueThrow(cron, new CronFormatException("initially broken"));
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(id: 1, scheduleCron: cron);

        var tick1 = scheduler.Tick([job]);
        var brokenNotification = Assert.Single(tick1.ScheduleErrorNotifications);
        Assert.True(brokenNotification.IsBroken);

        // Fully ack the broken state (both log + backend report) so the
        // recovery notification below is unambiguously a NEW transition, not
        // just leftover unacked bookkeeping from the failure.
        scheduler.MarkScheduleTransitionLogged(job.Id);
        scheduler.MarkScheduleTransitionReportedToBackend(job.Id, brokenNotification.Fingerprint);

        // Cron is fixed now -- resolves to a future time (not due), we only
        // care about the recovery transition itself, not whether it fires.
        fake.Enqueue(cron, T + TimeSpan.FromHours(1));

        var tick2 = scheduler.Tick([job]);

        var recoveryNotification = Assert.Single(tick2.ScheduleErrorNotifications);
        Assert.Equal(job.Id, recoveryNotification.JobId);
        Assert.False(recoveryNotification.IsBroken);
        Assert.Equal(string.Empty, recoveryNotification.Fingerprint);
        Assert.True(recoveryNotification.NeedsLogging);
        Assert.True(recoveryNotification.NeedsBackendReport);
    }

    [Theory]
    [InlineData("absent")]
    [InlineData("disabled")]
    [InlineData("switchedToWatch")]
    public void Tick_BrokenJobBecomesIneligible_ReconciliationPassProducesRecoveryNotification(string scenario)
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        const string cron = "ineligible-cron";
        fake.EnqueueThrow(cron, new CronFormatException("broken before disappearing"));
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(id: 1, scheduleCron: cron);

        var tick1 = scheduler.Tick([job]);
        var brokenNotification = Assert.Single(tick1.ScheduleErrorNotifications);
        Assert.True(brokenNotification.IsBroken);

        // Fully ack so the reconciliation-driven recovery notification below
        // is unambiguous (same reasoning as the recovery test above).
        scheduler.MarkScheduleTransitionLogged(job.Id);
        scheduler.MarkScheduleTransitionReportedToBackend(job.Id, brokenNotification.Fingerprint);

        IReadOnlyList<BackupJobDto> nextSnapshot = scenario switch
        {
            "absent" => [],
            "disabled" => [TestData.Job(id: 1, scheduleCron: cron, isEnabled: false)],
            "switchedToWatch" => [TestData.Job(id: 1, triggerMode: "WATCH", scheduleCron: null)],
            _ => throw new InvalidOperationException(),
        };

        var tick2 = scheduler.Tick(nextSnapshot);

        var recoveryNotification = Assert.Single(tick2.ScheduleErrorNotifications);
        Assert.Equal(job.Id, recoveryNotification.JobId);
        Assert.False(recoveryNotification.IsBroken);
        Assert.Equal(string.Empty, recoveryNotification.Fingerprint);
        Assert.True(recoveryNotification.NeedsLogging);
        Assert.True(recoveryNotification.NeedsBackendReport);
    }

    [Fact]
    public void MarkScheduleTransitionReportedToBackend_StaleFingerprint_DoesNotAckCurrentState()
    {
        // Direct regression coverage for the race the reviewer flagged: a
        // fire-and-forget backend-report continuation from an OLDER Tick
        // completing after state has moved on must not be able to mark the
        // CURRENT (different) state as acked.
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        const string cron = "guarded-cron";
        fake.EnqueueThrow(cron, new CronFormatException("broken #1"));
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(id: 1, scheduleCron: cron);

        var tick1 = scheduler.Tick([job]);
        var notification1 = Assert.Single(tick1.ScheduleErrorNotifications);
        var currentFingerprint = notification1.Fingerprint;
        Assert.True(notification1.NeedsBackendReport);

        scheduler.MarkScheduleTransitionLogged(job.Id);

        // Stale/incorrect fingerprint -- must be a no-op.
        scheduler.MarkScheduleTransitionReportedToBackend(job.Id, "stale|fingerprint-does-not-match");

        fake.EnqueueThrow(cron, new CronFormatException("broken #2 (still same fingerprint)"));
        var tick2 = scheduler.Tick([job]);
        var notification2 = Assert.Single(tick2.ScheduleErrorNotifications);
        // Proves the stale ack above had NO effect: still needs a backend report.
        Assert.True(notification2.NeedsBackendReport);
        Assert.Equal(currentFingerprint, notification2.Fingerprint);

        // Now ack with the CURRENT/correct fingerprint.
        scheduler.MarkScheduleTransitionReportedToBackend(job.Id, currentFingerprint);

        fake.EnqueueThrow(cron, new CronFormatException("broken #3 (still same fingerprint)"));
        var tick3 = scheduler.Tick([job]);
        // Fully acked (Logged + BackendAcked) with an unchanged fingerprint
        // -> filtered out of ScheduleErrorNotifications entirely.
        Assert.Empty(tick3.ScheduleErrorNotifications);
    }

    [Theory]
    [InlineData(true)] // TimeZoneNotFoundException
    [InlineData(false)] // InvalidTimeZoneException
    public void Tick_InvalidTimezoneExceptions_AreCaughtIdenticallyToCronFormatException(bool useTimeZoneNotFound)
    {
        var clock = new TestClock(T);
        var fake = new FakeCronNextRunCalculator();
        const string cron = "tz-cron";
        Exception thrown = useTimeZoneNotFound
            ? new TimeZoneNotFoundException("No such time zone.")
            : new InvalidTimeZoneException("Corrupt time zone data.");
        fake.EnqueueThrow(cron, thrown);
        var scheduler = new JobScheduler(fake, clock);
        var job = TestData.Job(id: 1, scheduleCron: cron, timezone: "Not/ARealZone");

        SchedulerTickResult? result = null;
        var exception = Record.Exception(() => result = scheduler.Tick([job]));

        Assert.Null(exception);
        var notification = Assert.Single(result!.ScheduleErrorNotifications);
        Assert.True(notification.IsBroken);
        Assert.Equal(thrown.Message, notification.ErrorMessage);
    }
}
