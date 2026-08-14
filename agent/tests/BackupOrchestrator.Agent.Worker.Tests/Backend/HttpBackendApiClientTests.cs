using System.Net;
using System.Net.Sockets;
using System.Reflection;
using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Core.Retry;
using BackupOrchestrator.Agent.Worker.Backend;
using BackupOrchestrator.Agent.Worker.Tests.Support;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;
using Polly;
using Polly.Retry;

namespace BackupOrchestrator.Agent.Worker.Tests.Backend;

/// <summary>
/// Exercises HttpBackendApiClient.ExecuteAsync's control flow (retry-then-
/// succeed, exception-exhaustion, result-exhaustion, cancellation) end-to-end
/// through a real HttpClient wired to a StubHttpMessageHandler -- never a
/// real socket/HTTP call, per this role's constraints.
///
/// TESTABILITY GAP, documented rather than worked around silently:
/// RetryPolicyFactory.Create hardcodes its retry delay (2s/4s/8s/16s/30s,
/// UseJitter=true) with no TimeProvider/delay-override parameter, and
/// HttpBackendApiClient builds its two ResiliencePipeline fields internally
/// in its constructor (not injectable). Fully exhausting the real 5-attempt
/// pipeline would require waiting through real, jittered backoff up to
/// ~150s worst case per test -- exactly what RetryPolicyFactoryTests'
/// own doc comment says this codebase deliberately avoids. For the two
/// exhaustion scenarios below, this test file uses reflection to swap the
/// private _defaultRetryPipeline field for a pipeline built with the SAME
/// attempt count and retry predicate shape as production but a near-zero
/// delay, so ExecuteAsync's own post-pipeline branching (result-exhaustion
/// vs exception-exhaustion vs cancellation) is exercised deterministically
/// and fast. The one scenario that exercises the REAL production pipeline
/// (single retry) is deliberately kept to exactly one retry to bound the
/// unavoidable real wait to a few seconds. If RetryPolicyFactory ever grows
/// a proper TimeProvider seam, this reflection workaround should be deleted
/// in favor of injecting a fast TimeProvider directly.
/// </summary>
public sealed class HttpBackendApiClientTests
{
    private static IOptions<AgentOptions> Options() => Microsoft.Extensions.Options.Options.Create(new AgentOptions
    {
        ServerId = 42,
        AgentKey = "real-agent-key",
        ConnectionConfigKey = "real-connection-config-key",
        BackendBaseUrl = "https://backend.example.test",
        OfflineQueueDirectory = "/var/lib/agent/queue",
    });

    private static BackupRecordCreateRequest SampleRequest() => new()
    {
        BackupJobId = 1,
        FileName = "test.bak",
        RemotePath = "/remote/test.bak",
        FileSizeBytes = 1024,
    };

    private static HttpBackendApiClient CreateClient(
        StubHttpMessageHandler handler, out HttpClient httpClient, ILogger<HttpBackendApiClient>? logger = null)
    {
        httpClient = new HttpClient(handler) { BaseAddress = new Uri("https://backend.example.test") };
        return new HttpBackendApiClient(httpClient, Options(), logger ?? NullLogger<HttpBackendApiClient>.Instance);
    }

    /// <summary>
    /// Mirrors HttpBackendApiClient's private ShouldRetryDefault/IsRetryableStatus
    /// shape (duplicated here -- those are private and there's no
    /// InternalsVisibleTo in this codebase) but with a near-zero delay, so
    /// exhausting all RetryPolicyFactory.MaxAttempts retries doesn't require a
    /// real multi-second wait. See class doc comment.
    /// </summary>
    private static ResiliencePipeline<HttpResponseMessage> BuildFastDefaultShapedPipeline()
    {
        var options = new RetryStrategyOptions<HttpResponseMessage>
        {
            MaxRetryAttempts = RetryPolicyFactory.MaxAttempts,
            BackoffType = DelayBackoffType.Constant,
            Delay = TimeSpan.FromMilliseconds(1),
            UseJitter = false,
            ShouldHandle = args => ValueTask.FromResult(
                args.Outcome.Exception is not null ||
                (args.Outcome.Result is not null &&
                    ((int)args.Outcome.Result.StatusCode >= 500
                        || args.Outcome.Result.StatusCode == HttpStatusCode.RequestTimeout
                        || args.Outcome.Result.StatusCode == HttpStatusCode.TooManyRequests))),
        };

        return new ResiliencePipelineBuilder<HttpResponseMessage>().AddRetry(options).Build();
    }

    private static void ReplaceDefaultRetryPipeline(HttpBackendApiClient client, ResiliencePipeline<HttpResponseMessage> pipeline)
    {
        var field = typeof(HttpBackendApiClient).GetField("_defaultRetryPipeline", BindingFlags.NonPublic | BindingFlags.Instance)
            ?? throw new InvalidOperationException(
                "_defaultRetryPipeline field not found -- HttpBackendApiClient's private field was renamed; update this test helper alongside it.");
        field.SetValue(client, pipeline);
    }

