using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using Microsoft.Data.SqlClient;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Worker.Watch;

/// <summary>
/// The only ISqlBackupFinishDetector implementation that touches
/// Microsoft.Data.SqlClient. Windows Integrated Auth only (per the
/// useMsdbForJob gate in WatchHostedService: job.SqlInstanceUseWindowsAuth
/// must be true). Never logs the connection string as a whole (it contains
/// no credential for Integrated Security, but the host itself is still kept
/// out of exception messages that bubble up through SqlDetectorUnavailableException
/// where practical).
/// </summary>
public sealed class MsdbBackupFinishDetector : ISqlBackupFinishDetector
{
    private readonly AgentOptions _options;
    private readonly ILogger<MsdbBackupFinishDetector> _logger;

    public MsdbBackupFinishDetector(IOptions<AgentOptions> options, ILogger<MsdbBackupFinishDetector> logger)
    {
        _options = options.Value;
        _logger = logger;
    }

    public async Task<DateTimeOffset?> TryGetBackupFinishUtcAsync(
        string sqlInstanceHost,
        int? sqlInstancePort,
        string? sqlInstanceName,
        string databaseName,
        string localFilePath,
        CancellationToken cancellationToken)
    {
        var connectionString = BuildConnectionString(sqlInstanceHost, sqlInstancePort, sqlInstanceName);

        try
        {
            await using var connection = new SqlConnection(connectionString);
            await connection.OpenAsync(cancellationToken);

            await using var command = connection.CreateCommand();
            command.CommandTimeout = _options.MsdbCommandTimeoutSeconds;
            command.CommandText = """
                SELECT TOP 1 bs.backup_finish_date
                FROM msdb.dbo.backupmediafamily bmf
                JOIN msdb.dbo.backupset bs ON bs.media_set_id = bmf.media_set_id
                WHERE bmf.physical_device_name = @filePath
                  AND bs.database_name = @databaseName
                  -- TODO(verify-before-production): 'D' (Database/FULL) and 'I'
                  -- (Differential) type codes are sourced from Microsoft's
                  -- sys.backupset reference documentation and have NOT been
                  -- verified against a live SQL Server msdb instance in this
                  -- sandbox. Confirm against a real instance before shipping.
                  AND bs.type IN ('D', 'I')
                ORDER BY bs.backup_finish_date DESC;
                """;
            command.Parameters.AddWithValue("@filePath", localFilePath);
            command.Parameters.AddWithValue("@databaseName", databaseName);

            var result = await command.ExecuteScalarAsync(cancellationToken);
            if (result is null or DBNull)
            {
                return null;
            }

            var finishDate = (DateTime)result;
            return new DateTimeOffset(DateTime.SpecifyKind(finishDate, DateTimeKind.Utc));
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex) when (ex is SqlException or TimeoutException or InvalidOperationException)
        {
            // Connection-level fault (timeout/host unreachable/login failure)
            // or the connection-open itself timing out -- classified uniformly
            // as "msdb unavailable this cycle", never as "file not ready".
            throw new SqlDetectorUnavailableException(
                $"msdb query failed for database {databaseName} (connectivity classified) -- falling back to lock-check", ex);
        }
    }

    private string BuildConnectionString(string host, int? port, string? instanceName)
    {
        var dataSource = instanceName is not null
            ? $"{host}\\{instanceName}"
            : port is not null
                ? $"{host},{port}"
                : host;

        var builder = new SqlConnectionStringBuilder
        {
            DataSource = dataSource,
            IntegratedSecurity = true,
            ConnectTimeout = _options.MsdbConnectTimeoutSeconds,
            InitialCatalog = "msdb",
            // Intranet-only SQL Server behind the existing VPN perimeter (see
            // project CLAUDE.md) -- not exposed to the public internet, so
            // relaxing the modern SqlClient default (Encrypt=true, which
            // requires a trusted TLS certificate most on-prem instances
            // don't have configured) is an acceptable, deliberate choice
            // here rather than a security compromise.
            Encrypt = false,
        };

        return builder.ConnectionString;
    }
}
