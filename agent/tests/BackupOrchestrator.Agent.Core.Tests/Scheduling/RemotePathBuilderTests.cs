using BackupOrchestrator.Agent.Core.Scheduling;

namespace BackupOrchestrator.Agent.Core.Tests.Scheduling;

public sealed class RemotePathBuilderTests
{
    [Fact]
    public void BuildRemoteDirectory_KnownIds_ProducesConventionalPath()
    {
        var result = RemotePathBuilder.BuildRemoteDirectory(serverId: 7, backupJobId: 42);

        Assert.Equal("/7/42/", result);
    }

    [Fact]
    public void BuildRemoteFileName_WindowsStylePath_UsesTimestampPrefixAndOriginalFileName()
    {
        // RemotePathBuilder delegates to Path.GetFileName, which is
        // platform-dependent: only Windows treats '\' as a path separator
        // (on Linux/macOS it's just an ordinary filename character). The
        // production agent only ever runs on Windows (self-contained
        // Windows Service against WinSCP), so this test asserts the real
        // Windows-separator behavior when run on Windows, and documents the
        // (correct, and still deterministic) pass-through behavior observed
        // when this same cross-platform test suite runs on non-Windows CI.
        var start = new DateTimeOffset(2026, 8, 11, 3, 4, 5, TimeSpan.Zero);

        var result = RemotePathBuilder.BuildRemoteFileName(@"C:\backups\database.bak", start);

        if (OperatingSystem.IsWindows())
        {
            Assert.Equal("20260811_030405_database.bak", result);
        }
        else
        {
            Assert.Equal(@"20260811_030405_C:\backups\database.bak", result);
        }
    }

    [Fact]
    public void BuildRemoteFileName_ForwardSlashPath_ExtractsFileNameCorrectly()
    {
        var start = new DateTimeOffset(2026, 1, 2, 13, 0, 0, TimeSpan.Zero);

        var result = RemotePathBuilder.BuildRemoteFileName("/var/backups/dump.sql", start);

        Assert.Equal("20260102_130000_dump.sql", result);
    }

    [Fact]
    public void BuildRemoteFileName_TrailingSlashDirectoryOnlyPath_FallsBackToBackupPlaceholder()
    {
        var start = new DateTimeOffset(2026, 1, 2, 13, 0, 0, TimeSpan.Zero);

        // "/" (rather than a Windows-style "C:\backups\") is used here so the
        // fallback is exercised deterministically on every platform this
        // suite runs on: TrimEnd('/', '\\') strips it to "", and
        // Path.GetFileName("") is "" on every OS, triggering the fallback.
        var result = RemotePathBuilder.BuildRemoteFileName("/", start);

        Assert.Equal("20260102_130000_backup", result);
    }

    [Fact]
    public void BuildRemoteFileName_WindowsTrailingBackslashDirectoryOnlyPath_FallsBackToBackupPlaceholder_OnWindows()
    {
        // Same fallback, but via a Windows-style trailing backslash -- only
        // meaningful where '\' is actually treated as a separator.
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var start = new DateTimeOffset(2026, 1, 2, 13, 0, 0, TimeSpan.Zero);

        var result = RemotePathBuilder.BuildRemoteFileName(@"C:\backups\", start);

        Assert.Equal("20260102_130000_backup", result);
    }

    [Fact]
    public void BuildRemoteFileName_EmptyPath_FallsBackToBackupPlaceholder()
    {
        var start = new DateTimeOffset(2026, 1, 2, 13, 0, 0, TimeSpan.Zero);

        var result = RemotePathBuilder.BuildRemoteFileName(string.Empty, start);

        Assert.Equal("20260102_130000_backup", result);
    }

    [Fact]
    public void BuildRemoteFileName_TimestampIsSortableAndUsesGivenInstantVerbatim()
    {
        // Verifies the agent does not silently convert to local time -- the
        // caller is responsible for passing a UTC transfer-start instant.
        var start = new DateTimeOffset(2026, 12, 31, 23, 59, 59, TimeSpan.Zero);

        var result = RemotePathBuilder.BuildRemoteFileName("file.bak", start);

        Assert.Equal("20261231_235959_file.bak", result);
    }

    [Theory]
    [InlineData("/1/2/", "file.bak", "/1/2/file.bak")]
    [InlineData("/1/2", "file.bak", "/1/2/file.bak")]
    public void CombineRemotePath_HandlesPresenceOrAbsenceOfTrailingSlash(
        string directory, string fileName, string expected)
    {
        var result = RemotePathBuilder.CombineRemotePath(directory, fileName);

        Assert.Equal(expected, result);
    }
}
