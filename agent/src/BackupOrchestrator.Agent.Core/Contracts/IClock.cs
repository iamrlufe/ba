namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>Testability seam over DateTimeOffset.UtcNow.</summary>
public interface IClock
{
    DateTimeOffset UtcNow { get; }
}

public sealed class SystemClock : IClock
{
    public DateTimeOffset UtcNow => DateTimeOffset.UtcNow;
}