    [Fact]
    public async Task CreateBackupRecordAsync_TransientSocketExceptionThenSuccess_SucceedsAndLogsRetryWarning()
    {
        var attempt = 0;
        var handler = new StubHttpMessageHandler((_, _) =>
        {
            attempt++;
            if (attempt == 1)
            {
                throw new HttpRequestException(
                    "connection refused", new SocketException((int)SocketError.ConnectionRefused));
            }

            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.Created));
        });

        var mockLogger = new Mock<ILogger<HttpBackendApiClient>>();
        var client = CreateClient(handler, out var httpClient, mockLogger.Object);
        using (httpClient)
        {
            var exception = await Record.ExceptionAsync(
                () => client.CreateBackupRecordAsync(SampleRequest(), CancellationToken.None));

            Assert.Null(exception);
            Assert.Equal(2, handler.CallCount);

            // The real production LogRetryAttempt/FindSocketException path ran
            // here (this is the one scenario NOT using the reflection-swapped
            // fast pipeline) -- confirm it actually logged something at
            // Warning, without pinning down exact message text (diagnostic
            // logging, not the behavior under test).
            mockLogger.Verify(
                l => l.Log(
                    LogLevel.Warning,
                    It.IsAny<EventId>(),
                    It.IsAny<It.IsAnyType>(),
                    It.IsAny<Exception>(),
                    It.IsAny<Func<It.IsAnyType, Exception?, string>>()),
                Times.AtLeastOnce);
        }
    }

    [Fact]
    public async Task CreateBackupRecordAsync_PersistentTransportException_ExhaustsRetries_ThrowsBackendUnavailableException_WrappingOriginalFromExceptionPath()
    {
        var handler = new StubHttpMessageHandler((_, _) =>
            throw new HttpRequestException("connection refused", new SocketException((int)SocketError.ConnectionRefused)));

        var mockLogger = new Mock<ILogger<HttpBackendApiClient>>();
        var client = CreateClient(handler, out var httpClient, mockLogger.Object);
        using (httpClient)
        {
            ReplaceDefaultRetryPipeline(client, BuildFastDefaultShapedPipeline());

            var exception = await Assert.ThrowsAsync<BackendUnavailableException>(
                () => client.CreateBackupRecordAsync(SampleRequest(), CancellationToken.None));

            // Exception-exhaustion path (ExecuteAsync's catch clause) wraps
            // the original exception -- distinguishes it from the
            // result-exhaustion path below, which throws with no InnerException.
            Assert.NotNull(exception.InnerException);
            Assert.IsType<HttpRequestException>(exception.InnerException);
            Assert.IsType<SocketException>(exception.InnerException!.InnerException);

            // Initial attempt + MaxAttempts retries.
            Assert.Equal(RetryPolicyFactory.MaxAttempts + 1, handler.CallCount);

            mockLogger.Verify(
                l => l.Log(
                    LogLevel.Warning,
                    It.IsAny<EventId>(),
                    It.IsAny<It.IsAnyType>(),
                    It.IsAny<Exception>(),
                    It.IsAny<Func<It.IsAnyType, Exception?, string>>()),
                Times.AtLeastOnce);
        }
    }

    [Fact]
    public async Task CreateBackupRecordAsync_PersistentServiceUnavailableStatus_ExhaustsRetries_ThrowsBackendUnavailableException_FromResultPathWithoutInnerException()
    {
        var handler = new StubHttpMessageHandler((_, _) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)));

        var client = CreateClient(handler, out var httpClient);
        using (httpClient)
        {
            ReplaceDefaultRetryPipeline(client, BuildFastDefaultShapedPipeline());

            var exception = await Assert.ThrowsAsync<BackendUnavailableException>(
                () => client.CreateBackupRecordAsync(SampleRequest(), CancellationToken.None));

            // Result-exhaustion path (the isUnavailableStatus check right
            // after pipeline.ExecuteAsync returns, NOT the catch clause) --
            // no exception ever occurred, so there must be no InnerException.
            Assert.Null(exception.InnerException);
            Assert.Contains("503", exception.Message);

            Assert.Equal(RetryPolicyFactory.MaxAttempts + 1, handler.CallCount);
        }
    }

    [Fact]
    public async Task CreateBackupRecordAsync_PreCancelledToken_PropagatesOperationCanceledException_NotWrappedAsBackendUnavailable()
    {
        var handler = new StubHttpMessageHandler((_, _) =>
            throw new InvalidOperationException("handler must not be invoked once the token is already cancelled"));

        var client = CreateClient(handler, out var httpClient);
        using (httpClient)
        using (var cts = new CancellationTokenSource())
        {
            cts.Cancel();

            await Assert.ThrowsAnyAsync<OperationCanceledException>(
                () => client.CreateBackupRecordAsync(SampleRequest(), cts.Token));

            Assert.Equal(0, handler.CallCount);
        }
    }
}
