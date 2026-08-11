using System.ComponentModel;
using System.Diagnostics;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Worker.Monitoring;

/// <summary>
/// Runs every CpuSamplingIntervalSeconds indefinitely -- every Process
/// obtained from Process.GetProcesses() MUST be Disposed, same handle
/// discipline this project already applies to WinSCP sessions, or this leaks
/// process handles until resource exhaustion.
/// </summary>
public sealed class ProcessSnapshotProvider : IProcessSnapshotProvider
{
    private readonly ILogger<ProcessSnapshotProvider> _logger;

    public ProcessSnapshotProvider(ILogger<ProcessSnapshotProvider> logger)
    {
        _logger = logger;
    }

    public IReadOnlyList<ProcessSnapshotItem> GetSnapshot()
    {
        var result = new List<ProcessSnapshotItem>();

        foreach (var process in Process.GetProcesses())
        {
            try
            {
                int pid;
                string processName;
                try
                {
                    pid = process.Id;
                    processName = process.ProcessName;
                }
                catch (Exception ex) when (ex is Win32Exception or InvalidOperationException)
                {
                    _logger.LogDebug(ex, "Skipping a process whose Id/ProcessName could not be read");
                    continue;
                }

                if (processName.Equals("Idle", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                TimeSpan totalProcessorTime;
                long workingSetBytes;
                try
                {
                    totalProcessorTime = process.TotalProcessorTime;
                    workingSetBytes = process.WorkingSet64;
                }
                catch (Exception ex) when (ex is Win32Exception or InvalidOperationException)
                {
                    _logger.LogDebug(
                        ex,
                        "Skipping process {ProcessName} (pid {Pid}): access denied or exited between samples",
                        processName, pid);
                    continue;
                }

                result.Add(new ProcessSnapshotItem
                {
                    Pid = pid,
                    ProcessName = processName,
                    TotalProcessorTime = totalProcessorTime,
                    WorkingSetBytes = workingSetBytes,
                });
            }
            finally
            {
                process.Dispose();
            }
        }

        return result;
    }
}
