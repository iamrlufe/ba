namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// The seam over a single directory's live file-appearance notifications.
/// FileSystemWatchDirectoryMonitor (Worker project) is the only
/// implementation that touches System.IO.FileSystemWatcher. Created and
/// disposed per-WATCH-job by WatchHostedService, never a singleton itself --
/// see its class doc comment.
/// </summary>
public interface IWatchDirectoryMonitor : IDisposable
{
    /// <summary>
    /// Begins watching directoryPath. onFileAppeared is invoked with the
    /// full path of a newly-created or newly-renamed-into-place file.
    /// onWatcherFaulted is invoked if the underlying watcher errors out
    /// (buffer overflow, directory deleted, etc.) after exhausting its own
    /// bounded internal retry -- the caller should treat the monitor as dead
    /// and fall back to the periodic reconciliation rescan as a backstop.
    /// </summary>
    void Start(string directoryPath, Action<string> onFileAppeared, Action<Exception> onWatcherFaulted);
}
