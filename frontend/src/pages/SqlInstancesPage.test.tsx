import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SqlInstancesPage } from "@/pages/SqlInstancesPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { paginated, makeSqlInstance, makeUser } from "@/test/fixtures";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/sqlInstances", () => ({
  listSqlInstances: vi.fn(),
  createSqlInstance: vi.fn(),
  updateSqlInstance: vi.fn(),
  deleteSqlInstance: vi.fn(),
}));
vi.mock("@/api/endpoints/servers", () => ({ listServers: vi.fn() }));

import { useAuth } from "@/auth/AuthContext";
import { listSqlInstances, updateSqlInstance } from "@/api/endpoints/sqlInstances";
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

const instance = makeSqlInstance({ id: 3, name: "SQL1", use_windows_auth: false, credentials_set: true });

describe("SqlInstancesPage -- credential PATCH omission", () => {
  beforeEach(() => {
    setAuth();
    vi.mocked(listSqlInstances).mockResolvedValue(paginated([instance]));
    vi.mocked(listServers).mockResolvedValue(paginated([]));
    vi.mocked(updateSqlInstance).mockResolvedValue(instance);
  });

  it("omits a blank credential field from the PATCH payload instead of sending an empty string", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SqlInstancesPage />, { route: "/sql-instances" });

    expect(await screen.findByText("SQL1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Change credentials"));

    // Leave "Username" blank; only fill in "Password".
    const passwordInput = within(dialog).getByLabelText("Password");
    await user.type(passwordInput, "new-rotated-password");

    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateSqlInstance).toHaveBeenCalled());
    const [, , payload] = vi.mocked(updateSqlInstance).mock.calls[0];
    expect(payload).not.toHaveProperty("username");
    expect(payload).toHaveProperty("password", "new-rotated-password");
  });

  it("omits both credential fields from the PATCH payload when 'Change credentials' is left off", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SqlInstancesPage />, { route: "/sql-instances" });

    expect(await screen.findByText("SQL1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateSqlInstance).toHaveBeenCalled());
    const [, , payload] = vi.mocked(updateSqlInstance).mock.calls[0];
    expect(payload).not.toHaveProperty("username");
    expect(payload).not.toHaveProperty("password");
  });
});
