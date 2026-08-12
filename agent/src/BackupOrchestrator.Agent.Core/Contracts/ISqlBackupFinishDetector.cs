namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// msdb-priority WATCH-mode readiness detector. MsdbBackupFinishDetector
/// (Worker project) is the only implementation that touches
/// Microsoft.Data.SqlClient; only ever consulted for a job when
/// job.SqlInstanceId is set, job.SqlInstanceUseWindowsAuth == true, and
/// job.SqlInstanceHost is not null (the "useMsdbForJob" gate -- see
/// WatchHostedService).
/// </summary>
public interface ISqlBackupFinishDetector
{
    /// <summary>
    /// Resolves whether localFilePath corresponds to a completed
    /// FULL/DIFFERENTIAL backupset for databaseName. Throws
    /// SqlDetectorUnavailableException for a connectivity-classified failure
    /// (connect timeout, connection refused, auth failure, query timeout) --
    /// callers MUST catch that specifically and fall back to lock-check for
    /// this cycle only. A successful connect+query returning no match is NOT
    /// an exception -- it returns null.
    ///
    /// DEVIATION FROM SPEC: the spec's method signature was
    /// TryGetBackupFinishUtcAsync(databaseName, localFilePath,
    /// cancellationToken) with no way to identify which SQL Server instance
    /// to connect to -- infeasible to implement against
    /// Microsoft.Data.SqlClient as written (Windows Integrated Auth still
    /// needs a target host/port/instance name). sqlInstanceHost/
    /// sqlInstancePort/sqlInstanceName (sourced from BackupJobDto.SqlInstanceHost/
    /// SqlInstancePort/SqlInstanceInstanceName by the caller) were added as
    /// leading parameters to close that gap.
    /// </summary>
    Task<DateTimeOffset?> TryGetBackupFinishUtcAsync(
        string sqlInstanceHost,
        int? sqlInstancePort,
        string? sqlInstanceName,
        string databaseName,
        string localFilePath,
        CancellationToken cancellationToken);
}

/// <summary>
/// Thrown by ISqlBackupFinishDetector for a connectivity-classified failure
/// only (never for a successful zero-row query result, which returns null
/// instead). Callers must catch this specifically and fall back to
/// lock-check for the current cycle only -- no msdb-specific backoff, the
/// next cycle tries msdb fresh again.
/// </summary>
public sealed class SqlDetectorUnavailableException : Exception
{
    public SqlDetectorUnavailableException(string message, Exception? inner = null) : base(message, inner)
    {
    }
}
