import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { JobDetailPage } from "@/pages/JobDetailPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { makeBackupJob, makeUser } from "@/test/fixtures";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/backupJobs", () => ({
  getBackupJob: vi.fn(),
  deleteBackupJob: vi.fn(),
  verifyBackupJob: vi.fn(),
}));
vi.mock("@/api/endpoints/jobRuns", () => ({ listJobRuns: vi.fn(), createJobRun: vi.fn() }));
vi.mock("@/api/endpoints/verificationRuns", () => ({ listVerificationRuns: vi.fn() }));

import { useAuth } from "@/auth/AuthContext";
import { getBackupJob } from "@/api/endpoints/backupJobs";

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

beforeEach(() => {
  setAuth();
});

describe("JobDetailPage -- remote_directory / remote_directory_override display", () => {
  it("shows the resolved remote_directory with the 'manually overridden' caption when an override is set", async () => {
    const job = makeBackupJob({
      id: 20,
      remote_directory: "Taraz/tTaraz/DIFF/",
      remote_directory_override: "Taraz/tTaraz/DIFF/",
    });
    vi.mocked(getBackupJob).mockResolvedValue(job);

    renderWithProviders(<JobDetailPage />, { route: "/jobs/20", path: "/jobs/:id" });

    expect(await screen.findByText(job.name)).toBeInTheDocument();
    expect(screen.getByText("Taraz/tTaraz/DIFF/")).toBeInTheDocument();
    expect(screen.getByText("переопределено вручную")).toBeInTheDocument();
    expect(screen.queryByText("вычислено автоматически")).not.toBeInTheDocument();
  });

  it("shows the resolved remote_directory with the 'computed automatically' caption when no override is set", async () => {
    const job = makeBackupJob({
      id: 21,
      remote_directory: "trz1c8.rcku.net/Nightly_42/DIFF/",
      remote_directory_override: null,
    });
    vi.mocked(getBackupJob).mockResolvedValue(job);

    renderWithProviders(<JobDetailPage />, { route: "/jobs/21", path: "/jobs/:id" });

    expect(await screen.findByText(job.name)).toBeInTheDocument();
    expect(screen.getByText("trz1c8.rcku.net/Nightly_42/DIFF/")).toBeInTheDocument();
    expect(screen.getByText("вычислено автоматически")).toBeInTheDocument();
    expect(screen.queryByText("переопределено вручную")).not.toBeInTheDocument();
  });
});
