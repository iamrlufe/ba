using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Tests.Support;

/// <summary>Shared factory for a minimal-but-valid BackupJobDto, so each test only overrides what it cares about.</summary>
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
        string remoteDirectory = "/srv1/job-1/",
        int missedRunGraceMinutes = 15,
        string triggerMode = "SCHEDULE",
        int? copyWindowStartHour = null,
        int? copyWindowEndHour = null,
        bool copyWindowWeekendUnrestricted = false,
        string? watchDirectory = null) =>
        new()
        {
            Id = id,
            ServerId = serverId,
            DiskId = 1,
            IsEnabled = isEnabled,
            Name = $"job-{id}",
            SourcePath = sourcePath,
            RemoteDirectory = remoteDirectory,
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
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        };
}
