namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>One entry of HeartbeatRequest.Services on the wire.</summary>
public sealed class ServiceStatusItem
{
    public required string ServiceName { get; init; }

    /// <summary>
    /// Free text -- verbatim ServiceControllerStatus.ToString(), or the
    /// "NotFound" sentinel for a service that doesn't exist on this host.
    /// </summary>
    public required string Status { get; init; }
}
