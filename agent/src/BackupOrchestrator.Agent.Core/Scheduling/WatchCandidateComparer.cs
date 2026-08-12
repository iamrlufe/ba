using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Scheduling;

/// <summary>
/// Pure ordering rule: msdb-priority timestamp when known, else
/// LastWriteTimeUtc; tiebreak on lexicographically-later file name. Does NOT
/// prefer DetectionMethod == Msdb as a tiebreaker in its own right -- the rule
/// is about which timestamp is authoritative for a given file's own ordering
/// key, not that msdb-detected files always outrank lock-check-detected ones.
/// </summary>
public static class WatchCandidateComparer
{
    public static bool IsMoreRecent(WatchCandidateFile candidate, WatchCandidateFile existing)
    {
        if (candidate.OrderingTimestampUtc != existing.OrderingTimestampUtc)
        {
            return candidate.OrderingTimestampUtc > existing.OrderingTimestampUtc;
        }

        return string.CompareOrdinal(
            Path.GetFileName(candidate.LocalFilePath),
            Path.GetFileName(existing.LocalFilePath)) > 0;
    }
}
