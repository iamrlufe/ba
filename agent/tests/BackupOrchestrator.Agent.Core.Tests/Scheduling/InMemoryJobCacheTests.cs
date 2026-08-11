using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Core.Tests.Support;

namespace BackupOrchestrator.Agent.Core.Tests.Scheduling;

public sealed class InMemoryJobCacheTests
{
    [Fact]
    public void GetAll_BeforeAnyReplaceAll_ReturnsEmpty()
    {
        var cache = new InMemoryJobCache();

        Assert.Empty(cache.GetAll());
    }

    [Fact]
    public void ReplaceAll_ThenGetAll_ReturnsAllSuppliedJobs()
    {
        var cache = new InMemoryJobCache();
        var jobs = new[] { TestData.Job(id: 1), TestData.Job(id: 2) };

        cache.ReplaceAll(jobs);

        Assert.Equal(2, cache.GetAll().Count);
    }

    [Fact]
    public void ReplaceAll_CalledAgain_FullySupersedesPreviousSnapshot()
    {
        var cache = new InMemoryJobCache();
        cache.ReplaceAll([TestData.Job(id: 1), TestData.Job(id: 2)]);

        cache.ReplaceAll([TestData.Job(id: 3)]);

        var all = cache.GetAll();
        Assert.Single(all);
        Assert.Equal(3, all[0].Id);
        Assert.Null(cache.GetById(1));
        Assert.Null(cache.GetById(2));
    }

    [Fact]
    public void GetById_KnownId_ReturnsMatchingJob()
    {
        var cache = new InMemoryJobCache();
        cache.ReplaceAll([TestData.Job(id: 5, isEnabled: false)]);

        var job = cache.GetById(5);

        Assert.NotNull(job);
        Assert.False(job!.IsEnabled);
    }

    [Fact]
    public void GetById_UnknownId_ReturnsNull()
    {
        var cache = new InMemoryJobCache();
        cache.ReplaceAll([TestData.Job(id: 5)]);

        Assert.Null(cache.GetById(999));
    }

    [Fact]
    public void ReplaceAll_DuplicateJobIdsInInput_LastOneWins()
    {
        var cache = new InMemoryJobCache();
        var first = TestData.Job(id: 1, isEnabled: true);
        var second = TestData.Job(id: 1, isEnabled: false);

        cache.ReplaceAll([first, second]);

        var result = cache.GetById(1);
        Assert.NotNull(result);
        Assert.False(result!.IsEnabled);
    }
}
