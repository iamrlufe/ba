using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Worker.Monitoring;
using BackupOrchestrator.Agent.Worker.Tests.Support;
using Microsoft.Extensions.Logging.Abstractions;

namespace BackupOrchestrator.Agent.Worker.Tests.Monitoring;

/// <summary>
/// CpuUsageSampler is the only stateful (non-pure) class under test in this
/// pass, but its collaborators (IProcessSnapshotProvider, IClock) are both
/// fully fake-able, so the ticking/averaging/reset behavior is deterministic
/// and doesn't need real process sampling or wall-clock time.
/// </summary>
public sealed class CpuUsageSamplerTests
{
    private static readonly DateTimeOffset T = new(2026, 8, 11, 12, 0, 0, TimeSpan.Zero);

    /// <summary>
    /// CpuUsageSampler.Sample() calls CpuDeltaCalculator.Compute(..., Environment.ProcessorCount)
    /// directly -- it's not an injectable dependency (see CpuUsageSampler.cs), so
    /// machine-wide-pct expectations in this file are computed against the real
    /// core count of whatever machine runs the suite, matching production
    /// behavior exactly rather than hardcoding a single-core assumption that
    /// would only hold on a 1-core test runner. (Per-core arithmetic itself is
    /// already exhaustively covered, core-count-independent, by
    /// CpuDeltaCalculatorTests in the Core.Tests project.)
    /// </summary>
    private static double ExpectedMachineCpuPct(double busySeconds, double elapsedSeconds) =>
        busySeconds / elapsedSeconds / Environment.ProcessorCount * 100;

    private static ProcessSnapshotItem Item(int pid, string name, TimeSpan cpuTime) =>
        new()
        {
            Pid = pid,
            ProcessName = name,
            TotalProcessorTime = cpuTime,
            WorkingSetBytes = 1024,
        };

    private static CpuUsageSampler CreateSampler(FakeProcessSnapshotProvider provider, TestClock clock) =>
        new(provider, clock, NullLogger<CpuUsageSampler>.Instance);

    [Fact]
    public void TakeAndReset_AfterOnlyTheFirstEverSample_ReturnsNull()
    {
        var provider = new FakeProcessSnapshotProvider();
        var clock = new TestClock(T);
        var sampler = CreateSampler(provider, clock);

        provider.Enqueue([Item(1, "svc", TimeSpan.Zero)]);
        sampler.Sample(); // establishes baseline only -- no delta yet

        var result = sampler.TakeAndReset();

        Assert.Null(result);
    }

    [Fact]
    public void TakeAndReset_TwoSamplesWithClockAdvanced_ReturnsNonNullAveragedSnapshot()
    {
        var provider = new FakeProcessSnapshotProvider();
        var clock = new TestClock(T);
        var sampler = CreateSampler(provider, clock);

        provider.Enqueue([Item(1, "svc", TimeSpan.Zero)]);
        sampler.Sample(); // baseline

        clock.Advance(TimeSpan.FromSeconds(2));
        provider.Enqueue([Item(1, "svc", TimeSpan.FromSeconds(1))]);
        sampler.Sample(); // one real tick: 1s busy / 2s elapsed, divided by real core count

        var result = sampler.TakeAndReset();

        Assert.NotNull(result);
        Assert.Equal(ExpectedMachineCpuPct(busySeconds: 1, elapsedSeconds: 2), result!.MachineCpuUsagePct, precision: 6);
        Assert.Single(result.LatestProcessSamples);
        Assert.Equal("svc", result.LatestProcessSamples[0].ProcessName);
    }

    [Fact]
    public void TakeAndReset_ZeroTicksSinceLastReset_ReturnsNullNotZeroedObject()
    {
        var provider = new FakeProcessSnapshotProvider();
        var clock = new TestClock(T);
        var sampler = CreateSampler(provider, clock);

        // No Sample() calls at all.
        var result = sampler.TakeAndReset();

        Assert.Null(result);
    }

