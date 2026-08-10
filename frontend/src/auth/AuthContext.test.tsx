import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/auth/AuthContext";
import { makeUser } from "@/test/fixtures";

vi.mock("@/api/endpoints/auth", () => ({
  login: vi.fn(),
  getMe: vi.fn(),
}));

import { login, getMe } from "@/api/endpoints/auth";

function Harness() {
  const { isAdmin, user, login: doLogin } = useAuth();
  return (
    <div>
      <p data-testid="is-admin">{String(isAdmin)}</p>
      <p data-testid="username">{user?.username ?? "none"}</p>
      <button onClick={() => doLogin({ username: "u", password: "p" })}>login</button>
    </div>
  );
}

describe("AuthContext isAdmin", () => {
  beforeEach(() => {
    vi.mocked(login).mockReset();
    vi.mocked(getMe).mockReset();
  });

  it("is false when there is no logged-in user", () => {
    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>,
    );
    expect(screen.getByTestId("is-admin")).toHaveTextContent("false");
    expect(screen.getByTestId("username")).toHaveTextContent("none");
  });

  it("is true for a user with role ADMIN", async () => {
    vi.mocked(login).mockResolvedValue({ access_token: "tok-admin", token_type: "bearer" } as never);
    vi.mocked(getMe).mockResolvedValue(makeUser({ role: "ADMIN", username: "adminuser" }));

    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>,
    );

    await act(async () => {
      screen.getByRole("button", { name: "login" }).click();
    });

    await waitFor(() => expect(screen.getByTestId("username")).toHaveTextContent("adminuser"));
    expect(screen.getByTestId("is-admin")).toHaveTextContent("true");
  });

  it("is false for a user with role OPERATOR", async () => {
    vi.mocked(login).mockResolvedValue({ access_token: "tok-op", token_type: "bearer" } as never);
    vi.mocked(getMe).mockResolvedValue(makeUser({ role: "OPERATOR", username: "operatoruser" }));

    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>,
    );

    await act(async () => {
      screen.getByRole("button", { name: "login" }).click();
    });

    await waitFor(() => expect(screen.getByTestId("username")).toHaveTextContent("operatoruser"));
    expect(screen.getByTestId("is-admin")).toHaveTextContent("false");
  });
});
