namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// Fallback (or only, when msdb isn't applicable to a job) WATCH-mode
/// readiness detector: is the file still exclusively held open by its
/// writer? ExclusiveOpenFileLockChecker (Worker project) is the only
/// implementation that touches System.IO.File directly.
/// </summary>
public interface IFileLockChecker
{
    /// <summary>
    /// True if localFilePath can be opened with FileShare.None right now
    /// (i.e. no other process holds it open) -- false if it's still locked by the
    /// writer. Any exception other than a sharing-violation-shaped one (file not
    /// found, access denied for an unrelated reason) should propagate, not be
    /// silently treated as "still locked".
    /// </summary>
    bool IsUnlocked(string localFilePath);
}
