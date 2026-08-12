using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Scheduling;

namespace BackupOrchestrator.Agent.Core.Tests.Scheduling;

public sealed class WatchCandidateComparerTests
{
    private static readonly DateTimeOffset T = new(2026, 8, 11, 12, 0, 0, TimeSpan.Zero);

    private static WatchCandidateFile Candidate(
        DateTimeOffset orderingTimestampUtc, string fileName, int backupJobId = 1) =>
        new()
        {
            BackupJobId = backupJobId,
            LocalFilePath = $@"C:\watch\{fileName}",
            OrderingTimestampUtc = orderingTimestampUtc,
            DetectionMethod = WatchDetectionMethod.LockCheck,
            FileSizeBytes = 1024,
        };

    [Fact]
    public void IsMoreRecent_CandidateTimestampNewer_ReturnsTrue()
    {
        var candidate = Candidate(T.AddMinutes(5), "b.bak");
        var existing = Candidate(T, "a.bak");

        Assert.True(WatchCandidateComparer.IsMoreRecent(candidate, existing));
    }

    [Fact]
    public void IsMoreRecent_CandidateTimestampOlder_ReturnsFalse_EvenIfFileNameWouldWinTiebreak()
    {
        // Candidate's file name would win a lexicographic tiebreak ("z" > "a"),
        // but the timestamp comparison must take priority since they differ.
        var candidate = Candidate(T, "z.bak");
        var existing = Candidate(T.AddMinutes(5), "a.bak");

        Assert.False(WatchCandidateComparer.IsMoreRecent(candidate, existing));
    }

    [Fact]
    public void IsMoreRecent_EqualTimestamps_LexicographicallyLaterFileNameWins()
    {
        var candidate = Candidate(T, "backup_b.bak");
        var existing = Candidate(T, "backup_a.bak");

        Assert.True(WatchCandidateComparer.IsMoreRecent(candidate, existing));
    }

    [Fact]
    public void IsMoreRecent_EqualTimestamps_LexicographicallyEarlierFileName_ReturnsFalse()
    {
        var candidate = Candidate(T, "backup_a.bak");
        var existing = Candidate(T, "backup_b.bak");

        Assert.False(WatchCandidateComparer.IsMoreRecent(candidate, existing));
    }

    [Fact]
    public void IsMoreRecent_EqualTimestampAndIdenticalFileName_DoesNotThrowAndReturnsFalse()
    {
        var candidate = Candidate(T, "same.bak");
        var existing = Candidate(T, "same.bak");

        var result = Record.Exception(() => WatchCandidateComparer.IsMoreRecent(candidate, existing));

        Assert.Null(result);
        Assert.False(WatchCandidateComparer.IsMoreRecent(candidate, existing));
    }

    [Fact]
    public void IsMoreRecent_EqualTimestamps_FileNameComparisonIsOrdinal_NotCultureAware()
    {
        // string.CompareOrdinal: uppercase letters sort before lowercase ones
        // (ordinal 'A' = 65 < 'a' = 97), unlike a culture-aware comparison
        // that might treat them as equivalent for ordering purposes.
        var candidate = Candidate(T, "backup_a.bak");
        var existing = Candidate(T, "backup_A.bak");

        Assert.True(WatchCandidateComparer.IsMoreRecent(candidate, existing));
    }

    [Fact]
    public void IsMoreRecent_ComparesFileNameOnly_NotFullPath()
    {
        // Different directories, same file name -> tiebreak still resolves via
        // Path.GetFileName, so identical names in different dirs are treated as equal.
        var candidate = new WatchCandidateFile
        {
            BackupJobId = 1,
            LocalFilePath = @"C:\other\dir\same.bak",
            OrderingTimestampUtc = T,
            DetectionMethod = WatchDetectionMethod.Msdb,
            FileSizeBytes = 2048,
        };
        var existing = Candidate(T, "same.bak");

        Assert.False(WatchCandidateComparer.IsMoreRecent(candidate, existing));
    }
}
