using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Scheduling;

public enum CandidateOfferOutcome
{
    Accepted,
    Superseded,
    Discarded,
}

/// <summary>
/// Per-WATCH-job single-candidate-slot state machine. Thread-safety
/// mirrors CpuUsageSampler's explicit single-lock pattern. Never a FIFO queue:
/// OfferCandidate always keeps at most one held candidate per job, replacing
/// (never appending) when a strictly more recent READY file arrives.
///
/// Deliberately holds NO notion of "a JobRun is pending for this candidate" --
/// the candidate slot and the copy-window wait are purely agent-side/in-memory.
/// A JobRun is created only inside BackupRunPipeline.RunAsync, invoked only
/// from WatchHostedService's dispatch path, only once ClaimForDispatch below
/// has actually succeeded. This deliberately differs from SCHEDULE mode (which
/// creates its JobRun immediately, before any window wait) -- see
/// WatchHostedService's doc comment for why.
/// </summary>
public sealed class WatchCandidateTracker
{
    private readonly object _gate = new();
    private readonly Dictionary<int, WatchCandidateFile> _heldCandidateByJobId = new();
    private readonly Dictionary<int, WatchCandidateFile> _lastTransferredByJobId = new();
    private readonly HashSet<int> _dispatchCycleInFlight = new();

    public CandidateOfferOutcome OfferCandidate(WatchCandidateFile candidate, out WatchCandidateFile? supersededOrDiscarded)
    {
        lock (_gate)
        {
            if (_lastTransferredByJobId.TryGetValue(candidate.BackupJobId, out var lastTransferred)
                && !WatchCandidateComparer.IsMoreRecent(candidate, lastTransferred))
            {
                supersededOrDiscarded = candidate;
                return CandidateOfferOutcome.Discarded;
            }

            if (_heldCandidateByJobId.TryGetValue(candidate.BackupJobId, out var held))
            {
                if (WatchCandidateComparer.IsMoreRecent(candidate, held))
                {
                    _heldCandidateByJobId[candidate.BackupJobId] = candidate;
                    supersededOrDiscarded = held;
                    return CandidateOfferOutcome.Superseded;
                }

                supersededOrDiscarded = candidate;
                return CandidateOfferOutcome.Discarded;
            }

            _heldCandidateByJobId[candidate.BackupJobId] = candidate;
            supersededOrDiscarded = null;
            return CandidateOfferOutcome.Accepted;
        }
    }

    public bool TryBeginDispatchCycle(int backupJobId)
    {
        lock (_gate)
        {
            return _dispatchCycleInFlight.Add(backupJobId);
        }
    }

    public void EndDispatchCycle(int backupJobId)
    {
        lock (_gate)
        {
            _dispatchCycleInFlight.Remove(backupJobId);
        }
    }

    /// <summary>
    /// Read-only peek: true if a candidate is currently held for
    /// backupJobId, without consuming it (unlike ClaimForDispatch). Used by
    /// WatchHostedService to decide whether ending-and-immediately-restarting
    /// a dispatch cycle is worth doing, breaking the unconditional-restart
    /// recursion that previously caused a StackOverflowException on
    /// zero-suspension dispatch cycles (unrestricted copy window + nothing
    /// claimed).
    /// </summary>
    public bool HasHeldCandidate(int backupJobId)
    {
        lock (_gate)
        {
            return _heldCandidateByJobId.ContainsKey(backupJobId);
        }
    }

    public WatchCandidateFile? ClaimForDispatch(int backupJobId)
    {
        lock (_gate)
        {
            if (!_heldCandidateByJobId.TryGetValue(backupJobId, out var held))
            {
                return null;
            }

            _heldCandidateByJobId.Remove(backupJobId);
            return held;
        }
    }

    public void MarkTransferred(WatchCandidateFile transferred)
    {
        lock (_gate)
        {
            _lastTransferredByJobId[transferred.BackupJobId] = transferred;
        }
    }
}
