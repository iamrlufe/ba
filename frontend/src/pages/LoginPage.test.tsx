import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";
import { LoginPage } from "@/pages/LoginPage";
import { ApiError } from "@/api/client";
import { createTestQueryClient } from "@/test/renderWithProviders";

vi.mock("@/api/endpoints/auth", () => ({
  login: vi.fn(),
  getMe: vi.fn(),
}));

import { login, getMe } from "@/api/endpoints/auth";

function DashboardStub() {
  return <p>Dashboard Page</p>;
}

function renderLoginPage() {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/login"]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/dashboard" element={<DashboardStub />} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.mocked(login).mockReset();
    vi.mocked(getMe).mockReset();
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it("stores the JWT in sessionStorage (not localStorage) and navigates away from /login on success", async () => {
    const user = userEvent.setup();
    // Spy on Storage.prototype (shared by both localStorage and
    // sessionStorage) and filter by `this` afterwards -- spying directly on
    // a live jsdom Storage *instance* would itself create a phantom
    // "setItem" storage entry, since Storage instances proxy arbitrary
    // property assignment into the store per the Web Storage spec.
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");

    vi.mocked(login).mockResolvedValue({ access_token: "secret-jwt-token", token_type: "bearer" } as never);
    vi.mocked(getMe).mockResolvedValue({
      id: 1,
      username: "admin",
      role: "ADMIN",
      is_active: true,
      telegram_user_id: null,
      created_at: "2026-01-01T00:00:00Z",
    } as never);

    renderLoginPage();

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "hunter2");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByText("Dashboard Page")).toBeInTheDocument());

    expect(window.sessionStorage.getItem("bo_token")).toBe("secret-jwt-token");
    expect(window.localStorage.getItem("bo_token")).toBeNull();
    expect(window.localStorage.length).toBe(0);

    // Explicit project-wide invariant: localStorage.setItem must never be
    // invoked with the token storage key, under any code path.
    const localStorageWriteCalls = setItemSpy.mock.contexts.filter((ctx) => ctx === window.localStorage);
    expect(localStorageWriteCalls).toHaveLength(0);
    // Sanity check the spy setup itself is working (sessionStorage.setItem
    // shares the same Storage.prototype and *should* have been called).
    const sessionStorageWriteCalls = setItemSpy.mock.contexts.filter((ctx) => ctx === window.sessionStorage);
    expect(sessionStorageWriteCalls.length).toBeGreaterThan(0);
  });

  it("shows an error and does not store a token on failed login", async () => {
    const user = userEvent.setup();
    vi.mocked(login).mockRejectedValue(new ApiError(401, "Incorrect username or password"));

    renderLoginPage();

    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "wrongpass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Incorrect username or password")).toBeInTheDocument();
    expect(screen.queryByText("Dashboard Page")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("bo_token")).toBeNull();
    expect(window.localStorage.getItem("bo_token")).toBeNull();
    expect(getMe).not.toHaveBeenCalled();
  });

  it("never renders the token into the DOM", async () => {
    const user = userEvent.setup();
    vi.mocked(login).mockResolvedValue({ access_token: "super-secret-value-xyz", token_type: "bearer" } as never);
    vi.mocked(getMe).mockResolvedValue({
      id: 2,
      username: "op",
      role: "OPERATOR",
      is_active: true,
      telegram_user_id: null,
      created_at: "2026-01-01T00:00:00Z",
    } as never);

    renderLoginPage();

    await user.type(screen.getByLabelText("Username"), "op");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByText("Dashboard Page")).toBeInTheDocument());
    expect(document.body.innerHTML).not.toContain("super-secret-value-xyz");
  });
});
