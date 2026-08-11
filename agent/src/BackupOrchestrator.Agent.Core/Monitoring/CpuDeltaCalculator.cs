using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Monitoring;

/// <summary>Pure, no I/O -- fully unit-testable.</summary>
public static class CpuDeltaCalculator
{
    /// <summary>
    /// Computes machine-wide CPU% and per-process CPU% from two process
    /// snapshots taken <paramref name="elapsed"/> apart. Excludes any entry
    /// whose ProcessName equals "Idle" (ordinal, case-insensitive) from the
    /// machine-wide busy-time sum. Machine-wide result clamped to [0,100].
    /// Per-process results are NOT divided by core count and NOT clamped
    /// (can exceed 100 on a multi-core box -- standard OS convention).
    /// Only processes present in BOTH snapshots (matched by Pid) contribute
    /// a per-process result; processes present in only one snapshot are
    /// silently excluded (process started/exited between samples).
    /// </summary>
    public static (double MachineCpuPct, IReadOnlyList<TopProcessItem> PerProcess) Compute(
        IReadOnlyList<ProcessSnapshotItem> previous,
        IReadOnlyList<ProcessSnapshotItem> current,
        TimeSpan elapsed,
        int processorCount)
    {
        if (elapsed <= TimeSpan.Zero)
        {
            return (0.0, []);
        }

        var previousByPid = new Dictionary<int, ProcessSnapshotItem>();
        foreach (var item in previous)
        {
            previousByPid[item.Pid] = item;
        }

        var totalBusySeconds = 0.0;
        var perProcess = new List<TopProcessItem>();

        foreach (var currentItem in current)
        {
            if (!previousByPid.TryGetValue(currentItem.Pid, out var previousItem))
            {
                continue;
            }

            var delta = currentItem.TotalProcessorTime - previousItem.TotalProcessorTime;
            if (delta < TimeSpan.Zero)
            {
                delta = TimeSpan.Zero;
            }

            if (!string.Equals(currentItem.ProcessName, "Idle", StringComparison.OrdinalIgnoreCase))
            {
                totalBusySeconds += delta.TotalSeconds;
            }

            var cpuPct = delta.TotalSeconds / elapsed.TotalSeconds * 100;
            perProcess.Add(new TopProcessItem
            {
                ProcessName = currentItem.ProcessName,
                Pid = currentItem.Pid,
                CpuPct = cpuPct,
                MemoryBytes = currentItem.WorkingSetBytes,
            });
        }

        var machineCpuPct = totalBusySeconds / (elapsed.TotalSeconds * processorCount) * 100;
        machineCpuPct = Math.Clamp(machineCpuPct, 0.0, 100.0);

        return (machineCpuPct, perProcess);
    }
}
