import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ServerDetailPage } from "@/pages/ServerDetailPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { paginated, makeServer, makeUser } from "@/test/fixtures";
import type { ServerMetricsRead } from "@/api/types";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/servers", () => ({
  getServer: vi.fn(),
  updateServer: vi.fn(),
  deleteServer: vi.fn(),
  getServerMetrics: vi.fn(),
}));
vi.mock("@/api/endpoints/disks", () => ({ listDisks: vi.fn() }));
vi.mock("@/api/endpoints/backupJobs", () => ({ listBackupJobs: vi.fn() }));
vi.mock("@/api/endpoints/jobRuns", () => ({ listJobRuns: vi.fn() }));

import { useAuth } from "@/auth/AuthContext";
import { getServer, getServerMetrics, updateServer } from "@/api/endpoints/servers";
import { listDisks } from "@/api/endpoints/disks";
import { listBackupJobs } from "@/api/endpoints/backupJobs";

function setAuth() {
  vi.mocked(useAuth).mockReturnValue({
    token: "tok",
    user: makeUser(),
    isLoading: false,
    isAdmin: true,
    login: vi.fn(),
    logout: vi.fn(),
  } as ReturnType<typeof useAuth>);
}

describe("ServerDetailPage -- credential PATCH omission", () => {
  beforeEach(() => {
    setAuth();
    vi.mocked(getServer).mockResolvedValue(makeServer({ id: 7, name: "prod-sql-01" }));
    vi.mocked(listDisks).mockResolvedValue(paginated([]));
    vi.mocked(listBackupJobs).mockResolvedValue(paginated([]));
    vi.mocked(updateServer).mockResolvedValue(makeServer({ id: 7 }));
    vi.mocked(getServerMetrics).mockResolvedValue({ server_id: 7, metrics: null });
  });

  it("omits a blank credential field from the PATCH payload instead of sending an empty string", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ServerDetailPage />, { route: "/servers/7", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Change credentials"));

    // Leave "Username" blank; only fill in "Password".
    const passwordInput = within(dialog).getByLabelText("Password");
    await user.type(passwordInput, "new-rotated-password");

    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateServer).toHaveBeenCalled());
    const [, , payload] = vi.mocked(updateServer).mock.calls[0];
    expect(payload).not.toHaveProperty("username");
    expect(payload).toHaveProperty("password", "new-rotated-password");
    expect(payload).not.toHaveProperty("ssh_private_key");
  });

  it("omits all credential fields from the PATCH payload when 'Change credentials' is left off", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ServerDetailPage />, { route: "/servers/7", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateServer).toHaveBeenCalled());
    const [, , payload] = vi.mocked(updateServer).mock.calls[0];
    expect(payload).not.toHaveProperty("username");
    expect(payload).not.toHaveProperty("password");
    expect(payload).not.toHaveProperty("ssh_private_key");
  });
});

