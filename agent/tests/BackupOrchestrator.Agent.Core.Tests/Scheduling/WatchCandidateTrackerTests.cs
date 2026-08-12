using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;

namespace BackupOrchestrator.Agent.Core.Tests.Scheduling;

public sealed class WatchCandidateTrackerTests
{
    private static readonly DateTimeOffset T = new(2026, 8, 11, 12, 0, 0, TimeSpan.Zero);

    private static WatchCandidateFile Candidate(
        DateTimeOffset orderingTimestampUtc, string fileName, int backupJobId = 1) =>
        new()
        {
            BackupJobId = backupJobId,
            LocalFilePath = $@"C:\watch\{fileName}",
            OrderingTimestampUtc = orderingTimestampUtc,
            DetectionMethod = WatchDetectionMethod.LockCheck,
            FileSizeBytes = 1024,
        };

    [Fact]
    public void OfferCandidate_NothingHeld_IsAccepted_AndRetrievableViaClaimForDispatch()
    {
        var tracker = new WatchCandidateTracker();
        var candidate = Candidate(T, "a.bak");

        var outcome = tracker.OfferCandidate(candidate, out var supersededOrDiscarded);

        Assert.Equal(CandidateOfferOutcome.Accepted, outcome);
        Assert.Null(supersededOrDiscarded);

        var claimed = tracker.ClaimForDispatch(candidate.BackupJobId);
        Assert.Same(candidate, claimed);
    }

    [Fact]
    public void OfferCandidate_NewerThanHeld_Supersedes_OutParamIsTheOldCandidate_ClaimReturnsTheNewOne()
    {
        var tracker = new WatchCandidateTracker();
        var older = Candidate(T, "a.bak");
        var newer = Candidate(T.AddMinutes(5), "b.bak");
        tracker.OfferCandidate(older, out _);

        var outcome = tracker.OfferCandidate(newer, out var supersededOrDiscarded);

        Assert.Equal(CandidateOfferOutcome.Superseded, outcome);
        Assert.Same(older, supersededOrDiscarded);

        var claimed = tracker.ClaimForDispatch(newer.BackupJobId);
        Assert.Same(newer, claimed);
    }

    [Fact]
    public void OfferCandidate_NotMoreRecentThanHeld_IsDiscarded_OutParamIsTheJustOfferedCandidate_HeldUnchanged()
    {
        var tracker = new WatchCandidateTracker();
        var held = Candidate(T.AddMinutes(5), "b.bak");
        var rejected = Candidate(T, "a.bak");
        tracker.OfferCandidate(held, out _);

        var outcome = tracker.OfferCandidate(rejected, out var supersededOrDiscarded);

        Assert.Equal(CandidateOfferOutcome.Discarded, outcome);
        Assert.Same(rejected, supersededOrDiscarded);

        var claimed = tracker.ClaimForDispatch(held.BackupJobId);
        Assert.Same(held, claimed); // held candidate unchanged
    }

    [Fact]
    public void OfferCandidate_AfterMarkTransferred_NotMoreRecentThanLastTransferred_IsDiscarded_EvenWithNothingHeld()
    {
        var tracker = new WatchCandidateTracker();
        var transferred = Candidate(T, "a.bak");
        tracker.OfferCandidate(transferred, out _);
        tracker.MarkTransferred(tracker.ClaimForDispatch(transferred.BackupJobId)!); // slot cleared

        Assert.Null(tracker.ClaimForDispatch(transferred.BackupJobId)); // confirm nothing held

        var stale = Candidate(T, "a.bak"); // same timestamp -- not "more recent" per the comparer
        var outcome = tracker.OfferCandidate(stale, out var supersededOrDiscarded);

        Assert.Equal(CandidateOfferOutcome.Discarded, outcome);
        Assert.Same(stale, supersededOrDiscarded);
        Assert.Null(tracker.ClaimForDispatch(transferred.BackupJobId));
    }

    [Fact]
    public void OfferCandidate_AfterMarkTransferred_MoreRecentThanLastTransferred_IsAccepted()
    {
        var tracker = new WatchCandidateTracker();
        var transferred = Candidate(T, "a.bak");
        tracker.OfferCandidate(transferred, out _);
        tracker.MarkTransferred(tracker.ClaimForDispatch(transferred.BackupJobId)!);

        var fresh = Candidate(T.AddMinutes(10), "b.bak");
        var outcome = tracker.OfferCandidate(fresh, out var supersededOrDiscarded);

        Assert.Equal(CandidateOfferOutcome.Accepted, outcome);
        Assert.Null(supersededOrDiscarded);
        Assert.Same(fresh, tracker.ClaimForDispatch(fresh.BackupJobId));
    }

