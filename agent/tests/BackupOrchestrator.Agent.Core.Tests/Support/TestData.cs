using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Core.Tests.Support;

/// <summary>Shared factory for a minimal-but-valid BackupJobDto, so each test only overrides what it cares about.</summary>
public static class TestData
{
    public static BackupJobDto Job(
        int id = 1,
        int serverId = 1,
        bool isEnabled = true,
        string scheduleCron = "* * * * *",
        string timezone = "UTC",
        int? expectedMaxDurationMinutes = null,
        string sourcePath = @"C:\backups\db.bak",
        int missedRunGraceMinutes = 15) =>
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
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        };
}
