using BackupOrchestrator.Agent.Core.Contracts;

namespace BackupOrchestrator.Agent.Worker.Backend;

/// <summary>
/// The only IOfflineReplayPacer implementation that performs a real wait --
/// tests inject a recording fake instead. See IOfflineReplayPacer for the
/// full rationale.
/// </summary>
public sealed class TaskDelayOfflineReplayPacer : IOfflineReplayPacer
{
    public Task PauseAsync(TimeSpan duration, CancellationToken cancellationToken) =>
        Task.Delay(duration, cancellationToken);
}
