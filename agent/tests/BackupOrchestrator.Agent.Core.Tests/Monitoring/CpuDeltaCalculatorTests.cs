using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Monitoring;

namespace BackupOrchestrator.Agent.Core.Tests.Monitoring;

/// <summary>
/// CpuDeltaCalculator.Compute is pure -- no clock, no process table -- so
/// every case here is driven by synthetic ProcessSnapshotItem pairs with
/// known TotalProcessorTime deltas and a known elapsed/processorCount.
/// </summary>
public sealed class CpuDeltaCalculatorTests
{
    private static ProcessSnapshotItem Item(int pid, string name, TimeSpan cpuTime, long workingSetBytes = 1024) =>
        new()
        {
            Pid = pid,
            ProcessName = name,
            TotalProcessorTime = cpuTime,
            WorkingSetBytes = workingSetBytes,
        };

    [Fact]
    public void Compute_KnownDeltaSingleCore_ProducesExactMachineWideCpuPct()
    {
        // 1 process, 1 core, 1s busy-time delta over a 2s elapsed window ->
        // 1 / (2 * 1) * 100 = 50%.
        var previous = new[] { Item(1, "svc", TimeSpan.FromSeconds(0)) };
        var current = new[] { Item(1, "svc", TimeSpan.FromSeconds(1)) };

        var (machineCpuPct, perProcess) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(2), processorCount: 1);

        Assert.Equal(50.0, machineCpuPct, precision: 6);
        Assert.Single(perProcess);
        Assert.Equal(50.0, perProcess[0].CpuPct, precision: 6);
    }

    [Fact]
    public void Compute_KnownDeltaMultiCore_DividesByProcessorCount()
    {
        // 2 processes each contributing 1s busy-time delta (2s total) over a
        // 2s elapsed window on a 4-core box -> 2 / (2 * 4) * 100 = 25%.
        var previous = new[]
        {
            Item(1, "a", TimeSpan.Zero),
            Item(2, "b", TimeSpan.Zero),
        };
        var current = new[]
        {
            Item(1, "a", TimeSpan.FromSeconds(1)),
            Item(2, "b", TimeSpan.FromSeconds(1)),
        };

        var (machineCpuPct, _) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(2), processorCount: 4);

        Assert.Equal(25.0, machineCpuPct, precision: 6);
    }

    [Fact]
    public void Compute_DeltaImpliesOver100Percent_ClampsMachineWideTo100()
    {
        // 10s busy-time delta over a 1s elapsed window on 1 core is
        // physically impossible but must clamp, not overflow/underflow.
        var previous = new[] { Item(1, "svc", TimeSpan.Zero) };
        var current = new[] { Item(1, "svc", TimeSpan.FromSeconds(10)) };

        var (machineCpuPct, _) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(1), processorCount: 1);

        Assert.Equal(100.0, machineCpuPct);
    }

    [Theory]
    [InlineData("Idle")]
    [InlineData("idle")]
    [InlineData("IDLE")]
    public void Compute_IdleProcess_ExcludedFromMachineWideSumButStillProducesPerProcessResult(string idleName)
    {
        // Idle contributes a huge delta -- if it were included in the
        // machine-wide sum it would dominate; the assertion below (0%)
        // proves it was excluded, since no other process has any delta.
        var previous = new[] { Item(1, idleName, TimeSpan.Zero) };
        var current = new[] { Item(1, idleName, TimeSpan.FromSeconds(2)) };

        var (machineCpuPct, perProcess) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(2), processorCount: 1);

        Assert.Equal(0.0, machineCpuPct);
        Assert.Single(perProcess);
        Assert.Equal(idleName, perProcess[0].ProcessName);
        Assert.Equal(100.0, perProcess[0].CpuPct, precision: 6);
    }

    [Fact]
    public void Compute_ProcessPresentInOnlyOneSnapshot_ProducesNoPerProcessResultForIt()
    {
        // pid 1 present in both; pid 2 only in "current" (started between
        // samples); pid 3 only in "previous" (exited between samples).
        var previous = new[]
        {
            Item(1, "steady", TimeSpan.Zero),
            Item(3, "exited", TimeSpan.FromSeconds(5)),
        };
        var current = new[]
        {
            Item(1, "steady", TimeSpan.FromSeconds(1)),
            Item(2, "started", TimeSpan.FromSeconds(1)),
        };

        var (_, perProcess) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(2), processorCount: 1);

        Assert.Single(perProcess);
        Assert.Equal("steady", perProcess[0].ProcessName);
        Assert.DoesNotContain(perProcess, p => p.ProcessName is "started" or "exited");
    }

    [Fact]
    public void Compute_SingleProcessDeltaOver100Percent_PerProcessResultIsNotClamped()
    {
        // A single process accumulating 4s of busy-time over a 1s elapsed
        // window (plausible on a multi-core box) -> 400%, not clamped,
        // unlike the machine-wide result.
        var previous = new[] { Item(1, "hog", TimeSpan.Zero) };
        var current = new[] { Item(1, "hog", TimeSpan.FromSeconds(4)) };

        var (machineCpuPct, perProcess) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(1), processorCount: 4);

        Assert.Equal(100.0, machineCpuPct); // machine-wide clamped: 4/(1*4)*100 = 100 exactly here
        Assert.Single(perProcess);
        Assert.Equal(400.0, perProcess[0].CpuPct, precision: 6); // per-process NOT divided by core count, NOT clamped
    }

    [Fact]
    public void Compute_SingleProcessDeltaFarOver100Percent_PerProcessResultExceeds100EvenWhenMachineWideClamped()
    {
        var previous = new[] { Item(1, "hog", TimeSpan.Zero) };
        var current = new[] { Item(1, "hog", TimeSpan.FromSeconds(20)) };

        var (machineCpuPct, perProcess) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(1), processorCount: 4);

        Assert.Equal(100.0, machineCpuPct); // clamped: 20/(1*4)*100 = 500 -> clamp to 100
        Assert.Equal(2000.0, perProcess[0].CpuPct, precision: 6); // per-process unbounded
        Assert.True(perProcess[0].CpuPct > 100.0);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Compute_NonPositiveElapsed_ReturnsSafeEmptyZeroResult(int elapsedSeconds)
    {
        var previous = new[] { Item(1, "svc", TimeSpan.Zero) };
        var current = new[] { Item(1, "svc", TimeSpan.FromSeconds(1)) };

        var (machineCpuPct, perProcess) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(elapsedSeconds), processorCount: 1);

        Assert.Equal(0.0, machineCpuPct);
        Assert.Empty(perProcess);
    }

    [Fact]
    public void Compute_NegativeProcessorTimeDelta_ClampedToZeroNotNegative()
    {
        // Simulates a pid-reuse edge case where "current" time is somehow
        // less than "previous" -- must not produce a negative CPU% for that
        // process nor subtract from the machine-wide sum.
        var previous = new[] { Item(1, "svc", TimeSpan.FromSeconds(5)) };
        var current = new[] { Item(1, "svc", TimeSpan.FromSeconds(1)) };

        var (machineCpuPct, perProcess) = CpuDeltaCalculator.Compute(
            previous, current, TimeSpan.FromSeconds(2), processorCount: 1);

        Assert.Equal(0.0, machineCpuPct);
        Assert.Equal(0.0, perProcess[0].CpuPct, precision: 6);
    }
}
