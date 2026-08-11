using BackupOrchestrator.Agent.Core.Contracts;

namespace BackupOrchestrator.Agent.Core.Tests.Support;

/// <summary>Mutable IClock test double -- advance manually to control elapsed-time-dependent logic deterministically.</summary>
public sealed class TestClock : IClock
{
    public TestClock(DateTimeOffset initialUtcNow) => UtcNow = initialUtcNow;

    public DateTimeOffset UtcNow { get; private set; }

    public void Advance(TimeSpan by) => UtcNow += by;

    public void Set(DateTimeOffset value) => UtcNow = value;
}
