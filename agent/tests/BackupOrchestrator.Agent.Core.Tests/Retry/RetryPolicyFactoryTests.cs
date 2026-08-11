using BackupOrchestrator.Agent.Core.Retry;
using Polly;

namespace BackupOrchestrator.Agent.Core.Tests.Retry;

/// <summary>
/// Asserts RetryPolicyFactory's configured shape (attempt count / delay
/// bounds) and the wiring of the caller-supplied predicate/callback,
/// deliberately WITHOUT exercising a real multi-attempt exponential-backoff
/// wait (that would mean sleeping ~2s+4s+8s+16s+30s = 60s+ per test run,
/// against a live HTTP call besides -- exactly what this role must not do).
/// Scenarios below either need zero retries (predicate always false, so no
/// delay is ever awaited) or assert the publicly declared constants
/// directly.
/// </summary>
public sealed class RetryPolicyFactoryTests
{
    [Fact]
    public void ConfiguredShape_MatchesDocumentedBoundedPolicy()
    {
        Assert.Equal(5, RetryPolicyFactory.MaxAttempts);
        Assert.Equal(TimeSpan.FromSeconds(2), RetryPolicyFactory.BaseDelay);
        Assert.Equal(2.0, RetryPolicyFactory.Multiplier);
        Assert.Equal(TimeSpan.FromSeconds(30), RetryPolicyFactory.MaxDelay);
    }

    [Fact]
    public async Task Create_PredicateNeverMatches_ExecutesExactlyOnce_NoRetryNoDelay()
    {
        var attempts = 0;
        var onRetryCallCount = 0;

        var pipeline = RetryPolicyFactory.Create<int>(
            shouldRetryPredicate: _ => ValueTask.FromResult(false),
            onRetry: (_, _) => onRetryCallCount++);

        var result = await pipeline.ExecuteAsync(async ct =>
        {
            attempts++;
            await Task.Yield();
            return 42;
        });

        Assert.Equal(42, result);
        Assert.Equal(1, attempts);
        Assert.Equal(0, onRetryCallCount);
    }

    [Fact]
    public async Task Create_ShouldHandlePredicate_ReceivesTheActualOutcomeResult()
    {
        int? observedResult = null;

        var pipeline = RetryPolicyFactory.Create<int>(
            shouldRetryPredicate: args =>
            {
                observedResult = args.Outcome.Result;
                return ValueTask.FromResult(false);
            });

        await pipeline.ExecuteAsync(_ => ValueTask.FromResult(503));

        Assert.Equal(503, observedResult);
    }

    [Fact]
    public async Task Create_PredicateNeverMatches_PropagatesExceptionWithoutRetrying()
    {
        var attempts = 0;

        var pipeline = RetryPolicyFactory.Create<int>(
            shouldRetryPredicate: _ => ValueTask.FromResult(false));

        await Assert.ThrowsAsync<InvalidOperationException>(() => pipeline.ExecuteAsync<int>(_ =>
        {
            attempts++;
            throw new InvalidOperationException("simulated failure");
        }).AsTask());

        Assert.Equal(1, attempts);
    }
}
