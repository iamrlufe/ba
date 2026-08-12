using BackupOrchestrator.Agent.Core.Contracts;

namespace BackupOrchestrator.Agent.Worker.Watch;

/// <summary>
/// The only IFileLockChecker implementation that touches System.IO.File
/// directly. Fallback (or only, when msdb isn't applicable) WATCH-mode
/// readiness detector: attempts an exclusive open, treating a sharing
/// violation as "still locked by the writer" and letting any other
/// exception (file not found, unrelated permission failure) propagate so
/// the caller's per-file loop can log it distinctly.
/// </summary>
public sealed class ExclusiveOpenFileLockChecker : IFileLockChecker
{
    public bool IsUnlocked(string localFilePath)
    {
        try
        {
            using var fs = File.Open(localFilePath, FileMode.Open, FileAccess.Read, FileShare.None);
            return true;
        }
        catch (IOException)
        {
            // Sharing violation -- another process (the backup writer) still
            // holds this file open. Not ready yet; not an error.
            return false;
        }
    }
}
