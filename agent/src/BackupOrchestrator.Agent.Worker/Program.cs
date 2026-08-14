using BackupOrchestrator.Agent.Core.Configuration;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Scheduling;
using BackupOrchestrator.Agent.Worker.Backend;
using BackupOrchestrator.Agent.Worker.HostedServices;
using BackupOrchestrator.Agent.Worker.Monitoring;
using BackupOrchestrator.Agent.Worker.OfflineQueue;
using BackupOrchestrator.Agent.Worker.Pipeline;
using BackupOrchestrator.Agent.Worker.Transfer;
using BackupOrchestrator.Agent.Worker.Watch;
using Microsoft.Extensions.Options;
using Serilog;

var builder = Host.CreateApplicationBuilder(args);

// Host.CreateApplicationBuilder already wires appsettings.json,
// appsettings.{Environment}.json, and environment variables (with the
// standard "__" = ":" section-separator convention) in that order -- so
// Agent__AgentKey / Agent__ConnectionConfigKey / etc. override the "Agent"
// section from appsettings.json with no extra configuration needed here.
// This is exactly the "Agent__ prefix" override behavior the spec asks for.

// ---- Serilog: rolling file always on, console gated to dev -------------
var isDevelopment = builder.Environment.IsDevelopment();
var logDirectory = builder.Configuration["Serilog:RollingFileDirectory"];
if (string.IsNullOrWhiteSpace(logDirectory))
{
    logDirectory = Path.Combine(AppContext.BaseDirectory, "logs");
}

Directory.CreateDirectory(logDirectory);

var loggerConfiguration = new LoggerConfiguration()
    .MinimumLevel.Information()
    .Enrich.FromLogContext()
    .WriteTo.File(
        Path.Combine(logDirectory, "agent-.log"),
        rollingInterval: RollingInterval.Day,
        retainedFileCountLimit: 30,
        shared: true);

if (isDevelopment)
{
    loggerConfiguration = loggerConfiguration.WriteTo.Console();
}

Log.Logger = loggerConfiguration.CreateLogger();
builder.Logging.ClearProviders();
builder.Logging.AddSerilog(dispose: true);

// ---- Options: bind + validate on start -----------------------------------
builder.Services
    .AddOptions<AgentOptions>()
    .Bind(builder.Configuration.GetSection(AgentOptions.SectionName))
    .ValidateOnStart();
builder.Services.AddSingleton<IValidateOptions<AgentOptions>, AgentOptionsValidator>();

// ---- Core seams / pure logic ---------------------------------------------
builder.Services.AddSingleton<IClock, SystemClock>();
builder.Services.AddSingleton<IJobCache, InMemoryJobCache>();
builder.Services.AddSingleton<ICronNextRunCalculator, CronosNextRunCalculator>();
builder.Services.AddSingleton<JobScheduler>();

// ---- I/O implementations (Worker-only) -----------------------------------
builder.Services.AddSingleton<IOfflineEventQueue, SqliteOfflineEventQueue>();
builder.Services.AddSingleton<IOfflineReplayPacer, TaskDelayOfflineReplayPacer>();
builder.Services.AddSingleton<IBackupTransferClient, WinScpTransferClient>();
builder.Services.AddSingleton<IProcessSnapshotProvider, ProcessSnapshotProvider>();
builder.Services.AddSingleton<ICpuUsageSampler, CpuUsageSampler>();
builder.Services.AddSingleton<IHostMemoryProvider, HostMemoryProvider>();
builder.Services.AddSingleton<IServiceStatusChecker, ServiceStatusChecker>();
builder.Services.AddSingleton<IMonitoringConfigCache, InMemoryMonitoringConfigCache>();

// ---- WATCH-mode seams / pure logic + I/O implementations ------------------
builder.Services.AddSingleton<IFileLockChecker, ExclusiveOpenFileLockChecker>();
builder.Services.AddSingleton<ISqlBackupFinishDetector, MsdbBackupFinishDetector>();
builder.Services.AddSingleton<IWatchLedger, SqliteWatchLedger>();
builder.Services.AddSingleton<WatchCandidateTracker>();
builder.Services.AddSingleton<BackupRunPipeline>();

// IWatchDirectoryMonitor is deliberately IDisposable and created/disposed
// per-job by WatchHostedService, never a singleton itself -- this factory
// hands out a fresh instance (still constructor-injected with its own
// ILogger<T> via the resolved ILoggerFactory) each time it's invoked.
builder.Services.AddSingleton<Func<IWatchDirectoryMonitor>>(sp =>
    () => new FileSystemWatchDirectoryMonitor(sp.GetRequiredService<ILogger<FileSystemWatchDirectoryMonitor>>()));

builder.Services.AddHttpClient<IBackendApiClient, HttpBackendApiClient>((serviceProvider, client) =>
{
    var options = serviceProvider.GetRequiredService<IOptions<AgentOptions>>().Value;
    client.BaseAddress = new Uri(options.BackendBaseUrl);
    // X-Agent-Key is a per-request-shape secret shared by heartbeat/jobs-list/
    // job-run/backup-record calls -- set once here as a default header on the
    // typed client rather than re-added per call. NEVER logged; this line
    // itself only ever appears once, at client construction, never in a log
    // statement.
    client.DefaultRequestHeaders.Add("X-Agent-Key", options.AgentKey);
    client.Timeout = TimeSpan.FromSeconds(30);
})
.ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler
{
    // Defense-in-depth only -- the known dominant failure mode (recurring
    // SocketException 10054 on idle pooled connections) is primarily fixed
    // server-side via uvicorn's --timeout-keep-alive 90 (handled separately).
    // Forcing this pool to recycle connections before they'd otherwise go
    // idle/stale reduces (but does not eliminate) the window in which a
    // connection could be reused after the server has already closed it.
    PooledConnectionLifetime = TimeSpan.FromMinutes(2),
    // Defense-in-depth only, same rationale as above -- proactively retires
    // idle-too-long pooled connections rather than relying solely on the
    // server-side keep-alive timeout to avoid a stale-connection reuse race.
    PooledConnectionIdleTimeout = TimeSpan.FromSeconds(30),
});

// ---- Hosted services -------------------------------------------------------
builder.Services.AddHostedService<HeartbeatHostedService>();
builder.Services.AddHostedService<JobPollHostedService>();
builder.Services.AddHostedService<SchedulerHostedService>();
builder.Services.AddHostedService<WatchHostedService>();
builder.Services.AddHostedService<OfflineReplayHostedService>();
builder.Services.AddHostedService<CpuSamplingHostedService>();
builder.Services.AddHostedService<MonitoringConfigPollHostedService>();

// Windows Service integration -- no-op when not actually running as a
// service (e.g. `dotnet run` in a console during development), safe to
// always register. See the Worker .csproj BUILD NOTE for why net8.0-windows
// was kept despite this being a cross-platform build sandbox.
builder.Services.AddWindowsService(options => options.ServiceName = "BackupOrchestratorAgent");

var host = builder.Build();
host.Run();
