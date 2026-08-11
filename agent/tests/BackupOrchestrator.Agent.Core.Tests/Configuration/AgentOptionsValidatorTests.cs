using BackupOrchestrator.Agent.Core.Configuration;
using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Core.Tests.Configuration;

public sealed class AgentOptionsValidatorTests
{
    private readonly AgentOptionsValidator _validator = new();

    private static AgentOptions ValidOptions() => new()
    {
        ServerId = 1,
        AgentKey = "real-agent-key-abcdef123456",
        ConnectionConfigKey = "real-connection-config-key-987654",
        BackendBaseUrl = "https://backup-orchestrator.internal",
        OfflineQueueDirectory = "/var/lib/agent/queue",
        HeartbeatIntervalSeconds = 60,
        JobPollIntervalSeconds = 30,
        DefaultJobTimeoutMinutes = 120,
        OfflineQueueMaxAgeDays = 14,
        MonitoringConfigPollIntervalSeconds = 300,
        CpuSamplingIntervalSeconds = 5,
    };

    [Fact]
    public void Validate_WellFormedOptions_Succeeds()
    {
        var result = _validator.Validate(null, ValidOptions());

        Assert.True(result.Succeeded);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Validate_NonPositiveServerId_Fails(int serverId)
    {
        var options = ValidOptions();
        options.ServerId = serverId;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.ServerId)));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Validate_MissingAgentKey_Fails(string? agentKey)
    {
        var options = ValidOptions();
        options.AgentKey = agentKey!;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.AgentKey)) && f.Contains("empty"));
    }

    [Theory]
    [InlineData("CHANGE_ME")]
    [InlineData("change_me_please")]
    [InlineData("CHANGEME")]
    [InlineData("SET_VIA_ENV_VAR")]
    [InlineData("REPLACE_ME")]
    [InlineData("todo-fill-this-in")]
    [InlineData("PLACEHOLDER")]
    [InlineData("xxx-PlaceHolder-xxx")]
    public void Validate_AgentKeyLooksLikeUnfilledPlaceholder_Fails(string placeholderValue)
    {
        var options = ValidOptions();
        options.AgentKey = placeholderValue;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.AgentKey)) && f.Contains("placeholder"));
    }

    [Theory]
    [InlineData("CHANGE_ME")]
    [InlineData("SET_VIA_ENV_VAR")]
    public void Validate_ConnectionConfigKeyLooksLikeUnfilledPlaceholder_Fails(string placeholderValue)
    {
        var options = ValidOptions();
        options.ConnectionConfigKey = placeholderValue;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(
            result.Failures!, f => f.Contains(nameof(AgentOptions.ConnectionConfigKey)) && f.Contains("placeholder"));
    }

    [Fact]
    public void Validate_PlaceholderMarkerMatch_IsCaseInsensitive()
    {
        var options = ValidOptions();
        options.AgentKey = "cHaNgE_mE";

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
    }

    [Fact]
    public void Validate_AgentKeyEqualsConnectionConfigKey_FailsEvenIfBothOtherwiseValid()
    {
        var options = ValidOptions();
        const string sharedSecret = "same-secret-value-used-twice";
        options.AgentKey = sharedSecret;
        options.ConnectionConfigKey = sharedSecret;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(
            result.Failures!,
            f => f.Contains(nameof(AgentOptions.AgentKey)) && f.Contains(nameof(AgentOptions.ConnectionConfigKey)));
    }

    [Fact]
    public void Validate_AgentKeyDiffersFromConnectionConfigKey_DoesNotRaiseCollisionError()
    {
        var options = ValidOptions();

        var result = _validator.Validate(null, options);

        Assert.True(result.Succeeded);
    }

    [Theory]
    [InlineData("")]
    [InlineData("not-a-url")]
    [InlineData("relative/path")]
    public void Validate_InvalidBackendBaseUrl_Fails(string url)
    {
        var options = ValidOptions();
        options.BackendBaseUrl = url;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.BackendBaseUrl)));
    }

    [Theory]
    [InlineData("https://backend.example.com")]
    [InlineData("http://localhost:8000")]
    public void Validate_ValidAbsoluteBackendBaseUrl_DoesNotFailOnThatField(string url)
    {
        var options = ValidOptions();
        options.BackendBaseUrl = url;

        var result = _validator.Validate(null, options);

        Assert.True(result.Succeeded);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("  ")]
    public void Validate_MissingOfflineQueueDirectory_Fails(string? directory)
    {
        var options = ValidOptions();
        options.OfflineQueueDirectory = directory!;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.OfflineQueueDirectory)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-5)]
    public void Validate_NonPositiveHeartbeatIntervalSeconds_Fails(int value)
    {
        var options = ValidOptions();
        options.HeartbeatIntervalSeconds = value;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.HeartbeatIntervalSeconds)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-5)]
    public void Validate_NonPositiveJobPollIntervalSeconds_Fails(int value)
    {
        var options = ValidOptions();
        options.JobPollIntervalSeconds = value;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.JobPollIntervalSeconds)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Validate_NonPositiveDefaultJobTimeoutMinutes_Fails(int value)
    {
        var options = ValidOptions();
        options.DefaultJobTimeoutMinutes = value;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.DefaultJobTimeoutMinutes)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Validate_NonPositiveOfflineQueueMaxAgeDays_Fails(int value)
    {
        var options = ValidOptions();
        options.OfflineQueueMaxAgeDays = value;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.OfflineQueueMaxAgeDays)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Validate_NonPositiveMonitoringConfigPollIntervalSeconds_Fails(int value)
    {
        var options = ValidOptions();
        options.MonitoringConfigPollIntervalSeconds = value;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.MonitoringConfigPollIntervalSeconds)));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Validate_NonPositiveCpuSamplingIntervalSeconds_Fails(int value)
    {
        var options = ValidOptions();
        options.CpuSamplingIntervalSeconds = value;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains(nameof(AgentOptions.CpuSamplingIntervalSeconds)));
    }

    [Fact]
    public void Validate_CpuSamplingIntervalSecondsExceedsHeartbeatIntervalSeconds_Fails()
    {
        var options = ValidOptions();
        options.HeartbeatIntervalSeconds = 30;
        options.CpuSamplingIntervalSeconds = 31;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(
            result.Failures!,
            f => f.Contains(nameof(AgentOptions.CpuSamplingIntervalSeconds))
                 && f.Contains(nameof(AgentOptions.HeartbeatIntervalSeconds)));
    }

    [Fact]
    public void Validate_CpuSamplingIntervalSecondsEqualsHeartbeatIntervalSeconds_DoesNotFailCrossFieldRule()
    {
        // "must not exceed" -- equal is allowed, only strictly greater fails.
        var options = ValidOptions();
        options.HeartbeatIntervalSeconds = 30;
        options.CpuSamplingIntervalSeconds = 30;

        var result = _validator.Validate(null, options);

        Assert.True(result.Succeeded);
    }

    [Fact]
    public void Validate_CpuSamplingIntervalSecondsLessThanHeartbeatIntervalSeconds_Succeeds()
    {
        var options = ValidOptions();
        options.HeartbeatIntervalSeconds = 60;
        options.CpuSamplingIntervalSeconds = 5;

        var result = _validator.Validate(null, options);

        Assert.True(result.Succeeded);
    }

    [Fact]
    public void Validate_MultipleInvalidFields_ReportsAllFailuresNotJustFirst()
    {
        var options = ValidOptions();
        options.ServerId = 0;
        options.AgentKey = string.Empty;
        options.BackendBaseUrl = string.Empty;

        var result = _validator.Validate(null, options);

        Assert.True(result.Failed);
        var failures = result.Failures!.ToList();
        Assert.Contains(failures, f => f.Contains(nameof(AgentOptions.ServerId)));
        Assert.Contains(failures, f => f.Contains(nameof(AgentOptions.AgentKey)));
        Assert.Contains(failures, f => f.Contains(nameof(AgentOptions.BackendBaseUrl)));
        Assert.True(failures.Count >= 3);
    }
}
