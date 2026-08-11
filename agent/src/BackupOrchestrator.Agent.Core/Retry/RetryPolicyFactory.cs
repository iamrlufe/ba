using Polly;
using Polly.Retry;

namespace BackupOrchestrator.Agent.Core.Retry;

/// <summary>
/// Builds the bounded Polly retry pipeline used by HttpBackendApiClient (Worker):
/// 5 attempts, 2s base delay, x2 multiplier (2s, 4s, 8s, 16s, 30s -- capped),
/// 30s max delay. NEVER infinite -- once exhausted, callers must let the
/// final exception/failure propagate so it can be turned into
/// BackendUnavailableException and routed into offline-queue mode.
/// </summary>
public static class RetryPolicyFactory
{
    public const int MaxAttempts = 5;
    public static readonly TimeSpan BaseDelay = TimeSpan.FromSeconds(2);
    public const double Multiplier = 2.0;
    public static readonly TimeSpan MaxDelay = TimeSpan.FromSeconds(30);

    /// <summary>
    /// Generic Polly ResiliencePipeline retry strategy over any outcome type.
    /// The Worker project wires this with an HttpResponseMessage-shaped
    /// outcome/predicate; kept generic here so it stays pure/testable in Core
    /// with no HttpClient dependency.
    /// </summary>
    public static ResiliencePipeline<TResult> Create<TResult>(
        Func<RetryPredicateArguments<TResult>, ValueTask<bool>> shouldRetryPredicate,
        Action<int, TimeSpan>? onRetry = null)
    {
        var options = new RetryStrategyOptions<TResult>
        {
            MaxRetryAttempts = MaxAttempts,
            BackoffType = DelayBackoffType.Exponential,
            Delay = BaseDelay,
            MaxDelay = MaxDelay,
            UseJitter = true,
            ShouldHandle = shouldRetryPredicate,
            OnRetry = args =>
            {
                onRetry?.Invoke(args.AttemptNumber, args.RetryDelay);
                return ValueTask.CompletedTask;
            },
        };

        return new ResiliencePipelineBuilder<TResult>()
            .AddRetry(options)
            .Build();
    }
}
