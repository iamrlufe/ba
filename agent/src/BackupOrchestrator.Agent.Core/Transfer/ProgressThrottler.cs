using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Transfer;

/// <summary>
/// Decides which raw WinSCP FileTransferProgress samples actually get turned
/// into a PATCH /api/job-runs/{id} call, to avoid hammering the backend on
/// every few-KB progress tick. A sample is allowed through when EITHER:
///   - at least 2-5 seconds have elapsed since the last reported sample, OR
///   - percent complete has changed by at least 1 percentage point.
/// The first sample for a given instance is always allowed through.
/// Not thread-safe by design -- one instance per in-flight transfer.
/// </summary>
public sealed class ProgressThrottler
{
    private readonly IClock _clock;
    private readonly TimeSpan _minInterval;
    private readonly int _minPercentDelta;

    private DateTimeOffset? _lastReportedAt;
    private int? _lastReportedPercent;

    public ProgressThrottler(IClock clock, TimeSpan? minInterval = null, int minPercentDelta = 1)
    {
        _clock = clock;
        _minInterval = minInterval ?? TimeSpan.FromSeconds(3);
        _minPercentDelta = minPercentDelta;
    }

    /// <summary>Returns true if this sample should be reported now (and records it as the new baseline).</summary>
    public bool ShouldReport(TransferProgress progress)
    {
        var now = _clock.UtcNow;

        if (_lastReportedAt is null || _lastReportedPercent is null)
        {
            _lastReportedAt = now;
            _lastReportedPercent = progress.PercentComplete;
            return true;
        }

        var elapsed = now - _lastReportedAt.Value;
        var percentDelta = Math.Abs(progress.PercentComplete - _lastReportedPercent.Value);

        if (elapsed < _minInterval && percentDelta < _minPercentDelta)
        {
            return false;
        }

        _lastReportedAt = now;
        _lastReportedPercent = progress.PercentComplete;
        return true;
    }
}
