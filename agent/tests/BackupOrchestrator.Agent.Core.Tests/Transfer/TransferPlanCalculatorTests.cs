using BackupOrchestrator.Agent.Core.Transfer;

namespace BackupOrchestrator.Agent.Core.Tests.Transfer;

/// <summary>
/// Pure function, no mocking needed -- see TransferPlanCalculator's doc
/// comment: it is deliberately WinSCP-free so it's testable in isolation
/// from Session.FileExists/GetFileInfo.
/// </summary>
public sealed class TransferPlanCalculatorTests
{
    [Fact]
    public void Determine_RemoteDoesNotExist_ReturnsFull()
    {
        var plan = TransferPlanCalculator.Determine(remoteExists: false, remoteSizeBytes: null, localSizeBytes: 1000);

        Assert.Equal(TransferPlan.Full, plan);
    }

    [Fact]
    public void Determine_RemoteDoesNotExist_RemoteSizeIgnoredEvenIfNonNull()
    {
        // remoteExists=false should short-circuit before remoteSizeBytes is
        // ever consulted, regardless of what (nonsensical) value it holds.
        var plan = TransferPlanCalculator.Determine(remoteExists: false, remoteSizeBytes: 500, localSizeBytes: 1000);

        Assert.Equal(TransferPlan.Full, plan);
    }

    [Fact]
    public void Determine_RemoteExistsWithEqualSize_ReturnsSkip()
    {
        var plan = TransferPlanCalculator.Determine(remoteExists: true, remoteSizeBytes: 1000, localSizeBytes: 1000);

        Assert.Equal(TransferPlan.Skip, plan);
    }

    [Fact]
    public void Determine_RemoteExistsWithEqualSizeZero_ReturnsSkip()
    {
        // Boundary: both zero-byte files still count as "equal", not resume/overwrite.
        var plan = TransferPlanCalculator.Determine(remoteExists: true, remoteSizeBytes: 0, localSizeBytes: 0);

        Assert.Equal(TransferPlan.Skip, plan);
    }

    [Fact]
    public void Determine_RemoteSmallerThanLocal_ReturnsResume()
    {
        var plan = TransferPlanCalculator.Determine(remoteExists: true, remoteSizeBytes: 999, localSizeBytes: 1000);

        Assert.Equal(TransferPlan.Resume, plan);
    }

    [Fact]
    public void Determine_RemoteSmallerThanLocalByOneByte_ReturnsResume()
    {
        // Boundary directly adjacent to the Skip case above.
        var plan = TransferPlanCalculator.Determine(remoteExists: true, remoteSizeBytes: 999, localSizeBytes: 1000);

        Assert.Equal(TransferPlan.Resume, plan);
    }

    [Fact]
    public void Determine_RemoteLargerThanLocal_ReturnsOverwriteAnomaly()
    {
        var plan = TransferPlanCalculator.Determine(remoteExists: true, remoteSizeBytes: 1001, localSizeBytes: 1000);

        Assert.Equal(TransferPlan.OverwriteAnomaly, plan);
    }

    [Fact]
    public void Determine_RemoteLargerThanLocalByOneByte_ReturnsOverwriteAnomaly()
    {
        // Boundary directly adjacent to the Skip case (1000 vs 999 the other direction).
        var plan = TransferPlanCalculator.Determine(remoteExists: true, remoteSizeBytes: 1000, localSizeBytes: 999);

        Assert.Equal(TransferPlan.OverwriteAnomaly, plan);
    }

    [Fact]
    public void Determine_RemoteExistsWithNullSize_TreatedAsNotEqualAndNotSmaller_ReturnsOverwriteAnomaly()
    {
        // remoteSizeBytes is `long?`; null != localSizeBytes and null < localSizeBytes
        // is false for a nullable long comparison, so this falls through to the
        // OverwriteAnomaly branch. Documents the actual (if unusual) behavior at
        // this edge rather than assuming it can't happen.
        var plan = TransferPlanCalculator.Determine(remoteExists: true, remoteSizeBytes: null, localSizeBytes: 1000);

        Assert.Equal(TransferPlan.OverwriteAnomaly, plan);
    }
}
