using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;
using BackupOrchestrator.Agent.Worker.HostedServices;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;

namespace BackupOrchestrator.Agent.Worker.Tests.HostedServices;

/// <summary>
/// Exercises MonitoringConfigPollHostedService.PollOnceAsync directly
/// (the internal, I/O-free-boundary method) against a mocked
/// IBackendApiClient and a mocked IMonitoringConfigCache -- never a real
/// HTTP call. ExecuteAsync's PeriodicTimer loop itself is not under test
/// here, matching this project's existing JobPollHostedService convention.
/// </summary>
public sealed class MonitoringConfigPollHostedServiceTests
{
    private static AgentOptions Options(int serverId = 42) => new()
    {
        ServerId = serverId,
        AgentKey = "real-agent-key",
        ConnectionConfigKey = "real-connection-config-key",
        BackendBaseUrl = "https://backend.example.com",
        OfflineQueueDirectory = "/var/lib/agent/queue",
    };

    private static MonitoringConfigPollHostedService CreateService(
        Mock<IBackendApiClient> backendApiClient,
        Mock<IMonitoringConfigCache> monitoringConfigCache,
        AgentOptions? options = null) =>
        new(
            backendApiClient.Object,
            monitoringConfigCache.Object,
            Microsoft.Extensions.Options.Options.Create(options ?? Options()),
            NullLogger<MonitoringConfigPollHostedService>.Instance);

    [Fact]
    public async Task PollOnceAsync_SuccessfulPoll_ReplacesCacheWithReturnedServiceNames()
    {
        var backendApiClient = new Mock<IBackendApiClient>();
        var monitoringConfigCache = new Mock<IMonitoringConfigCache>();
        var options = Options(serverId: 42);
        IReadOnlyList<string> serviceNames = ["MSSQLSERVER", "SQLSERVERAGENT"];

        backendApiClient
            .Setup(c => c.GetMonitoringConfigAsync(42, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MonitoringConfigResult { ServerId = 42, ServiceNames = serviceNames });

        var service = CreateService(backendApiClient, monitoringConfigCache, options);

        await service.PollOnceAsync(CancellationToken.None);

        monitoringConfigCache.Verify(c => c.Replace(serviceNames), Times.Once);
    }

    [Fact]
    public async Task PollOnceAsync_BackendUnavailable_DoesNotCallReplaceAndDoesNotThrow()
    {
        var backendApiClient = new Mock<IBackendApiClient>();
        var monitoringConfigCache = new Mock<IMonitoringConfigCache>();

        backendApiClient
            .Setup(c => c.GetMonitoringConfigAsync(It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new BackendUnavailableException("backend unreachable"));

        var service = CreateService(backendApiClient, monitoringConfigCache);

        var exception = await Record.ExceptionAsync(() => service.PollOnceAsync(CancellationToken.None));

        Assert.Null(exception);
        monitoringConfigCache.Verify(c => c.Replace(It.IsAny<IReadOnlyList<string>>()), Times.Never);
    }

    [Fact]
    public async Task PollOnceAsync_UsesServerIdFromOptions()
    {
        var backendApiClient = new Mock<IBackendApiClient>();
        var monitoringConfigCache = new Mock<IMonitoringConfigCache>();
        var options = Options(serverId: 777);

        backendApiClient
            .Setup(c => c.GetMonitoringConfigAsync(777, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MonitoringConfigResult { ServerId = 777, ServiceNames = [] });

        var service = CreateService(backendApiClient, monitoringConfigCache, options);

        await service.PollOnceAsync(CancellationToken.None);

        backendApiClient.Verify(c => c.GetMonitoringConfigAsync(777, It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task PollOnceAsync_SuccessfulPollWithEmptyServiceList_StillReplacesCache()
    {
        // Empty (non-null) is a legitimate "no services configured" snapshot,
        // distinct from "never successfully polled" -- must still call
        // Replace() so CurrentServiceNames transitions from null to [].
        var backendApiClient = new Mock<IBackendApiClient>();
        var monitoringConfigCache = new Mock<IMonitoringConfigCache>();

        backendApiClient
            .Setup(c => c.GetMonitoringConfigAsync(It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MonitoringConfigResult { ServerId = 42, ServiceNames = [] });

        var service = CreateService(backendApiClient, monitoringConfigCache);

        await service.PollOnceAsync(CancellationToken.None);

        monitoringConfigCache.Verify(c => c.Replace(It.Is<IReadOnlyList<string>>(names => names.Count == 0)), Times.Once);
    }
}
