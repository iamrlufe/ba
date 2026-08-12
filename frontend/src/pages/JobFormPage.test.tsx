import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { JobFormPage } from "@/pages/JobFormPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { paginated, makeBackupJob, makeServer, makeSqlInstance, makeUser } from "@/test/fixtures";
import type { DiskRead } from "@/api/types";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/servers", () => ({ listServers: vi.fn() }));
vi.mock("@/api/endpoints/disks", () => ({ listDisks: vi.fn() }));
vi.mock("@/api/endpoints/sqlInstances", () => ({ listSqlInstances: vi.fn() }));
vi.mock("@/api/endpoints/backupJobs", () => ({
  createBackupJob: vi.fn(),
  getBackupJob: vi.fn(),
  updateBackupJob: vi.fn(),
}));

import { useAuth } from "@/auth/AuthContext";
import { listServers } from "@/api/endpoints/servers";
import { listDisks } from "@/api/endpoints/disks";
import { listSqlInstances } from "@/api/endpoints/sqlInstances";
import { createBackupJob, getBackupJob, updateBackupJob } from "@/api/endpoints/backupJobs";

const server = makeServer({ id: 7, name: "prod-sql-01" });
const disk: DiskRead = {
  id: 1,
  server_id: 7,
  label: "C-drive",
  mount_path: "C:\\backups",
  warning_threshold_pct: 80,
  critical_threshold_pct: 90,
  total_bytes: null,
  free_bytes: null,
  used_pct: null,
  usage_checked_at: null,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

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

/** Opens a Radix Select trigger (found by its associated label text) and picks an option. */
async function chooseOption(user: ReturnType<typeof userEvent.setup>, labelText: string, optionText: string) {
  const trigger = screen.getByLabelText(labelText);
  await user.click(trigger);
  const option = await screen.findByRole("option", { name: optionText });
  await user.click(option);
}

/** Fills in name + server + disk -- the fields required for any submission in create mode. */
async function fillCoreRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Name"), "my-job");
  await chooseOption(user, "Server", "prod-sql-01");
  await waitFor(() => expect(listDisks).toHaveBeenCalled());
  await chooseOption(user, "Disk", "C-drive (C:\\backups)");
}

beforeEach(() => {
  setAuth();
  vi.mocked(listServers).mockResolvedValue(paginated([server]));
  vi.mocked(listDisks).mockResolvedValue(paginated([disk]));
  vi.mocked(listSqlInstances).mockResolvedValue(paginated([makeSqlInstance()]));
});

describe("JobFormPage -- create mode, trigger_mode conditional rendering", () => {
  it("defaults to SCHEDULE mode: shows source_path/schedule_cron, hides watch_directory", async () => {
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());

    expect(screen.getByLabelText("Source path (remote)")).toBeInTheDocument();
    expect(screen.getByLabelText("Schedule (cron)")).toBeInTheDocument();
    expect(screen.queryByLabelText("Watch directory (remote)")).not.toBeInTheDocument();
  });

  it("switching to WATCH hides source_path/schedule_cron and shows watch_directory", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await chooseOption(user, "Trigger mode", "Watch directory");

    expect(screen.queryByLabelText("Source path (remote)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Schedule (cron)")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Watch directory (remote)")).toBeInTheDocument();
  });
});

describe("JobFormPage -- create mode, WATCH + TRANSACTION_LOG/CUSTOM rejected", () => {
  it("blocks submission and does not call createBackupJob", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillCoreRequiredFields(user);

    await chooseOption(user, "Trigger mode", "Watch directory");
    await user.type(screen.getByLabelText("Watch directory (remote)"), "/watched/incoming");
    await chooseOption(user, "Backup type", "TRANSACTION_LOG");

    await user.click(screen.getByRole("button", { name: "Create job" }));

    expect(
      await screen.findByText(/Watch-mode jobs don't support Transaction Log or Custom backup types/),
    ).toBeInTheDocument();
    expect(createBackupJob).not.toHaveBeenCalled();
  });
});

