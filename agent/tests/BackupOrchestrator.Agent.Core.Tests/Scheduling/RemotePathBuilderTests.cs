using BackupOrchestrator.Agent.Core.Scheduling;

namespace BackupOrchestrator.Agent.Core.Tests.Scheduling;

public sealed class RemotePathBuilderTests
{
    [Fact]
    public void NormalizeRemoteDirectory_AlreadyHasBothSlashes_Unchanged()
    {
        var result = RemotePathBuilder.NormalizeRemoteDirectory("/srv1/job-name-1/");

        Assert.Equal("/srv1/job-name-1/", result);
    }

    [Fact]
    public void NormalizeRemoteDirectory_MissingLeadingSlash_AddsIt()
    {
        var result = RemotePathBuilder.NormalizeRemoteDirectory("srv1/job-name-1/");

        Assert.Equal("/srv1/job-name-1/", result);
    }

    [Fact]
    public void NormalizeRemoteDirectory_MissingTrailingSlash_AddsIt()
    {
        var result = RemotePathBuilder.NormalizeRemoteDirectory("/srv1/job-name-1");

        Assert.Equal("/srv1/job-name-1/", result);
    }

    [Fact]
    public void NormalizeRemoteDirectory_MissingBothSlashes_AddsBoth()
    {
        var result = RemotePathBuilder.NormalizeRemoteDirectory("srv1/job-name-1");

        Assert.Equal("/srv1/job-name-1/", result);
    }

    [Fact]
    public void NormalizeRemoteDirectory_SurroundingWhitespace_TrimmedBeforeNormalizing()
    {
        var result = RemotePathBuilder.NormalizeRemoteDirectory("  srv1/job-name-1  ");

        Assert.Equal("/srv1/job-name-1/", result);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void NormalizeRemoteDirectory_EmptyOrWhitespaceOnly_ThrowsArgumentException(string input)
    {
        Assert.Throws<ArgumentException>(() => RemotePathBuilder.NormalizeRemoteDirectory(input));
    }

    [Fact]
    public void BuildRemoteFileName_WindowsStylePath_UsesOriginalFileNameVerbatim()
    {
        // RemotePathBuilder delegates to Path.GetFileName, which is
        // platform-dependent: only Windows treats '\' as a path separator
        // (on Linux/macOS it's just an ordinary filename character). The
        // production agent only ever runs on Windows (self-contained
        // Windows Service against WinSCP), so this test asserts the real
        // Windows-separator behavior when run on Windows, and documents the
        // (correct, and still deterministic) pass-through behavior observed
        // when this same cross-platform test suite runs on non-Windows CI.
        var result = RemotePathBuilder.BuildRemoteFileName(@"C:\backups\database.bak");

        if (OperatingSystem.IsWindows())
        {
            Assert.Equal("database.bak", result);
        }
        else
        {
            Assert.Equal(@"C:\backups\database.bak", result);
        }
    }

    [Fact]
    public void BuildRemoteFileName_ForwardSlashPath_ExtractsFileNameCorrectly()
    {
        var result = RemotePathBuilder.BuildRemoteFileName("/var/backups/dump.sql");

        Assert.Equal("dump.sql", result);
    }

    [Fact]
    public void BuildRemoteFileName_TrailingSlashDirectoryOnlyPath_FallsBackToBackupPlaceholder()
    {
        // "/" (rather than a Windows-style "C:\backups\") is used here so the
        // fallback is exercised deterministically on every platform this
        // suite runs on: TrimEnd('/', '\\') strips it to "", and
        // Path.GetFileName("") is "" on every OS, triggering the fallback.
        var result = RemotePathBuilder.BuildRemoteFileName("/");

        Assert.Equal("backup", result);
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

        var result = RemotePathBuilder.BuildRemoteFileName(@"C:\backups\");

        Assert.Equal("backup", result);
    }

    [Fact]
    public void BuildRemoteFileName_EmptyPath_FallsBackToBackupPlaceholder()
    {
        var result = RemotePathBuilder.BuildRemoteFileName(string.Empty);

        Assert.Equal("backup", result);
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