describe("ServerDetailPage -- server ID display", () => {
  beforeEach(() => {
    setAuth();
    vi.mocked(getServer).mockResolvedValue(makeServer({ id: 123, name: "prod-sql-01" }));
    vi.mocked(listDisks).mockResolvedValue(paginated([]));
    vi.mocked(listBackupJobs).mockResolvedValue(paginated([]));
    vi.mocked(getServerMetrics).mockResolvedValue({ server_id: 123, metrics: null });
  });

  it("renders the server's numeric ID in the Server info card", async () => {
    renderWithProviders(<ServerDetailPage />, { route: "/servers/123", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    expect(screen.getByText("ID")).toBeInTheDocument();
    expect(screen.getByText("123")).toBeInTheDocument();
  });
});

describe("ServerDetailPage -- Metrics card", () => {
  beforeEach(() => {
    setAuth();
    vi.mocked(getServer).mockResolvedValue(makeServer({ id: 7, name: "prod-sql-01" }));
    vi.mocked(listDisks).mockResolvedValue(paginated([]));
    vi.mocked(listBackupJobs).mockResolvedValue(paginated([]));
  });

  it("renders an EmptyState (not an error, not a crash) when metrics is null", async () => {
    vi.mocked(getServerMetrics).mockResolvedValue({ server_id: 7, metrics: null });

    renderWithProviders(<ServerDetailPage />, { route: "/servers/7", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    expect(await screen.findByText("No monitoring data yet")).toBeInTheDocument();
  });

  it("renders CPU/memory bars, services list, and top-processes table when metrics is populated", async () => {
    const metrics: ServerMetricsRead = {
      id: 1,
      server_id: 7,
      cpu_usage_pct: 42.5,
      memory_used_bytes: 4294967296, // 4 GiB
      memory_total_bytes: 17179869184, // 16 GiB
      memory_used_pct: 25,
      top_processes: [{ process_name: "sqlservr", pid: 1234, cpu_pct: 12.3, memory_bytes: 536870912 }],
      services_status: [
        { service_name: "MSSQLSERVER", status: "Running" },
        { service_name: "SomeOddService", status: "StartPending" },
      ],
      checked_at: "2026-08-11T09:00:00Z",
      created_at: "2026-08-11T09:00:00Z",
      updated_at: "2026-08-11T09:00:00Z",
    } as ServerMetricsRead;
    vi.mocked(getServerMetrics).mockResolvedValue({ server_id: 7, metrics });

    renderWithProviders(<ServerDetailPage />, { route: "/servers/7", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();

    // CPU bar
    expect(await screen.findByText("42.5%")).toBeInTheDocument();
    // Memory bar (pct + used/total detail, rendered as adjacent text nodes in one <p>)
    expect(screen.getByText(/25\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/4\.0 GB \/ 16\.0 GB/)).toBeInTheDocument();

    // Services list: one recognized "Running" status, one unrecognized status
    // (safe-fallback branch of ServiceStatusBadge should not crash).
    expect(screen.getByText("MSSQLSERVER")).toBeInTheDocument();
    const runningBadge = screen.getByText("Running");
    expect(runningBadge.className).toContain("bg-success");
    expect(screen.getByText("SomeOddService")).toBeInTheDocument();
    const unknownBadge = screen.getByText("StartPending");
    expect(unknownBadge.className).toContain("bg-secondary");

    // Top-processes table
    expect(screen.getByText("sqlservr")).toBeInTheDocument();
    expect(screen.getByText("1234")).toBeInTheDocument();
    expect(screen.getByText("12.3%")).toBeInTheDocument();
    expect(screen.getByText("512.0 MB")).toBeInTheDocument();
  });
});

describe("ServerDetailPage -- monitored_service_names round-trip in edit dialog", () => {
  beforeEach(() => {
    setAuth();
    vi.mocked(listDisks).mockResolvedValue(paginated([]));
    vi.mocked(listBackupJobs).mockResolvedValue(paginated([]));
    vi.mocked(getServerMetrics).mockResolvedValue({ server_id: 7, metrics: null });
  });

  it("starts with 'use global default' checked and the textarea hidden when monitored_service_names is null", async () => {
    vi.mocked(getServer).mockResolvedValue(makeServer({ id: 7, name: "prod-sql-01", monitored_service_names: null }));
    vi.mocked(updateServer).mockResolvedValue(makeServer({ id: 7 }));

    const user = userEvent.setup();
    renderWithProviders(<ServerDetailPage />, { route: "/servers/7", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    const checkbox = within(dialog).getByLabelText("Use global default monitored services");
    expect(checkbox).toBeChecked();
    expect(within(dialog).queryByLabelText("Monitored service names")).not.toBeInTheDocument();
  });

  it("unchecking and typing service names submits a correctly parsed array", async () => {
    vi.mocked(getServer).mockResolvedValue(makeServer({ id: 7, name: "prod-sql-01", monitored_service_names: null }));
    vi.mocked(updateServer).mockResolvedValue(makeServer({ id: 7 }));

    const user = userEvent.setup();
    renderWithProviders(<ServerDetailPage />, { route: "/servers/7", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Use global default monitored services"));

    const textarea = within(dialog).getByLabelText("Monitored service names");
    await user.type(textarea, "Svc1{enter}Svc2, Svc3");

    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateServer).toHaveBeenCalled());
    const [, , payload] = vi.mocked(updateServer).mock.calls[0];
    expect(payload).toHaveProperty("monitored_service_names", ["Svc1", "Svc2", "Svc3"]);
  });

  it("unchecking with an empty textarea submits an empty array, not null or [\"\"]", async () => {
    vi.mocked(getServer).mockResolvedValue(makeServer({ id: 7, name: "prod-sql-01", monitored_service_names: null }));
    vi.mocked(updateServer).mockResolvedValue(makeServer({ id: 7 }));

    const user = userEvent.setup();
    renderWithProviders(<ServerDetailPage />, { route: "/servers/7", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Use global default monitored services"));

    // Leave the (now-visible, empty) textarea untouched.
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateServer).toHaveBeenCalled());
    const [, , payload] = vi.mocked(updateServer).mock.calls[0];
    expect(payload).toHaveProperty("monitored_service_names");
    expect((payload as { monitored_service_names: unknown }).monitored_service_names).toEqual([]);
  });

  it("re-checking 'use global default' submits null regardless of leftover textarea content", async () => {
    vi.mocked(getServer).mockResolvedValue(
      makeServer({ id: 7, name: "prod-sql-01", monitored_service_names: ["ExistingSvc"] }),
    );
    vi.mocked(updateServer).mockResolvedValue(makeServer({ id: 7 }));

    const user = userEvent.setup();
    renderWithProviders(<ServerDetailPage />, { route: "/servers/7", path: "/servers/:id" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    const checkbox = within(dialog).getByLabelText("Use global default monitored services");
    expect(checkbox).not.toBeChecked();

    const textarea = within(dialog).getByLabelText("Monitored service names");
    expect(textarea).toHaveValue("ExistingSvc");
    await user.type(textarea, "\nAnotherSvc");

    // Re-check "use global default" -- leftover textarea content must be ignored.
    await user.click(checkbox);
    expect(within(dialog).queryByLabelText("Monitored service names")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateServer).toHaveBeenCalled());
    const [, , payload] = vi.mocked(updateServer).mock.calls[0];
    expect(payload).toHaveProperty("monitored_service_names", null);
  });
});