describe("JobFormPage -- edit mode, trigger_mode switch submits coherent field set", () => {
  it("SCHEDULE -> WATCH: patch includes trigger_mode, watch_directory, source_path:null, schedule_cron:null together", async () => {
    const job = makeBackupJob({
      id: 5,
      trigger_mode: "SCHEDULE",
      source_path: "/remote/backups",
      schedule_cron: "0 2 * * *",
      watch_directory: null,
      server_id: 7,
      disk_id: 1,
    });
    vi.mocked(getBackupJob).mockResolvedValue(job);
    vi.mocked(updateBackupJob).mockResolvedValue(job);

    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="edit" />, { route: "/jobs/5", path: "/jobs/:id" });

    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue(job.name));
    await waitFor(() => expect(screen.getByLabelText("Source path (remote)")).toHaveValue(job.source_path));

    await chooseOption(user, "Trigger mode", "Watch directory");
    await user.type(screen.getByLabelText("Watch directory (remote)"), "/watched/incoming");

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateBackupJob).toHaveBeenCalled());
    const [, , patch] = vi.mocked(updateBackupJob).mock.calls[0];
    expect(patch).toEqual(
      expect.objectContaining({
        trigger_mode: "WATCH",
        watch_directory: "/watched/incoming",
        source_path: null,
        schedule_cron: null,
      }),
    );
  });

  it("WATCH -> SCHEDULE: patch includes trigger_mode, source_path, schedule_cron, watch_directory:null together", async () => {
    const job = makeBackupJob({
      id: 6,
      trigger_mode: "WATCH",
      source_path: null,
      schedule_cron: null,
      watch_directory: "/watched/existing",
      server_id: 7,
      disk_id: 1,
    });
    vi.mocked(getBackupJob).mockResolvedValue(job);
    vi.mocked(updateBackupJob).mockResolvedValue(job);

    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="edit" />, { route: "/jobs/6", path: "/jobs/:id" });

    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue(job.name));
    await waitFor(() => expect(screen.getByLabelText("Watch directory (remote)")).toHaveValue(job.watch_directory));

    await chooseOption(user, "Trigger mode", "Schedule (cron)");
    await user.type(screen.getByLabelText("Source path (remote)"), "/remote/new-path");
    await user.type(screen.getByLabelText("Schedule (cron)"), "0 3 * * *");

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateBackupJob).toHaveBeenCalled());
    const [, , patch] = vi.mocked(updateBackupJob).mock.calls[0];
    expect(patch).toEqual(
      expect.objectContaining({
        trigger_mode: "SCHEDULE",
        source_path: "/remote/new-path",
        schedule_cron: "0 3 * * *",
        watch_directory: null,
      }),
    );
  });
});

describe("JobFormPage -- copy-window validation", () => {
  async function fillScheduleModeCore(user: ReturnType<typeof userEvent.setup>) {
    await fillCoreRequiredFields(user);
    await user.type(screen.getByLabelText("Source path (remote)"), "/remote/backups");
    await user.type(screen.getByLabelText("Schedule (cron)"), "0 2 * * *");
  }

  it("only start hour set (end empty) shows a validation error and blocks submission", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillScheduleModeCore(user);

    await user.type(screen.getByLabelText("Copy window start (hour, 0-23)"), "10");
    await user.click(screen.getByRole("button", { name: "Create job" }));

    expect(await screen.findByText(/Set both start and end hour, or leave both empty/)).toBeInTheDocument();
    expect(createBackupJob).not.toHaveBeenCalled();
  });

  it("equal start/end hour shows a validation error and blocks submission", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillScheduleModeCore(user);

    await user.type(screen.getByLabelText("Copy window start (hour, 0-23)"), "5");
    await user.type(screen.getByLabelText("Copy window end (hour, 0-23)"), "5");
    await user.click(screen.getByRole("button", { name: "Create job" }));

    expect(
      await screen.findByText(/Start and end hour must differ/),
    ).toBeInTheDocument();
    expect(createBackupJob).not.toHaveBeenCalled();
  });

  it("wraparound window (start > end, e.g. 18 -> 9) is valid and submission proceeds", async () => {
    vi.mocked(createBackupJob).mockResolvedValue(makeBackupJob({ id: 99 }));
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillScheduleModeCore(user);

    await user.type(screen.getByLabelText("Copy window start (hour, 0-23)"), "18");
    await user.type(screen.getByLabelText("Copy window end (hour, 0-23)"), "9");
    await user.click(screen.getByRole("button", { name: "Create job" }));

    await waitFor(() => expect(createBackupJob).toHaveBeenCalled());
  });
});
