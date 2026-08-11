using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>Testability seam over System.ServiceProcess.ServiceController.</summary>
public interface IServiceStatusChecker
{
    /// <summary>Never throws for "service not installed" -- returns Status='NotFound' instead.</summary>
    ServiceStatusItem CheckStatus(string serviceName);
}
