using Microsoft.Extensions.Options;

namespace BackupOrchestrator.Agent.Core.Configuration;

/// <summary>
/// Wired via services.AddOptions&lt;AgentOptions&gt;().Bind(...).ValidateOnStart()
/// in Program.cs -- a bad config fails the process at startup instead of
/// failing mysteriously on the first heartbeat.
/// </summary>
public sealed class AgentOptionsValidator : IValidateOptions<AgentOptions>
{
    /// <summary>
    /// Values that must never be treated as real secrets -- catches
    /// appsettings.json templates being deployed unedited. Deliberately
    /// case-insensitive and substring-based ("CHANGE_ME" catches
    /// "CHANGE_ME_PLEASE" too) since these are meant to be obviously fake.
    /// </summary>
    private static readonly string[] PlaceholderMarkers =
    [
        "CHANGE_ME",
        "CHANGEME",
        "SET_VIA_ENV_VAR",
        "REPLACE_ME",
        "TODO",
        "PLACEHOLDER",
    ];

    public ValidateOptionsResult Validate(string? name, AgentOptions options)
    {
        var errors = new List<string>();

        if (options.ServerId <= 0)
        {
            errors.Add($"{nameof(AgentOptions.ServerId)} must be a positive integer.");
        }

        ValidateSecret(options.AgentKey, nameof(AgentOptions.AgentKey), errors);
        ValidateSecret(options.ConnectionConfigKey, nameof(AgentOptions.ConnectionConfigKey), errors);

        // Belt-and-suspenders: the two secrets must never collide, even if
        // both individually pass ValidateSecret -- a shared value would
        // silently defeat the "separate, more restricted" design intent
        // behind ConnectionConfigKey (see its doc comment on AgentOptions).
        if (!string.IsNullOrWhiteSpace(options.AgentKey)
            && !string.IsNullOrWhiteSpace(options.ConnectionConfigKey)
            && string.Equals(options.AgentKey, options.ConnectionConfigKey, StringComparison.Ordinal))
        {
            errors.Add(
                $"{nameof(AgentOptions.AgentKey)} and {nameof(AgentOptions.ConnectionConfigKey)} " +
                "must be different values -- they gate different levels of access.");
        }

        if (string.IsNullOrWhiteSpace(options.BackendBaseUrl))
        {
            errors.Add($"{nameof(AgentOptions.BackendBaseUrl)} must not be empty.");
        }
        else if (!Uri.TryCreate(options.BackendBaseUrl, UriKind.Absolute, out _))
        {
            errors.Add($"{nameof(AgentOptions.BackendBaseUrl)} must be a valid absolute URL.");
        }

        if (string.IsNullOrWhiteSpace(options.OfflineQueueDirectory))
        {
            errors.Add($"{nameof(AgentOptions.OfflineQueueDirectory)} must not be empty.");
        }

        if (options.HeartbeatIntervalSeconds <= 0)
        {
            errors.Add($"{nameof(AgentOptions.HeartbeatIntervalSeconds)} must be positive.");
        }

        if (options.JobPollIntervalSeconds <= 0)
        {
            errors.Add($"{nameof(AgentOptions.JobPollIntervalSeconds)} must be positive.");
        }

        if (options.DefaultJobTimeoutMinutes <= 0)
        {
            errors.Add($"{nameof(AgentOptions.DefaultJobTimeoutMinutes)} must be positive.");
        }

        if (options.OfflineQueueMaxAgeDays <= 0)
        {
            errors.Add($"{nameof(AgentOptions.OfflineQueueMaxAgeDays)} must be positive.");
        }

        if (options.MonitoringConfigPollIntervalSeconds <= 0)
        {
            errors.Add($"{nameof(AgentOptions.MonitoringConfigPollIntervalSeconds)} must be positive.");
        }

        if (options.CpuSamplingIntervalSeconds <= 0)
        {
            errors.Add($"{nameof(AgentOptions.CpuSamplingIntervalSeconds)} must be positive.");
        }

        if (options.CpuSamplingIntervalSeconds > options.HeartbeatIntervalSeconds)
        {
            errors.Add(
                $"{nameof(AgentOptions.CpuSamplingIntervalSeconds)} must not exceed " +
                $"{nameof(AgentOptions.HeartbeatIntervalSeconds)} -- otherwise the accumulator would rarely " +
                "collect more than one sample per heartbeat window, defeating interval averaging.");
        }

        return errors.Count > 0
            ? ValidateOptionsResult.Fail(errors)
            : ValidateOptionsResult.Success;
    }

    private static void ValidateSecret(string? value, string fieldName, List<string> errors)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            errors.Add($"{fieldName} must not be empty.");
            return;
        }

        foreach (var marker in PlaceholderMarkers)
        {
            if (value.Contains(marker, StringComparison.OrdinalIgnoreCase))
            {
                errors.Add(
                    $"{fieldName} looks like an unfilled placeholder value ('{marker}' found) -- " +
                    $"set a real secret via the Agent__{fieldName} environment variable.");
                return;
            }
        }
    }
}
