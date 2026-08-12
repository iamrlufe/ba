namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// Testability seam over the wait used by OfflineReplayHostedService between
/// batches (within one pass) and between passes (escalating backoff).
/// TaskDelayOfflineReplayPacer (Worker) is the only implementation that
/// performs a real wait; tests inject a recording fake that completes
/// immediately while capturing every requested duration, so a simulated
/// backlog of thousands of events can be replayed in a test in milliseconds
/// instead of real wall-clock minutes.
/// </summary>
public interface IOfflineReplayPacer
{
    Task PauseAsync(TimeSpan duration, CancellationToken cancellationToken);
}
