import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { ServersListPage } from "@/pages/ServersListPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { paginated, makeServer, makeUser } from "@/test/fixtures";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/servers", () => ({
  listServers: vi.fn(),
  createServer: vi.fn(),
}));

import { useAuth } from "@/auth/AuthContext";
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

describe("ServersListPage -- server ID display", () => {
  beforeEach(() => {
    setAuth();
  });

  it("renders each server's ID as a sub-label under its name in the table", async () => {
    vi.mocked(listServers).mockResolvedValue(
      paginated([makeServer({ id: 42, name: "prod-sql-01" }), makeServer({ id: 99, name: "prod-sql-02" })]),
    );

    renderWithProviders(<ServersListPage />, { route: "/servers" });

    expect(await screen.findByText("prod-sql-01")).toBeInTheDocument();
    expect(screen.getByText("ID 42")).toBeInTheDocument();
    expect(screen.getByText("prod-sql-02")).toBeInTheDocument();
    expect(screen.getByText("ID 99")).toBeInTheDocument();
  });
});