    [Fact]
    public void TryBeginDispatchCycle_ReturnsTrueOnce_FalseUntilEndDispatchCycle_TrueAgainAfter()
    {
        var tracker = new WatchCandidateTracker();
        const int jobId = 1;

        Assert.True(tracker.TryBeginDispatchCycle(jobId));
        Assert.False(tracker.TryBeginDispatchCycle(jobId));
        Assert.False(tracker.TryBeginDispatchCycle(jobId));

        tracker.EndDispatchCycle(jobId);

        Assert.True(tracker.TryBeginDispatchCycle(jobId));
    }

    [Fact]
    public void TryBeginDispatchCycle_IndependentPerJobId()
    {
        var tracker = new WatchCandidateTracker();

        Assert.True(tracker.TryBeginDispatchCycle(1));
        Assert.True(tracker.TryBeginDispatchCycle(2)); // a different job id is unaffected
        Assert.False(tracker.TryBeginDispatchCycle(1));
    }

    [Fact]
    public void ClaimForDispatch_NothingHeld_ReturnsNull()
    {
        var tracker = new WatchCandidateTracker();

        Assert.Null(tracker.ClaimForDispatch(999));
    }

    [Fact]
    public void ClaimForDispatch_CalledTwiceAfterOneOffer_SecondCallReturnsNull_SlotCorrectlyCleared()
    {
        var tracker = new WatchCandidateTracker();
        var candidate = Candidate(T, "a.bak");
        tracker.OfferCandidate(candidate, out _);

        var first = tracker.ClaimForDispatch(candidate.BackupJobId);
        var second = tracker.ClaimForDispatch(candidate.BackupJobId);

        Assert.Same(candidate, first);
        Assert.Null(second);
    }

    // ------------------------------------------------------------------
    // HasHeldCandidate: read-only peek used by WatchHostedService to decide
    // whether ending-and-immediately-restarting a dispatch cycle is worth
    // doing. Must never consume/remove the held candidate itself.
    // ------------------------------------------------------------------

    [Fact]
    public void HasHeldCandidate_NothingOffered_ReturnsFalse()
    {
        var tracker = new WatchCandidateTracker();

        Assert.False(tracker.HasHeldCandidate(1));
    }

    [Fact]
    public void HasHeldCandidate_AfterOfferCandidate_ReturnsTrue()
    {
        var tracker = new WatchCandidateTracker();
        var candidate = Candidate(T, "a.bak");

        tracker.OfferCandidate(candidate, out _);

        Assert.True(tracker.HasHeldCandidate(candidate.BackupJobId));
    }

    [Fact]
    public void HasHeldCandidate_AfterClaimForDispatchConsumesIt_ReturnsFalseAgain()
    {
        var tracker = new WatchCandidateTracker();
        var candidate = Candidate(T, "a.bak");
        tracker.OfferCandidate(candidate, out _);

        tracker.ClaimForDispatch(candidate.BackupJobId);

        Assert.False(tracker.HasHeldCandidate(candidate.BackupJobId));
    }

    [Fact]
    public void HasHeldCandidate_DoesNotConsumeCandidate_ClaimForDispatchStillReturnsTheRealCandidateAfterPeeking()
    {
        var tracker = new WatchCandidateTracker();
        var candidate = Candidate(T, "a.bak");
        tracker.OfferCandidate(candidate, out _);

        var peeked = tracker.HasHeldCandidate(candidate.BackupJobId);
        Assert.True(peeked);

        // The peek above must not have removed anything -- ClaimForDispatch
        // must still return the actual candidate, not null.
        var claimed = tracker.ClaimForDispatch(candidate.BackupJobId);
        Assert.Same(candidate, claimed);

        // And now that it really has been claimed, the slot is empty.
        Assert.False(tracker.HasHeldCandidate(candidate.BackupJobId));
    }

    [Fact]
    public async Task ConcurrentOfferCandidate_ForTheSameJob_DoesNotThrow_AndExactlyOneGenuinelyMostRecentCandidateIsHeld()
    {
        var tracker = new WatchCandidateTracker();
        const int jobId = 7;
        const int candidateCount = 500;

        // Distinct, strictly increasing timestamps so there is exactly one
        // unambiguous "most recent" winner regardless of arrival order.
        var candidates = Enumerable.Range(0, candidateCount)
            .Select(i => Candidate(T.AddSeconds(i), $"file_{i:D5}.bak", jobId))
            .ToList();
        var expectedWinner = candidates[^1]; // the one with the latest timestamp

        var exception = await Record.ExceptionAsync(() => Task.WhenAll(
            candidates.Select(c => Task.Run(() => tracker.OfferCandidate(c, out _)))));

        Assert.Null(exception);

        var held = tracker.ClaimForDispatch(jobId);
        Assert.NotNull(held);
        Assert.Same(expectedWinner, held);
        Assert.Null(tracker.ClaimForDispatch(jobId)); // slot cleared, nothing left dangling
    }
}
