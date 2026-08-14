using System.Net;
using System.Net.Http.Json;
using System.Net.Sockets;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Retry;
using Microsoft.Extensions.Options;
using Polly;

namespace BackupOrchestrator.Agent.Worker.Backend;

/// <summary>
/// The only IBackendApiClient implementation that touches a live HttpClient.
/// Registered as a typed client (see Program.cs) with X-Agent-Key set as a
/// default request header -- that header value is NEVER logged anywhere in
/// this class; log lines below only ever include the request URI/method and
/// the resulting status code.
///
/// Two retry pipelines are used, matching the spec's per-endpoint contract:
///   - _defaultRetryPipeline: retries on 5xx/408/429 or a transport exception,
///     for heartbeat/jobs-list/job-run/backup-record calls. Exhaustion throws
///     BackendUnavailableException, which callers turn into offline-queue mode.
///   - _connectivityOnlyRetryPipeline: retries ONLY on a transport exception
///     (DNS/connect failure/timeout), never on an HTTP status code, for
///     GetConnectionConfigAsync -- its 404/409/403/500 outcomes are all
///     explicitly documented, single-attempt, non-retried responses (a 500
///     there is "log and retry on next scheduled attempt", i.e. the next
///     poll tick calls this method again -- not an immediate Polly backoff).
/// Both pipelines are bounded (RetryPolicyFactory: 5 attempts max) -- never
/// infinite.
/// </summary>
public sealed class HttpBackendApiClient : IBackendApiClient
{
    private readonly HttpClient _httpClient;
    private readonly AgentOptions _options;
    private readonly ILogger<HttpBackendApiClient> _logger;
    private readonly ResiliencePipeline<HttpResponseMessage> _defaultRetryPipeline;
    private readonly ResiliencePipeline<HttpResponseMessage> _connectivityOnlyRetryPipeline;

    /// <summary>
    /// Ticks (UtcNow.UtcTicks) at which the most recent request attempt was
    /// started on THIS instance, across all endpoints. 0 = "no attempt yet".
    /// Best-effort diagnostic proxy only -- NOT the actual idle time of the
    /// specific pooled TCP connection reused for a given attempt (that is not
    /// observable via HttpClient/SocketsHttpHandler's public API). Read/written
    /// via Interlocked because BackupRunPipeline holds a single
    /// HttpBackendApiClient instance shared across concurrently-running job
    /// pipelines (SchedulerHostedService fires due jobs without awaiting them),
    /// so concurrent ExecuteAsync calls on the same instance are a real,
    /// by-design scenario here, not a hypothetical.
    /// </summary>
    private long _lastRequestAttemptAtTicks;

    public HttpBackendApiClient(HttpClient httpClient, IOptions<AgentOptions> options, ILogger<HttpBackendApiClient> logger)
    {
        _httpClient = httpClient;
        _options = options.Value;
        _logger = logger;

        _defaultRetryPipeline = RetryPolicyFactory.Create<HttpResponseMessage>(
            args => ValueTask.FromResult(ShouldRetryDefault(args.Outcome)),
            onRetry: (attempt, delay, outcome) => LogRetryAttempt("Backend request", attempt, delay, outcome));

        _connectivityOnlyRetryPipeline = RetryPolicyFactory.Create<HttpResponseMessage>(
            args => ValueTask.FromResult(args.Outcome.Exception is not null),
            onRetry: (attempt, delay, outcome) => LogRetryAttempt("connection-config request", attempt, delay, outcome));
    }

    /// <summary>
    /// Walks the exception chain (outer exception -> InnerException -> ...)
    /// looking for a SocketException. SocketsHttpHandler surfaces connection-level
    /// failures as HttpRequestException with InnerException set to the
    /// SocketException; some paths (and the ExecuteAsync catch clause's own
    /// "ex is SocketException" branch) can also surface a bare SocketException
    /// directly. This walk covers both without assuming a fixed wrapping depth.
    /// </summary>
    private static SocketException? FindSocketException(Exception? exception)
    {
        for (var current = exception; current is not null; current = current.InnerException)
        {
            if (current is SocketException socketException)
            {
                return socketException;
            }
        }

        return null;
    }

