using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Core.Transfer;
using WinSCP;

namespace BackupOrchestrator.Agent.Worker.Transfer;

/// <summary>
/// The only IBackupTransferClient implementation that touches WinSCP.Session.
/// Not unit-tested here (per the spec's testability boundary) -- exercising a
/// real FTP/SFTP session requires a live server and, practically, a Windows
/// host, neither of which are available in this sandbox. Reviewed for
/// correctness against the WinSCP .NET assembly's documented API surface and
/// compiled clean, but the actual transfer path is unverified until run on a
/// real Windows machine against a real server.
///
/// Session lifetime: `session` is always disposed via try/finally, on every
/// exit path including an exception thrown mid-transfer AND a forced
/// watchdog cancellation (SchedulerHostedService links a
/// CancellationTokenSource.CancelAfter(...) into the token passed here; this
/// class reacts to it via a `cancellationToken.Register(() => session.Abort())`
/// callback so a hung WinSCP.com process is forcibly killed rather than
/// leaking).
/// </summary>
public sealed class WinScpTransferClient : IBackupTransferClient
{
    private readonly ILogger<WinScpTransferClient> _logger;
    private readonly IClock _clock;

    public WinScpTransferClient(ILogger<WinScpTransferClient> logger, IClock clock)
    {
        _logger = logger;
        _clock = clock;
    }

    public async Task<TransferResult> TransferAsync(
        TransferRequest request, IProgress<TransferProgress> progress, CancellationToken cancellationToken)
    {
        Session? session = null;

        try
        {
            var sessionOptions = BuildSessionOptions(request.ConnectionConfig);
            session = new Session();

            // Belt-and-suspenders forced abort: the FileTransferProgress
            // handler below also checks the token and sets e.Cancel, but
            // that only fires once a transfer is actually in flight and
            // WinSCP is polling it. Session.Abort() forcibly kills the
            // underlying WinSCP.com process even if cancellation arrives
            // during connect/handshake, before any progress event exists.
            using var cancelRegistration = cancellationToken.Register(() =>
            {
                try
                {
                    session.Abort();
                }
                catch (Exception ex)
                {
                    _logger.LogDebug(ex, "session.Abort() threw during cancellation (session may already be closed)");
                }
            });

            var localFileSizeBytes = new FileInfo(request.LocalSourcePath).Length;

            var throttler = new ProgressThrottler(_clock);
            session.FileTransferProgress += (_, e) =>
            {
                if (cancellationToken.IsCancellationRequested)
                {
                    e.Cancel = true;
                    return;
                }

                var percent = (int)Math.Round(e.OverallProgress * 100);
                var sample = new TransferProgress
                {
                    PercentComplete = percent,
                    BytesTransferred = (long)(e.OverallProgress * localFileSizeBytes),
                    TotalBytes = localFileSizeBytes,
                    CurrentFileName = e.FileName,
                };

                if (throttler.ShouldReport(sample))
                {
                    progress.Report(sample);
                }
            };

            var remoteFullPath = RemotePathBuilder.CombineRemotePath(request.RemoteDirectory, request.RemoteFileName);

            await Task.Run(
                () =>
                {
                    session.Open(sessionOptions);
                    EnsureRemoteDirectoryExists(session, request.RemoteDirectory);

                    var transferOptions = new TransferOptions { TransferMode = TransferMode.Binary };
                    var result = session.PutFiles(request.LocalSourcePath, remoteFullPath, remove: false, options: transferOptions);
                    result.Check();
                },
                cancellationToken);

            cancellationToken.ThrowIfCancellationRequested();

            var checksum = await Sha256Hasher.ComputeHexHashAsync(request.LocalSourcePath, cancellationToken);
            var fileInfo = new FileInfo(request.LocalSourcePath);

            return new TransferResult
            {
                Success = true,
                Status = JobRunStatus.SUCCESS,
                RemotePath = remoteFullPath,
                FileSizeBytes = fileInfo.Length,
                Sha256Checksum = checksum,
            };
        }
        catch (OperationCanceledException)
        {
            _logger.LogWarning(
                "Transfer for job run {JobRunId} was cancelled (watchdog timeout or shutdown)", request.JobRunId);
            return new TransferResult
            {
                Success = false,
                Status = JobRunStatus.TIMEOUT,
                ErrorMessage = "Transfer cancelled by watchdog timeout or shutdown",
            };
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Transfer for job run {JobRunId} failed", request.JobRunId);
            return new TransferResult
            {
                Success = false,
                Status = JobRunStatus.FAILED,
                ErrorMessage = ex.Message,
            };
        }
        finally
        {
            session?.Dispose();
        }
    }

    /// <summary>WinSCP's CreateDirectory is not recursive -- create each path segment in order, tolerating "already exists".</summary>
    private static void EnsureRemoteDirectoryExists(Session session, string remoteDirectory)
    {
        var segments = remoteDirectory.Trim('/').Split('/', StringSplitOptions.RemoveEmptyEntries);
        var current = string.Empty;
        foreach (var segment in segments)
        {
            current += "/" + segment;
            if (session.FileExists(current))
            {
                continue;
            }

            try
            {
                session.CreateDirectory(current);
            }
            catch (SessionRemoteException) when (session.FileExists(current))
            {
                // Lost a race with a concurrent creator (or the server
                // reports existence oddly) -- fine, the directory is there.
            }
        }
    }

    /// <summary>
    /// NOT PRODUCTION SAFE for SFTP: GiveUpSecurityAndAcceptAnySshHostKey is
    /// used because the backend's ConnectionConfigDto carries no host-key
    /// fingerprint yet (nothing to pin against). This mirrors the
    /// already-documented "connection-config is not production-safe yet"
    /// caveat (see AgentOptions.ConnectionConfigKey) -- a real host-key
    /// pinning story is future work, not something this task can invent
    /// unilaterally.
    /// </summary>
    private static SessionOptions BuildSessionOptions(ConnectionConfigDto config)
    {
        var options = new SessionOptions
        {
            HostName = config.Host,
            PortNumber = config.Port,
            UserName = config.Username,
            Password = config.Password,
        };

        // Case-sensitive match against the backend's ProtocolType values
        // ("FTP" / "SFTP", always upper-case per app/models/enums.py).
        if (string.Equals(config.Protocol, "SFTP", StringComparison.Ordinal))
        {
            options.Protocol = Protocol.Sftp;
            options.SshHostKeyPolicy = SshHostKeyPolicy.GiveUpSecurityAndAcceptAny;

            // SessionOptions.SshPrivateKey accepts key content directly (not
            // just a file path) -- no temp file needed, so the decrypted key
            // material never touches disk on this end either.
            if (!string.IsNullOrEmpty(config.SshPrivateKey))
            {
                options.SshPrivateKey = config.SshPrivateKey;
            }
        }
        else
        {
            options.Protocol = Protocol.Ftp;
            options.FtpSecure = FtpSecure.None;
        }

        return options;
    }
}
