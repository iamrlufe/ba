namespace BackupOrchestrator.Agent.Core.Scheduling;

/// <summary>
/// The remote directory is resolved entirely by the backend now (see
/// BackupJobDto.RemoteDirectory) -- the agent no longer has an opinion on
/// remote directory structure. This class is left with only the mechanical,
/// non-business-decision pieces: normalizing the backend-supplied directory
/// string for WinSCP path semantics, deriving the remote file name from the
/// local file name (verbatim, no agent-added timestamp), and combining the
/// two into a full remote path.
/// </summary>
public static class RemotePathBuilder
{
    /// <summary>
    /// Normalizes a backend-supplied remote directory to have exactly one
    /// leading and one trailing '/'. Throws if the input is null, empty, or
    /// whitespace-only (after trimming) -- a BackupJob with no usable remote
    /// directory is a backend-contract violation the agent must fail fast
    /// on, not silently paper over.
    /// </summary>
    public static string NormalizeRemoteDirectory(string remoteDirectory)
    {
        if (string.IsNullOrWhiteSpace(remoteDirectory))
        {
            throw new ArgumentException(
                "remoteDirectory must not be null, empty, or whitespace-only", nameof(remoteDirectory));
        }

        var trimmed = remoteDirectory.Trim();
        var withLeading = trimmed.StartsWith('/') ? trimmed : "/" + trimmed;
        return withLeading.EndsWith('/') ? withLeading : withLeading + "/";
    }

    /// <summary>
    /// Remote-имя файла = имя локального файла буквально -- без добавляемого
    /// агентом timestamp. Одинаковые локальные имена дают одинаковые
    /// remote-имена -- осознанное решение (см. спецификацию), принят риск
    /// того, что для job'ов с неуникальным локальным именем файла новые
    /// передачи будут перезаписывать/схлопывать предыдущие. Тот же defensive
    /// fallback, что и раньше: путь только к директории или пустая строка
    /// даёт "backup", не пустое remote-имя.
    /// </summary>
    public static string BuildRemoteFileName(string localSourcePath)
    {
        var originalFileName = Path.GetFileName(localSourcePath.TrimEnd('/', '\\'));
        return string.IsNullOrEmpty(originalFileName) ? "backup" : originalFileName;
    }

    public static string CombineRemotePath(string remoteDirectory, string remoteFileName)
    {
        var directory = remoteDirectory.EndsWith('/') ? remoteDirectory : remoteDirectory + "/";
        return directory + remoteFileName;
    }
}
