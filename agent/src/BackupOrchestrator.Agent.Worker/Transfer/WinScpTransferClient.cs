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
/// watchdog/operator-cancel cancellation (BackupRunPipeline links a
/// CancellationTokenSource.CancelAfter(...) watchdog and a dedicated
/// operator-cancel source into the token passed here; this class reacts to
/// it via a `cancellationToken.Register(() => session.Abort())` callback so a
/// hung WinSCP.com process is forcibly killed rather than leaking) -- this
/// applies per-attempt, across the mid-transfer disconnect retry loop below.
///
/// Resumable transfer: before every PutFiles call, the destination's
/// existence/size is checked and compared against the local file (see
/// TransferPlanCalculator, a pure/unit-tested decision in Core) to decide
/// between a full upload, a WinSCP-native resume, skipping the transfer
/// entirely (destination already matches), or forcing a full overwrite (an
/// anomalous destination LARGER than the source). A mid-transfer disconnect
/// is retried (bounded, MaxTransferAttempts) by re-running this whole
/// decision from scratch on a fresh Session -- self-healing, since a failed
/// partial attempt naturally leaves partial bytes on the remote that the next
/// attempt's own plan check will see and resume from.
/// </summary>
public sealed class WinScpTransferClient : IBackupTransferClient
{
    /// <summary>Hard ceiling on mid-transfer disconnect retries -- never unbounded.</summary>
    private const int MaxTransferAttempts = 3;

    private static readonly TimeSpan RetryDelay = TimeSpan.FromSeconds(15);

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
        var localFileSizeBytes = new FileInfo(request.LocalSourcePath).Length;
        var remoteFullPath = RemotePathBuilder.CombineRemotePath(request.RemoteDirectory, request.RemoteFileName);

        // Created ONCE outside the attempt loop so its throttling state
        // persists across a resume rather than resetting per attempt.
        var throttler = new ProgressThrottler(_clock);

        for (var attempt = 1; attempt <= MaxTransferAttempts; attempt++)
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
                // Re-registered per attempt against each fresh Session.
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

                // Re-attached per attempt against each fresh Session -- the
                // throttler instance itself (its rate-limiting state) is
                // shared across attempts, see above.
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

                await Task.Run(
                    () =>
                    {
                        session.Open(sessionOptions);
                        EnsureRemoteDirectoryExists(session, request.RemoteDirectory);

                        var remoteExists = session.FileExists(remoteFullPath);
                        var remoteSizeBytes = remoteExists ? session.GetFileInfo(remoteFullPath).Length : (long?)null;
                        var plan = TransferPlanCalculator.Determine(remoteExists, remoteSizeBytes, localFileSizeBytes);

                        switch (plan)
                        {
                            case TransferPlan.Full:
                            case TransferPlan.Resume:
                                // The resume-vs-full decision was already made
                                // explicitly above -- State = On (not Smart,
                                // whose own size-threshold heuristic would be
                                // redundant and can't express OverwriteAnomaly's
                                // required Off).
                                var resumeOptions = new TransferOptions
                                {
                                    TransferMode = TransferMode.Binary,
                                    ResumeSupport = { State = TransferResumeSupportState.On },
                                };
                                session.PutFiles(request.LocalSourcePath, remoteFullPath, remove: false, options: resumeOptions).Check();
                                break;

                            case TransferPlan.OverwriteAnomaly:
                                _logger.LogWarning(
                                    "Remote file {RemotePath} ({RemoteSizeBytes} bytes) is larger than local source " +
                                    "{LocalPath} ({LocalSizeBytes} bytes); forcing a full overwrite instead of a resume",
                                    remoteFullPath, remoteSizeBytes, request.LocalSourcePath, localFileSizeBytes);
                                var overwriteOptions = new TransferOptions
                                {
                                    TransferMode = TransferMode.Binary,
                                    ResumeSupport = { State = TransferResumeSupportState.Off },
                                };
                                session.PutFiles(request.LocalSourcePath, remoteFullPath, remove: false, options: overwriteOptions).Check();
                                break;

                            case TransferPlan.Skip:
                                _logger.LogInformation(
                                    "Remote file {RemotePath} already matches local source size ({SizeBytes} bytes); skipping transfer",
                                    remoteFullPath, localFileSizeBytes);
                                break;
                        }
                    },
                    cancellationToken);

                cancellationToken.ThrowIfCancellationRequested();

                // TODO: no full remote-content SHA-256 verification after a
                // resume or skip is performed here -- same-size doesn't
                // guarantee byte-identical content after a resume, and a skip
                // performs zero verification at all. There is no pre-existing
                // expected checksum to verify against for a fresh backup
                // (the backend's BackupRecord.checksum is only ever SET by
                // the agent post-transfer, nothing to compare against) --
                // Session.CalculateFileChecksum is the likely mechanism for a
                // future follow-up. Out of scope for this task; do not
                // implement now.
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
                // Checked BEFORE the generic-exception retry branch below --
                // catch-clause ordering matters here: a watchdog/operator/
                // shutdown cancellation must return immediately, never retry.
                _logger.LogWarning(
                    "Transfer for job run {JobRunId} was cancelled (watchdog timeout, operator cancel, or shutdown)", request.JobRunId);
                return new TransferResult
                {
                    Success = false,
                    Status = JobRunStatus.TIMEOUT,
                    ErrorMessage = "Transfer cancelled by watchdog timeout, operator cancel, or shutdown",
                };
            }
            catch (Exception ex)
            {
                session?.Dispose();
                session = null;

                if (attempt >= MaxTransferAttempts)
                {
                    _logger.LogError(
                        ex, "Transfer for job run {JobRunId} failed after {Attempts} attempts", request.JobRunId, MaxTransferAttempts);
                    return new TransferResult
                    {
                        Success = false,
                        Status = JobRunStatus.FAILED,
                        ErrorMessage = $"Transfer failed after {MaxTransferAttempts} attempts: {ex.Message}",
                    };
                }

                _logger.LogWarning(
                    ex, "Transfer attempt {Attempt}/{MaxAttempts} for job run {JobRunId} failed; retrying in {DelaySeconds:F0}s " +
                    "(a fresh attempt will re-check remote state and resume from partial bytes if any were written)",
                    attempt, MaxTransferAttempts, request.JobRunId, RetryDelay.TotalSeconds);

                try
                {
                    await Task.Delay(RetryDelay, cancellationToken);
                }
                catch (OperationCanceledException)
                {
                    _logger.LogWarning("Transfer for job run {JobRunId} was cancelled while waiting to retry", request.JobRunId);
                    return new TransferResult
                    {
                        Success = false,
                        Status = JobRunStatus.TIMEOUT,
                        ErrorMessage = "Transfer cancelled by watchdog timeout, operator cancel, or shutdown",
                    };
                }
            }
            finally
            {
                session?.Dispose();
            }
        }

        // Unreachable: every loop iteration either returns (success, a
        // cancellation, a retry-wait cancellation, or the final attempt's
        // failure) or falls through to the next iteration after a delayed
        // retry. Present only to satisfy the compiler.
        throw new InvalidOperationException("Unreachable: transfer retry loop exited without returning a result");
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
