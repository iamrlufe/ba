import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { JobsListPage } from "@/pages/JobsListPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { paginated, makeBackupJob, makeServer, makeUser } from "@/test/fixtures";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/backupJobs", () => ({ listBackupJobs: vi.fn() }));
vi.mock("@/api/endpoints/servers", () => ({ listServers: vi.fn() }));

import { useAuth } from "@/auth/AuthContext";
import { listBackupJobs } from "@/api/endpoints/backupJobs";
import { listServers } from "@/api/endpoints/servers";

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

describe("JobsListPage -- Schedule column", () => {
  beforeEach(() => {
    setAuth();
    vi.mocked(listServers).mockResolvedValue(paginated([makeServer({ id: 7, name: "prod-sql-01" })]));
  });

  it("shows 'Watch: <directory>' for WATCH jobs and the raw cron string for SCHEDULE jobs", async () => {
    vi.mocked(listBackupJobs).mockResolvedValue(
      paginated([
        makeBackupJob({
          id: 1,
          name: "scheduled-job",
          trigger_mode: "SCHEDULE",
          schedule_cron: "0 2 * * *",
          watch_directory: null,
        }),
        makeBackupJob({
          id: 2,
          name: "watch-job",
          trigger_mode: "WATCH",
          schedule_cron: null,
          watch_directory: "/incoming/dropzone",
        }),
      ]),
    );

    renderWithProviders(<JobsListPage />, { route: "/jobs" });

    expect(await screen.findByText("scheduled-job")).toBeInTheDocument();
    expect(screen.getByText("0 2 * * *")).toBeInTheDocument();

    expect(screen.getByText("watch-job")).toBeInTheDocument();
    expect(screen.getByText("Watch: /incoming/dropzone")).toBeInTheDocument();
    expect(screen.queryByText("/incoming/dropzone", { exact: true })).not.toBeInTheDocument();
  });
});