    [Fact]
    public void TakeAndReset_AfterConsumingAccumulator_DoesNotResetDeltaBaseline()
    {
        var provider = new FakeProcessSnapshotProvider();
        var clock = new TestClock(T);
        var sampler = CreateSampler(provider, clock);

        provider.Enqueue([Item(1, "svc", TimeSpan.Zero)]);
        sampler.Sample(); // baseline

        clock.Advance(TimeSpan.FromSeconds(2));
        provider.Enqueue([Item(1, "svc", TimeSpan.FromSeconds(1))]);
        sampler.Sample(); // tick #1

        var first = sampler.TakeAndReset();
        Assert.NotNull(first);

        // If TakeAndReset() incorrectly cleared the delta baseline too, this
        // next Sample() would be treated as a fresh "first-ever" warm-up call
        // (producing no delta) instead of computing against the preserved
        // baseline from tick #1.
        clock.Advance(TimeSpan.FromSeconds(2));
        provider.Enqueue([Item(1, "svc", TimeSpan.FromSeconds(2))]); // +1s busy over +2s elapsed -- same ratio as tick #1

        sampler.Sample();
        var second = sampler.TakeAndReset();

        Assert.NotNull(second);
        Assert.Equal(ExpectedMachineCpuPct(busySeconds: 1, elapsedSeconds: 2), second!.MachineCpuUsagePct, precision: 6);
    }

    [Fact]
    public void TakeAndReset_MultipleTicksBeforeOneReset_ReturnsActualAverageNotJustLastTick()
    {
        var provider = new FakeProcessSnapshotProvider();
        var clock = new TestClock(T);
        var sampler = CreateSampler(provider, clock);

        provider.Enqueue([Item(1, "svc", TimeSpan.Zero)]);
        sampler.Sample(); // baseline

        clock.Advance(TimeSpan.FromSeconds(1));
        provider.Enqueue([Item(1, "svc", TimeSpan.FromSeconds(1))]); // +1s busy / 1s elapsed -- tick #1's ratio
        sampler.Sample();

        clock.Advance(TimeSpan.FromSeconds(1));
        provider.Enqueue([Item(1, "svc", TimeSpan.FromSeconds(1))]); // +0s busy / 1s elapsed -- tick #2's ratio (zero)
        sampler.Sample();

        // Average of [tick1Pct, 0] must be tick1Pct / 2, NOT the last tick's
        // value (0) and NOT tick1Pct unchanged.
        var result = sampler.TakeAndReset();

        var tick1Pct = ExpectedMachineCpuPct(busySeconds: 1, elapsedSeconds: 1);
        Assert.NotNull(result);
        Assert.Equal(tick1Pct / 2, result!.MachineCpuUsagePct, precision: 6);
    }

    [Fact]
    public void TakeAndReset_ClearsAccumulator_SoImmediateSecondCallReturnsNull()
    {
        var provider = new FakeProcessSnapshotProvider();
        var clock = new TestClock(T);
        var sampler = CreateSampler(provider, clock);

        provider.Enqueue([Item(1, "svc", TimeSpan.Zero)]);
        sampler.Sample();

        clock.Advance(TimeSpan.FromSeconds(2));
        provider.Enqueue([Item(1, "svc", TimeSpan.FromSeconds(1))]);
        sampler.Sample();

        var first = sampler.TakeAndReset();
        Assert.NotNull(first);

        var second = sampler.TakeAndReset(); // no new ticks since first reset
        Assert.Null(second);
    }

    [Fact]
    public void Sample_NonPositiveElapsedSinceLastSnapshot_SkippedWithoutMutatingBaselineOrAccumulator()
    {
        var provider = new FakeProcessSnapshotProvider();
        var clock = new TestClock(T);
        var sampler = CreateSampler(provider, clock);

        provider.Enqueue([Item(1, "svc", TimeSpan.Zero)]);
        sampler.Sample(); // baseline at T

        // Clock does NOT advance -- elapsed would be zero.
        provider.Enqueue([Item(1, "svc", TimeSpan.FromSeconds(5))]);
        sampler.Sample(); // must be skipped, not produce a bogus 0-elapsed division

        var result = sampler.TakeAndReset();

        Assert.Null(result);
    }
}
