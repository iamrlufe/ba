namespace BackupOrchestrator.Agent.Core.Contracts;

/// <summary>
/// Thrown by IBackendApiClient implementations once the bounded Polly retry
/// policy (RetryPolicyFactory: 5 attempts, 2s base, x2 multiplier, 30s cap)
/// has been exhausted without a successful response, OR when the backend is
/// unreachable outright (DNS/connection failure, timeout). Callers (the
/// hosted services) catch this specifically to fall into offline-queue mode
/// -- it must never surface as an unhandled crash.
/// </summary>
public sealed class BackendUnavailableException : Exception
{
    public BackendUnavailableException(string message, Exception? innerException = null)
        : base(message, innerException)
    {
    }
}