    private void LogRetryAttempt(string context, int attempt, TimeSpan delay, Outcome<HttpResponseMessage> outcome)
    {
        var socketException = FindSocketException(outcome.Exception);

        _logger.LogWarning(
            "{Context} retry {Attempt}/{MaxAttempts} after {DelaySeconds:F1}s backoff; ExceptionType={ExceptionType} SocketErrorCode={SocketErrorCode} NativeErrorCode={NativeErrorCode} ResultStatusCode={ResultStatusCode}",
            context,
            attempt,
            RetryPolicyFactory.MaxAttempts,
            delay.TotalSeconds,
            outcome.Exception?.GetType().Name,
            socketException?.SocketErrorCode,
            socketException?.ErrorCode,
            outcome.Result?.StatusCode);
    }

    /// <summary>
    /// Statuses that are always transient/backend-side, never a legitimate
    /// per-endpoint business outcome -- shared between the retry predicate
    /// (ShouldRetryDefault) and the post-pipeline exhaustion check in
    /// ExecuteAsync, so "still failing after all retries" is judged
    /// identically to "should this attempt be retried at all".
    /// </summary>
    private static bool IsRetryableStatus(HttpStatusCode status) =>
        (int)status >= 500 || status == HttpStatusCode.RequestTimeout || status == HttpStatusCode.TooManyRequests;

    private static bool ShouldRetryDefault(Outcome<HttpResponseMessage> outcome)
    {
        if (outcome.Exception is not null)
        {
            return true;
        }

        return outcome.Result is not null && IsRetryableStatus(outcome.Result.StatusCode);
    }

    public async Task<HeartbeatResult> SendHeartbeatAsync(HeartbeatRequest request, CancellationToken cancellationToken)
    {
        var uri = $"/api/agents/{_options.ServerId}/heartbeat";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "POST", uri,
            () => new HttpRequestMessage(HttpMethod.Post, uri) { Content = JsonContent.Create(request, options: AgentJsonOptions.Default) },
            cancellationToken);

        LogOutcome("POST", uri, response.StatusCode);
        response.EnsureSuccessStatusCode();

