using System.ServiceProcess;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Worker.Monitoring;

public sealed class ServiceStatusChecker : IServiceStatusChecker
{
    private readonly ILogger<ServiceStatusChecker> _logger;

    public ServiceStatusChecker(ILogger<ServiceStatusChecker> logger)
    {
        _logger = logger;
    }

    public ServiceStatusItem CheckStatus(string serviceName)
    {
        try
        {
            using var sc = new ServiceController(serviceName);
            var status = sc.Status;
            return new ServiceStatusItem { ServiceName = serviceName, Status = status.ToString() };
        }
        catch (InvalidOperationException ex)
        {
            _logger.LogDebug(ex, "Service {ServiceName} not found or inaccessible; reporting NotFound", serviceName);
            return new ServiceStatusItem { ServiceName = serviceName, Status = "NotFound" };
        }
    }
}
