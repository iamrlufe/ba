using BackupOrchestrator.Agent.Core.Contracts;

namespace BackupOrchestrator.Agent.Worker.Watch;

/// <summary>
/// The only IWatchDirectoryMonitor implementation that touches
/// System.IO.FileSystemWatcher. NotifyFilters.FileName ONLY (deliberately
/// not LastWrite/Size -- readiness is decided by lock-check/msdb polling,
/// not watcher-reported writes; this also keeps event volume low, avoiding
/// InternalBufferSize overflow risk). Subscribes both Created and Renamed
/// (some backup tools write to a temp name and rename on completion -- the
/// rename event is the only signal for that case).
/// </summary>
public sealed class FileSystemWatchDirectoryMonitor : IWatchDirectoryMonitor
{
    private const int MaxImmediateRetries = 3;
    private static readonly TimeSpan RetryDelay = TimeSpan.FromSeconds(2);

    private readonly ILogger<FileSystemWatchDirectoryMonitor>? _logger;
    private readonly object _gate = new();

    private string? _directoryPath;
    private Action<string>? _onFileAppeared;
    private Action<Exception>? _onWatcherFaulted;
    private FileSystemWatcher? _watcher;
    private int _immediateRetryCount;
    private bool _disposed;

    public FileSystemWatchDirectoryMonitor()
    {
    }

    public FileSystemWatchDirectoryMonitor(ILogger<FileSystemWatchDirectoryMonitor> logger)
    {
        _logger = logger;
    }

    public void Start(string directoryPath, Action<string> onFileAppeared, Action<Exception> onWatcherFaulted)
    {
        lock (_gate)
        {
            _directoryPath = directoryPath;
            _onFileAppeared = onFileAppeared;
            _onWatcherFaulted = onWatcherFaulted;
            _immediateRetryCount = 0;
            CreateAndAttachWatcher();
        }
    }

    private void CreateAndAttachWatcher()
    {
        var watcher = new FileSystemWatcher(_directoryPath!)
        {
            NotifyFilter = NotifyFilters.FileName,
            Filter = "*",
            IncludeSubdirectories = false,
        };

        watcher.Created += OnCreatedOrRenamed;
        watcher.Renamed += OnCreatedOrRenamed;
        watcher.Error += OnError;
        watcher.EnableRaisingEvents = true;

        _watcher = watcher;
    }

    private void OnCreatedOrRenamed(object sender, FileSystemEventArgs e)
    {
        try
        {
            _onFileAppeared?.Invoke(e.FullPath);
        }
        catch (Exception ex)
        {
            // Defensive only -- an unhandled exception from a
            // FileSystemWatcher event handler would otherwise be swallowed
            // silently by the framework, which is worse than logging it.
            _logger?.LogError(ex, "Unhandled exception in WATCH file-appeared callback for {FullPath}", e.FullPath);
        }
    }

    private void OnError(object sender, ErrorEventArgs e)
    {
        var exception = e.GetException();
        _logger?.LogWarning(exception, "FileSystemWatcher error on {DirectoryPath}", _directoryPath);

        lock (_gate)
        {
            if (_disposed)
            {
                return;
            }

            DetachAndDisposeWatcher();

            if (_immediateRetryCount >= MaxImmediateRetries)
            {
                _logger?.LogWarning(
                    "FileSystemWatcher for {DirectoryPath} exhausted {MaxRetries} immediate retries; " +
                    "relying on periodic reconciliation rescan as backstop", _directoryPath, MaxImmediateRetries);
                _onWatcherFaulted?.Invoke(exception);
                return;
            }

            _immediateRetryCount++;
        }

        Thread.Sleep(RetryDelay);

        lock (_gate)
        {
            if (_disposed)
            {
                return;
            }

            try
            {
                CreateAndAttachWatcher();
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex, "Failed to recreate FileSystemWatcher for {DirectoryPath}", _directoryPath);
                _onWatcherFaulted?.Invoke(ex);
            }
        }
    }

    private void DetachAndDisposeWatcher()
    {
        if (_watcher is null)
        {
            return;
        }

        _watcher.EnableRaisingEvents = false;
        _watcher.Created -= OnCreatedOrRenamed;
        _watcher.Renamed -= OnCreatedOrRenamed;
        _watcher.Error -= OnError;
        _watcher.Dispose();
        _watcher = null;
    }

    public void Dispose()
    {
        lock (_gate)
        {
            _disposed = true;
            DetachAndDisposeWatcher();
        }
    }
}