        var body = await response.Content.ReadFromJsonAsync<HeartbeatResponseBody>(AgentJsonOptions.Default, cancellationToken);
        return new HeartbeatResult { Success = true, ServerStatus = body?.Server?.Status };
    }

    public async Task<JobsPage> GetJobsAsync(int serverId, int limit, int offset, CancellationToken cancellationToken)
    {
        var uri = $"/api/agents/{serverId}/jobs?limit={limit}&offset={offset}";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "GET", uri,
            () => new HttpRequestMessage(HttpMethod.Get, uri),
            cancellationToken);

        LogOutcome("GET", uri, response.StatusCode);
        response.EnsureSuccessStatusCode();

        var body = await response.Content.ReadFromJsonAsync<JobsPageBody>(AgentJsonOptions.Default, cancellationToken)
            ?? throw new BackendUnavailableException($"GET {uri} returned an empty body");
        return new JobsPage { Items = body.Items, Total = body.Total };
    }

    public async Task<MonitoringConfigResult> GetMonitoringConfigAsync(int serverId, CancellationToken cancellationToken)
    {
        var uri = $"/api/agents/{serverId}/monitoring-config";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "GET", uri,
            () => new HttpRequestMessage(HttpMethod.Get, uri),
            cancellationToken);

        LogOutcome("GET", uri, response.StatusCode);

        // Any non-2xx that wasn't already retried by the pipeline (e.g. a 404
        // for a deleted/misconfigured ServerId) must not throw an unhandled
        // HttpRequestException out of this method -- MonitoringConfigPollHostedService
        // only catches BackendUnavailableException, and an unhandled exception
        // from any BackgroundService.ExecuteAsync takes down the whole agent
        // host (BackgroundServiceExceptionBehavior.StopHost). Treat any
        // remaining non-success the same as backend-unavailable: the poll
        // loop logs it and continues with the last-known cached config.
        if (!response.IsSuccessStatusCode)
        {
            throw new BackendUnavailableException($"GET {uri} returned {(int)response.StatusCode} {response.StatusCode}");
        }

        var body = await response.Content.ReadFromJsonAsync<MonitoringConfigBody>(AgentJsonOptions.Default, cancellationToken)
            ?? throw new BackendUnavailableException($"GET {uri} returned an empty body");
        return new MonitoringConfigResult { ServerId = body.ServerId, ServiceNames = body.ServiceNames };
    }

    public async Task<ConnectionConfigResult> GetConnectionConfigAsync(int serverId, CancellationToken cancellationToken)
    {
        var uri = $"/api/agents/{serverId}/connection-config";
        using var response = await ExecuteAsync(
            _connectivityOnlyRetryPipeline,
            // Never treat any status code here as "backend unavailable" --
            // 404/409/403/500 are all explicitly documented, single-attempt
            // outcomes handled by the switch below (see class doc comment).
            static _ => false,
            "GET", uri,
            () =>
            {
                var msg = new HttpRequestMessage(HttpMethod.Get, uri);
                // Deliberately NOT a default header on the shared HttpClient --
                // this key is separate from X-Agent-Key and only ever needed
                // for this one call. Value itself is never logged.
                msg.Headers.Add("X-Connection-Config-Key", _options.ConnectionConfigKey);
                return msg;
            },
            cancellationToken);

        LogOutcome("GET", uri, response.StatusCode);

        switch (response.StatusCode)
        {
            case HttpStatusCode.OK:
                var body = await response.Content.ReadFromJsonAsync<ConnectionConfigDto>(AgentJsonOptions.Default, cancellationToken)
                    ?? throw new BackendUnavailableException($"GET {uri} returned 200 with an empty body");
                return ConnectionConfigResult.Success(body);
            case HttpStatusCode.NotFound:
                return ConnectionConfigResult.Failed(ConnectionConfigOutcome.ServerNotFound);
            case HttpStatusCode.Conflict:
                return ConnectionConfigResult.Failed(ConnectionConfigOutcome.Unavailable);
            case HttpStatusCode.Forbidden:
                // Server administratively DISABLED -- NOT an auth failure. See
                // ConnectionConfigOutcome.ServerDisabled doc comment.
                return ConnectionConfigResult.Failed(ConnectionConfigOutcome.ServerDisabled);
            case HttpStatusCode.InternalServerError:
                _logger.LogWarning(
                    "connection-config decryption failed server-side for server {ServerId}; will retry on next scheduled attempt",
                    serverId);
                return ConnectionConfigResult.Failed(ConnectionConfigOutcome.DecryptionFailed);
            default:
                throw new BackendUnavailableException($"GET {uri} returned unexpected status {(int)response.StatusCode}");
        }
    }

    public async Task<JobRunDto?> CreateJobRunAsync(JobRunCreateRequest request, CancellationToken cancellationToken)
    {
        const string uri = "/api/job-runs";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "POST", uri,
            () => new HttpRequestMessage(HttpMethod.Post, uri) { Content = JsonContent.Create(request, options: AgentJsonOptions.Default) },
            cancellationToken);

        LogOutcome("POST", uri, response.StatusCode);

        if (response.StatusCode == HttpStatusCode.Conflict)
        {
            // Expected per spec: job disabled, or an active run already
            // exists. Not an error -- log at Information, do not retry.
            _logger.LogInformation("{Uri} returned 409: job disabled or an active run already exists, skipping this fire", uri);
            return null;
        }

        response.EnsureSuccessStatusCode();

        return await response.Content.ReadFromJsonAsync<JobRunDto>(AgentJsonOptions.Default, cancellationToken)
            ?? throw new BackendUnavailableException($"POST {uri} returned an empty body");
    }

    public async Task<JobRunDto?> ClaimJobRunAsync(int jobRunId, CancellationToken cancellationToken)
    {
        var uri = $"/api/job-runs/{jobRunId}/claim";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "POST", uri,
            () => new HttpRequestMessage(HttpMethod.Post, uri),
            cancellationToken);

        LogOutcome("POST", uri, response.StatusCode);

        if (response.StatusCode is HttpStatusCode.Conflict or HttpStatusCode.NotFound)
        {
            // Expected per spec: lost the race to a concurrent dispatch
            // cycle (already claimed/started), or the run no longer exists.
            // Not an error -- log at Information, do not retry this tick.
            _logger.LogInformation(
                "{Uri} returned {StatusCode}: already claimed by a concurrent cycle or run no longer exists", uri, (int)response.StatusCode);
            return null;
        }

        response.EnsureSuccessStatusCode();

        return await response.Content.ReadFromJsonAsync<JobRunDto>(AgentJsonOptions.Default, cancellationToken)
            ?? throw new BackendUnavailableException($"POST {uri} returned an empty body");
    }

    public async Task<JobRunUpdateOutcome> PatchJobRunAsync(int jobRunId, JobRunPatch patch, CancellationToken cancellationToken)
    {
        var uri = $"/api/job-runs/{jobRunId}";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "PATCH", uri,
            () => new HttpRequestMessage(HttpMethod.Patch, uri) { Content = JsonContent.Create(patch, options: AgentJsonOptions.Default) },
            cancellationToken);

        LogOutcome("PATCH", uri, response.StatusCode);
        return InterpretJobRunUpdateResponse(response, uri);
    }

    public async Task<JobRunUpdateOutcome> CompleteJobRunAsync(
        int jobRunId, JobRunCompleteRequest request, CancellationToken cancellationToken)
    {
        var uri = $"/api/job-runs/{jobRunId}/complete";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "POST", uri,
            () => new HttpRequestMessage(HttpMethod.Post, uri) { Content = JsonContent.Create(request, options: AgentJsonOptions.Default) },
            cancellationToken);

        LogOutcome("POST", uri, response.StatusCode);
        return InterpretJobRunUpdateResponse(response, uri);
    }

    private JobRunUpdateOutcome InterpretJobRunUpdateResponse(HttpResponseMessage response, string uri)
    {
        if (response.StatusCode == HttpStatusCode.Conflict)
        {
            // Expected per spec: the run is already terminal (backend's atomic
            // conditional update lost the race, or won it earlier). Not an
            // error -- log at Information, do not retry, do not queue.
            _logger.LogInformation("{Uri} returned 409: job run already terminal, dropping this update", uri);
            return JobRunUpdateOutcome.AlreadyTerminal;
        }

        response.EnsureSuccessStatusCode();
        return JobRunUpdateOutcome.Success;
    }

    public async Task CreateBackupRecordAsync(BackupRecordCreateRequest request, CancellationToken cancellationToken)
    {
        const string uri = "/api/backup-records";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "POST", uri,
            () => new HttpRequestMessage(HttpMethod.Post, uri) { Content = JsonContent.Create(request, options: AgentJsonOptions.Default) },
            cancellationToken);

        LogOutcome("POST", uri, response.StatusCode);
        response.EnsureSuccessStatusCode();
    }

    public async Task ReportWatchEventAsync(WatchEventRequest request, CancellationToken cancellationToken)
    {
        var uri = $"/api/backup-jobs/{request.BackupJobId}/watch-events";
        using var response = await ExecuteAsync(
            _defaultRetryPipeline,
            static r => IsRetryableStatus(r.StatusCode),
            "POST", uri,
            () => new HttpRequestMessage(HttpMethod.Post, uri) { Content = JsonContent.Create(request, options: AgentJsonOptions.Default) },
            cancellationToken);

        LogOutcome("POST", uri, response.StatusCode);

        // Mirrors GetMonitoringConfigAsync's fixed pattern: any non-2xx that
        // wasn't already retried by the pipeline must become
        // BackendUnavailableException here, NEVER a raw HttpRequestException
        // escaping -- callers (WatchHostedService) only catch that specific
        // type and must not crash the hosted service loop.
        if (!response.IsSuccessStatusCode)
        {
            throw new BackendUnavailableException($"POST {uri} returned {(int)response.StatusCode} {response.StatusCode}");
        }
    }

    /// <summary>
    /// Executes one HTTP call through the given retry pipeline and normalizes
    /// EVERY form of "backend unreachable" to BackendUnavailableException --
    /// callers must never see a raw HttpRequestException/TaskCanceledException
    /// from this method.
    ///
    /// Polly v8's result-based retry (used by _defaultRetryPipeline for
    /// 5xx/408/429) does NOT throw when attempts are exhausted on a matched
    /// *result* -- only exception-based exhaustion rethrows. Left unchecked,
    /// that means a persistent 500 would flow back to the caller as a
    /// perfectly normal (if unsuccessful) HttpResponseMessage, and the
    /// caller's own EnsureSuccessStatusCode() would then throw an unwrapped
    /// HttpRequestException that hosted services don't catch -- crashing the
    /// whole process under BackgroundServiceExceptionBehavior.StopHost. The
    /// <paramref name="isUnavailableStatus"/> check below closes that gap by
    /// re-checking the final result against the same "should this have been
    /// retried" question and throwing uniformly if it's still true.
    /// </summary>
    private async Task<HttpResponseMessage> ExecuteAsync(
        ResiliencePipeline<HttpResponseMessage> pipeline,
        Func<HttpResponseMessage, bool> isUnavailableStatus,
        string httpMethod,
        string uri,
        Func<HttpRequestMessage> requestFactory,
        CancellationToken cancellationToken)
    {
        var now = DateTimeOffset.UtcNow;
        var previousAttemptTicks = Interlocked.Exchange(ref _lastRequestAttemptAtTicks, now.UtcTicks);
        double? secondsSinceLastAttempt = previousAttemptTicks == 0
            ? null
            : (now - new DateTimeOffset(previousAttemptTicks, TimeSpan.Zero)).TotalSeconds;

        try
        {
            var response = await pipeline.ExecuteAsync(
                async ct =>
                {
                    using var request = requestFactory();
                    return await _httpClient.SendAsync(request, ct);
                },
                cancellationToken);

            if (isUnavailableStatus(response))
            {
                _logger.LogWarning(
                    "{HttpMethod} {Uri} exhausted all retry attempts with a non-success status; ResultStatusCode={ResultStatusCode} SecondsSinceLastAttempt={SecondsSinceLastAttempt}",
                    httpMethod, uri, (int)response.StatusCode, secondsSinceLastAttempt);
                throw new BackendUnavailableException(
                    $"Backend returned {(int)response.StatusCode} after exhausting retries");
            }

            return response;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            // Caller-initiated cancellation (shutdown/watchdog) -- not a
            // backend-unavailable condition, let it propagate as-is.
            throw;
        }
        catch (BackendUnavailableException)
        {
            // Already the right type (thrown just above on result-exhaustion) -- don't re-wrap.
            throw;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or SocketException)
        {
            var socketException = FindSocketException(ex);
            _logger.LogWarning(
                "{HttpMethod} {Uri} exhausted all retry attempts; ExceptionType={ExceptionType} SocketErrorCode={SocketErrorCode} NativeErrorCode={NativeErrorCode} SecondsSinceLastAttempt={SecondsSinceLastAttempt}",
                httpMethod,
                uri,
                ex.GetType().Name,
                socketException?.SocketErrorCode,
                socketException?.ErrorCode,
                secondsSinceLastAttempt);
            throw new BackendUnavailableException("Backend unreachable after exhausting retries", ex);
        }
    }

    private void LogOutcome(string method, string uri, HttpStatusCode statusCode) =>
        _logger.LogDebug("{Method} {Uri} -> {StatusCode}", method, uri, (int)statusCode);

    private sealed class HeartbeatResponseBody
    {
        public ServerStatusBody? Server { get; set; }
    }

    private sealed class ServerStatusBody
    {
        public string? Status { get; set; }
    }

    private sealed class JobsPageBody
    {
        public List<BackupJobDto> Items { get; set; } = [];
        public int Total { get; set; }
    }

    private sealed class MonitoringConfigBody
    {
        public int ServerId { get; set; }
        public List<string> ServiceNames { get; set; } = [];
    }
}
