namespace BackupOrchestrator.Agent.Core.Transfer;

/// <summary>
/// Decision the transfer client makes BEFORE calling WinSCP's PutFiles,
/// based purely on remote-existence/size vs. local size -- deliberately no
/// WinSCP dependency here so it's unit-testable in isolation.
/// </summary>
public enum TransferPlan
{
    /// <summary>Remote doesn't exist yet -- normal full upload.</summary>
    Full,

    /// <summary>Remote exists and is smaller than local -- likely a prior partial transfer, resume it.</summary>
    Resume,

    /// <summary>Remote exists and is exactly the same size as local -- assume it already transferred; do not re-upload.</summary>
    Skip,

    /// <summary>
    /// Remote exists and is LARGER than local -- not a valid resume state
    /// (can't "resume" onto a file bigger than the source). Force a full
    /// truncate-and-rewrite rather than attempting a resume.
    /// </summary>
    OverwriteAnomaly,
}

/// <summary>
/// Pure logic, no WinSCP dependency -- see WinScpTransferClient.TransferAsync
/// for the caller that feeds it real Session.FileExists/GetFileInfo results.
/// </summary>
public static class TransferPlanCalculator
{
    public static TransferPlan Determine(bool remoteExists, long? remoteSizeBytes, long localSizeBytes)
    {
        if (!remoteExists)
        {
            return TransferPlan.Full;
        }

        if (remoteSizeBytes == localSizeBytes)
        {
            return TransferPlan.Skip;
        }

        if (remoteSizeBytes < localSizeBytes)
        {
            return TransferPlan.Resume;
        }

        return TransferPlan.OverwriteAnomaly;
    }
}
