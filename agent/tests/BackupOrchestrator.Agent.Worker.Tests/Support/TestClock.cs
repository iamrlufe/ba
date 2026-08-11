using BackupOrchestrator.Agent.Core.Contracts;

namespace BackupOrchestrator.Agent.Worker.Tests.Support;

/// <summary>
/// Mutable IClock test double -- advance manually to control elapsed-time-dependent
/// logic deterministically. Direct analogue of
/// BackupOrchestrator.Agent.Core.Tests.Support.TestClock (duplicated here since
/// test projects don't share an InternalsVisibleTo/test-support-library seam
/// in this codebase yet).
/// </summary>
public sealed class TestClock : IClock
{
    public TestClock(DateTimeOffset initialUtcNow) => UtcNow = initialUtcNow;

    public DateTimeOffset UtcNow { get; private set; }

    public void Advance(TimeSpan by) => UtcNow += by;

    public void Set(DateTimeOffset value) => UtcNow = value;
}
