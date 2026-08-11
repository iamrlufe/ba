using System.Text.Json.Serialization;

namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Mirrors app/models/enums.py::JobRunStatus on the backend exactly -- the
/// string values (not just member names) are sent over the wire, so keep
/// these in lockstep with the backend enum.
/// </summary>
[JsonConverter(typeof(JsonStringEnumConverter))]
public enum JobRunStatus
{
    PENDING,
    RUNNING,
    SUCCESS,
    WARNING,
    FAILED,
    CANCELLED,
    TIMEOUT,
}
