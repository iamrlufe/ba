using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// The seam over WinSCP. WinScpTransferClient (Worker project) is the only
/// implementation that touches WinSCP.Session; business logic (scheduler,
/// watchdog) depends on this interface only.
///
/// Implementations MUST guarantee the underlying transfer session is
/// disposed on every exit path, including the CancellationToken being
/// cancelled by the watchdog mid-transfer (SchedulerHostedService links a
/// CancellationTokenSource.CancelAfter(...) for this purpose).
/// </summary>
public interface IBackupTransferClient
{
    Task<TransferResult> TransferAsync(
        TransferRequest request,
        IProgress<TransferProgress> progress,
        CancellationToken cancellationToken);
}
