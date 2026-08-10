import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ServerDetailPage } from "@/pages/ServerDetailPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { paginated, makeServer, makeUser } from "@/test/fixtures";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/servers", () => ({ getServer: vi.fn(), updateServer: vi.fn(), deleteServer: vi.fn() }));
vi.mock("@/api/endpoints/disks", () => ({ listDisks: vi.fn() }));
vi.mock("@/api/endpoints/backupJobs", () => ({ listBackupJobs: vi.fn() }));
vi.mock("@/api/endpoints/jobRuns", () => ({ listJobRuns: vi.fn() }));

import { useAuth } from "@/auth/AuthContext";
import { getServer, updateServer } from "@/api/endpoints/servers";
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
