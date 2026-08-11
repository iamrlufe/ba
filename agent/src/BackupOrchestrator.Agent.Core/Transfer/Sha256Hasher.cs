using System.Security.Cryptography;

namespace BackupOrchestrator.Agent.Core.Transfer;

/// <summary>
/// Computes a local SHA-256 checksum before upload -- WinScpTransferClient
/// calls this on the local source file, then reports the resulting hex
/// string via POST /api/backup-records (BackupRecordCreateRequest.Checksum,
/// ChecksumAlgorithm = "SHA256"). File I/O only, no network/WinSCP
/// dependency, so it lives in Core and is directly unit-testable against a
/// temp file.
/// </summary>
public static class Sha256Hasher
{
    public const string AlgorithmName = "SHA256";

    public static async Task<string> ComputeHexHashAsync(string filePath, CancellationToken cancellationToken)
    {
        await using var stream = new FileStream(
            filePath, FileMode.Open, FileAccess.Read, FileShare.Read, bufferSize: 1024 * 1024, useAsync: true);

        var hashBytes = await SHA256.HashDataAsync(stream, cancellationToken);
        return Convert.ToHexString(hashBytes).ToLowerInvariant();
    }
}
