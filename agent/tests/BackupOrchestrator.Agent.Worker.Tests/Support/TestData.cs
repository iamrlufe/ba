using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Worker.Tests.Support;

/// <summary>
/// Shared factory for a minimal-but-valid BackupJobDto, so each test only
/// overrides what it cares about. Direct analogue of
/// BackupOrchestrator.Agent.Core.Tests.Support.TestData (duplicated here
/// since test projects don't share a test-support-library seam in this
/// codebase yet -- see the equivalent note on this project's TestClock),
/// extended with PendingManualRunId/CancelRequestedRunId which the Core
/// TestData doesn't expose (not needed by any Core-level pure-logic test,
/// but required for BackupRunPipeline/SchedulerHostedService/WatchHostedService
/// coverage here).
/// </summary>
public static class TestData
{
    public static BackupJobDto Job(
        int id = 1,
        int serverId = 1,
        bool isEnabled = true,
        string? scheduleCron = "* * * * *",
        string timezone = "UTC",
        int? expectedMaxDurationMinutes = null,
        string? sourcePath = @"C:\backups\db.bak",
        int missedRunGraceMinutes = 15,
        string triggerMode = "SCHEDULE",
        int? copyWindowStartHour = null,
        int? copyWindowEndHour = null,
        bool copyWindowWeekendUnrestricted = false,
        string? watchDirectory = null,
        int? pendingManualRunId = null,
        int? cancelRequestedRunId = null) =>
        new()
        {
            Id = id,
            ServerId = serverId,
            DiskId = 1,
            IsEnabled = isEnabled,
            Name = $"job-{id}",
            SourcePath = sourcePath,
            BackupType = "FULL",
            ScheduleCron = scheduleCron,
            Timezone = timezone,
            RetentionDays = 30,
            RetentionMinCopies = 1,
            ExpectedMaxDurationMinutes = expectedMaxDurationMinutes,
            MissedRunGraceMinutes = missedRunGraceMinutes,
            TriggerMode = triggerMode,
            CopyWindowStartHour = copyWindowStartHour,
            CopyWindowEndHour = copyWindowEndHour,
            CopyWindowWeekendUnrestricted = copyWindowWeekendUnrestricted,
            WatchDirectory = watchDirectory,
            PendingManualRunId = pendingManualRunId,
            CancelRequestedRunId = cancelRequestedRunId,
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        };
}
