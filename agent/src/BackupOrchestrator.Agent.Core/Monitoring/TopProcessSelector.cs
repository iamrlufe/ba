using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Monitoring;

/// <summary>Pure.</summary>
public static class TopProcessSelector
{
    /// <summary>
    /// top-5-by-CpuPct union top-5-by-MemoryBytes, deduplicated by
    /// ProcessName (when a name collides, keeps the instance with the
    /// higher CpuPct), capped at 10 total.
    /// </summary>
    public static IReadOnlyList<TopProcessItem> Select(IReadOnlyList<TopProcessItem> candidates)
    {
        var topByCpu = candidates.OrderByDescending(p => p.CpuPct).Take(5);
        var topByMemory = candidates.OrderByDescending(p => p.MemoryBytes).Take(5);

        return topByCpu
            .Concat(topByMemory)
            .GroupBy(p => p.ProcessName, StringComparer.Ordinal)
            .Select(g => g.OrderByDescending(x => x.CpuPct).First())
            .Take(10)
            .ToList();
    }
}
