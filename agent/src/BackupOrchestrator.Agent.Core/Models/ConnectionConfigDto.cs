namespace BackupOrchestrator.Agent.Core.Models;

/// <summary>
/// Mirrors app/schemas/agent.py::AgentConnectionConfigResponse. `Protocol` is
/// a string enum with values "FTP" or "SFTP" (case-sensitive match against
/// the backend's app.models.enums.ProtocolType values -- the backend always
/// emits upper-case, so comparisons in this codebase use ordinal, case-sensitive
/// string equality; see WinScpTransferClient).
///
/// CRITICAL: never log this type's contents (Username/Password/SshPrivateKey).
/// See AgentOptions.ConnectionConfigKey doc comment for why this whole
/// endpoint is not production-safe yet.
/// </summary>
public sealed class ConnectionConfigDto
{
    public required int ServerId { get; init; }
    public required string Host { get; init; }
    public required int Port { get; init; }
    public required string Protocol { get; init; }
    public string? Username { get; init; }
    public string? Password { get; init; }
    public string? SshPrivateKey { get; init; }
}
