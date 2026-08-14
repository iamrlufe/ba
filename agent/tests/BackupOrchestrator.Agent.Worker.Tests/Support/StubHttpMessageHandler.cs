namespace BackupOrchestrator.Agent.Worker.Tests.Support;

/// <summary>
/// Test double for the transport layer HttpBackendApiClient depends on --
/// wired into a real HttpClient via its constructor (HttpClient(HttpMessageHandler)),
/// so HttpBackendApiClient itself is exercised unmodified end-to-end (its own
/// SendAsync call, response handling, exception normalization) while NEVER
/// making a real network call. <paramref name="_handler"/> may throw
/// synchronously (mirrors SocketsHttpHandler throwing HttpRequestException
/// before returning a Task) or return a Task that faults/completes.
/// </summary>
public sealed class StubHttpMessageHandler : HttpMessageHandler
{
    private readonly Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> _handler;

    public StubHttpMessageHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> handler)
    {
        _handler = handler;
    }

    public int CallCount { get; private set; }

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        CallCount++;
        return _handler(request, cancellationToken);
    }
}
