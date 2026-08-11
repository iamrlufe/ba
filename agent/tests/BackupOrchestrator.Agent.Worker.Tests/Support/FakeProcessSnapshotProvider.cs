using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Worker.Tests.Support;

/// <summary>
/// Fully scriptable IProcessSnapshotProvider test double: snapshots are
/// queued and consumed FIFO on each GetSnapshot() call, so a test can
/// deterministically control exactly what CpuUsageSampler.Sample() observes
/// on each tick without touching the real process table.
/// </summary>
public sealed class FakeProcessSnapshotProvider : IProcessSnapshotProvider
{
    private readonly Queue<IReadOnlyList<ProcessSnapshotItem>> _queuedSnapshots = new();

    public void Enqueue(IReadOnlyList<ProcessSnapshotItem> snapshot) => _queuedSnapshots.Enqueue(snapshot);

    public IReadOnlyList<ProcessSnapshotItem> GetSnapshot()
    {
        if (_queuedSnapshots.Count == 0)
        {
            throw new InvalidOperationException(
                "FakeProcessSnapshotProvider: no queued snapshot. Call Enqueue(...) before invoking Sample().");
        }

        return _queuedSnapshots.Dequeue();
    }
}
