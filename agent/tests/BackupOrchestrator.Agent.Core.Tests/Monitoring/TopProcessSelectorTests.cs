using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Monitoring;

namespace BackupOrchestrator.Agent.Core.Tests.Monitoring;

/// <summary>
/// TopProcessSelector.Select is pure: top-5-by-CpuPct union top-5-by-MemoryBytes,
/// deduplicated by ProcessName (higher CpuPct wins on collision), capped at 10.
/// </summary>
public sealed class TopProcessSelectorTests
{
    private static TopProcessItem Item(string name, double cpuPct, long memoryBytes, int pid = 1) =>
        new()
        {
            ProcessName = name,
            Pid = pid,
            CpuPct = cpuPct,
            MemoryBytes = memoryBytes,
        };

    [Fact]
    public void Select_FewerThanFiveCandidates_ReturnsAllWithoutCrashing()
    {
        var candidates = new[]
        {
            Item("a", cpuPct: 10, memoryBytes: 100),
            Item("b", cpuPct: 20, memoryBytes: 200),
            Item("c", cpuPct: 30, memoryBytes: 300),
        };

        var result = TopProcessSelector.Select(candidates);

        Assert.Equal(3, result.Count);
        Assert.Equal(["c", "b", "a"], result.Select(p => p.ProcessName).ToArray());
    }

    [Fact]
    public void Select_EmptyCandidates_ReturnsEmpty()
    {
        var result = TopProcessSelector.Select([]);

        Assert.Empty(result);
    }

    [Fact]
    public void Select_TopByCpuButNotByMemory_AppearsInUnion()
    {
        // "cpuHog" is #1 by CPU but has trivial memory, so it would be
        // excluded from a memory-only top-5. "memHog" is the reverse.
        // Padding entries keep both out of each other's top-5 on the other axis.
        var candidates = new List<TopProcessItem>
        {
            Item("cpuHog", cpuPct: 999, memoryBytes: 1),
            Item("memHog", cpuPct: 0.1, memoryBytes: 999_999),
        };
        for (var i = 0; i < 5; i++)
        {
            candidates.Add(Item($"padding{i}", cpuPct: 50 + i, memoryBytes: 50_000 + i));
        }

        var result = TopProcessSelector.Select(candidates);

        Assert.Contains(result, p => p.ProcessName == "cpuHog");
        Assert.Contains(result, p => p.ProcessName == "memHog");
    }

    [Fact]
    public void Select_DuplicateProcessNameDifferentCpuPct_KeepsOnlyTheHigherCpuPctInstance()
    {
        // Same ProcessName appears twice (e.g. once in the top-CPU ranking
        // sample and once distinctly in the top-memory ranking sample, or
        // simply two literal candidates sharing a name) with two different
        // CpuPct values -- the dedup rule must keep the higher one and its
        // associated MemoryBytes, not the lower one, and not merge/average them.
        var candidates = new[]
        {
            Item("dup", cpuPct: 10, memoryBytes: 100, pid: 1),
            Item("dup", cpuPct: 90, memoryBytes: 50, pid: 2),
        };

        var result = TopProcessSelector.Select(candidates);

        var dup = Assert.Single(result, p => p.ProcessName == "dup");
        Assert.Equal(90, dup.CpuPct);
        Assert.Equal(50, dup.MemoryBytes);
        Assert.Equal(2, dup.Pid);
    }

    [Fact]
    public void Select_MoreThanTenUniqueCandidates_CapsResultAtExactlyTen()
    {
        // 8 distinct top-by-CPU entries and 8 distinct top-by-memory entries
        // (no name overlap) -- union would be 16 without the cap.
        var candidates = new List<TopProcessItem>();
        for (var i = 0; i < 8; i++)
        {
            candidates.Add(Item($"cpu{i}", cpuPct: 100 - i, memoryBytes: 1));
        }
        for (var i = 0; i < 8; i++)
        {
            candidates.Add(Item($"mem{i}", cpuPct: 0, memoryBytes: 1_000_000 - i));
        }

        var result = TopProcessSelector.Select(candidates);

        Assert.Equal(10, result.Count);
    }

    [Fact]
    public void Select_ResultOrderedByCpuPctDescending()
    {
        var candidates = new[]
        {
            Item("low", cpuPct: 1, memoryBytes: 10),
            Item("high", cpuPct: 99, memoryBytes: 10),
            Item("mid", cpuPct: 50, memoryBytes: 10),
        };

        var result = TopProcessSelector.Select(candidates);

        Assert.Equal(["high", "mid", "low"], result.Select(p => p.ProcessName).ToArray());
    }
}
