namespace BackupOrchestrator.Agent.Core.Scheduling;

/// <summary>
/// DESIGN DECISION (made autonomously -- the backend's BackupJob model has
/// no destination/remote-path field, so this convention is entirely the
/// agent's call, not specified by the backend contract):
///
///   remote directory = /{server_id}/{backup_job_id}/
///   remote file name = {yyyyMMdd_HHmmss}_{original file name from source_path}
///
/// The timestamp is the transfer start time (UTC), formatted sortable so a
/// directory listing naturally orders oldest-to-newest. The agent reports
/// whatever it chose here back to the backend via POST /api/backup-records
/// (BackupRecordCreateRequest.RemotePath / FileName) -- the backend has no
/// independent opinion on this and just stores what it's told.
/// </summary>
public static class RemotePathBuilder
{
    public static string BuildRemoteDirectory(int serverId, int backupJobId) =>
        $"/{serverId}/{backupJobId}/";

    public static string BuildRemoteFileName(string localSourcePath, DateTimeOffset transferStartUtc)
    {
        var originalFileName = Path.GetFileName(localSourcePath.TrimEnd('/', '\\'));
        if (string.IsNullOrEmpty(originalFileName))
        {
            // Defensive fallback: source_path pointed at a directory or was
            // otherwise unusable as a file name source. Should not happen
            // for a well-formed BackupJob.source_path, but never produce an
            // empty remote file name.
            originalFileName = "backup";
        }

        var timestamp = transferStartUtc.ToString("yyyyMMdd_HHmmss");
        return $"{timestamp}_{originalFileName}";
    }

    public static string CombineRemotePath(string remoteDirectory, string remoteFileName)
    {
        var directory = remoteDirectory.EndsWith('/') ? remoteDirectory : remoteDirectory + "/";
        return directory + remoteFileName;
    }
}
