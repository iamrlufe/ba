using BackupOrchestrator.Agent.Core.Transfer;

namespace BackupOrchestrator.Agent.Core.Tests.Transfer;

public sealed class Sha256HasherTests : IDisposable
{
    private readonly string _tempDir = Directory.CreateDirectory(
        Path.Combine(Path.GetTempPath(), "sha256-hasher-tests-" + Guid.NewGuid())).FullName;

    [Fact]
    public async Task ComputeHexHashAsync_KnownContent_ReturnsExpectedLowercaseHexDigest()
    {
        // Expected digest independently verified via `printf '%s' "hello world" | shasum -a 256`.
        const string expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9";
        var filePath = WriteFile("hello.txt", "hello world");

        var actual = await Sha256Hasher.ComputeHexHashAsync(filePath, CancellationToken.None);

        Assert.Equal(expected, actual);
        Assert.Equal(expected, actual.ToLowerInvariant());
    }

    [Fact]
    public async Task ComputeHexHashAsync_EmptyFile_ReturnsWellKnownEmptyDigest()
    {
        // The well-known SHA-256 digest of zero bytes.
        const string expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        var filePath = WriteFile("empty.txt", string.Empty);

        var actual = await Sha256Hasher.ComputeHexHashAsync(filePath, CancellationToken.None);

        Assert.Equal(expected, actual);
    }

    [Fact]
    public async Task ComputeHexHashAsync_DifferentContent_ProducesDifferentDigests()
    {
        var file1 = WriteFile("a.txt", "content-a");
        var file2 = WriteFile("b.txt", "content-b");

        var hash1 = await Sha256Hasher.ComputeHexHashAsync(file1, CancellationToken.None);
        var hash2 = await Sha256Hasher.ComputeHexHashAsync(file2, CancellationToken.None);

        Assert.NotEqual(hash1, hash2);
    }

    [Fact]
    public async Task ComputeHexHashAsync_IsDeterministic_SameContentSameDigest()
    {
        var filePath = WriteFile("repeat.txt", "backup-orchestrator-agent-test");

        var hash1 = await Sha256Hasher.ComputeHexHashAsync(filePath, CancellationToken.None);
        var hash2 = await Sha256Hasher.ComputeHexHashAsync(filePath, CancellationToken.None);

        Assert.Equal("ada7534eac5ee43f1e8c17a3393f506d558f6bb0b7e2ef054c6c463e6005bccd", hash1);
        Assert.Equal(hash1, hash2);
    }

    [Fact]
    public async Task ComputeHexHashAsync_MissingFile_ThrowsFileNotFoundException()
    {
        var missingPath = Path.Combine(_tempDir, "does-not-exist.bak");

        await Assert.ThrowsAsync<FileNotFoundException>(
            () => Sha256Hasher.ComputeHexHashAsync(missingPath, CancellationToken.None));
    }

    [Fact]
    public void AlgorithmName_IsSha256()
    {
        Assert.Equal("SHA256", Sha256Hasher.AlgorithmName);
    }

    private string WriteFile(string name, string content)
    {
        var path = Path.Combine(_tempDir, name);
        File.WriteAllText(path, content);
        return path;
    }

    public void Dispose()
    {
        if (Directory.Exists(_tempDir))
        {
            Directory.Delete(_tempDir, recursive: true);
        }
    }
}
