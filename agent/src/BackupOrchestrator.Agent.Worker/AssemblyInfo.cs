using System.Runtime.CompilerServices;

// Exposes internal-only members (e.g. *HostedService.PollOnceAsync, deliberately
// internal rather than public since they're a test-only seam, not part of the
// package's public API) to the Worker test project. Mirrors the same "test the
// I/O-orchestration method directly, mock IBackendApiClient at the boundary"
// approach already used for the pure logic in BackupOrchestrator.Agent.Core.
[assembly: InternalsVisibleTo("BackupOrchestrator.Agent.Worker.Tests")]
