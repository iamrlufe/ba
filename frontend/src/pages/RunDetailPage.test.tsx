import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, act } from "@testing-library/react";
import { RunDetailPage } from "@/pages/RunDetailPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { makeJobRun, makeUser } from "@/test/fixtures";
import { FakeWebSocket, installFakeWebSocket } from "@/test/fakeWebSocket";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/jobRuns", () => ({ getJobRun: vi.fn(), getJobRunLog: vi.fn() }));

import { useAuth } from "@/auth/AuthContext";
import { getJobRun, getJobRunLog } from "@/api/endpoints/jobRuns";

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

describe("RunDetailPage live progress", () => {
  beforeEach(() => {
    setAuth();
    installFakeWebSocket();
    vi.mocked(getJobRunLog).mockResolvedValue({ log_output: null });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the initial HTTP-seeded run, then updates the displayed status/progress from a WebSocket message", async () => {
    vi.mocked(getJobRun).mockResolvedValue(makeJobRun({ id: 42, status: "RUNNING", percent: 10, current_file: "file_0001.bak" }));

    renderWithProviders(<RunDetailPage />, { route: "/runs/42", path: "/runs/:id" });

    expect(await screen.findByText("Run #42")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("file_0001.bak")).toBeInTheDocument();
    // Initial JobRunStatusBadge (RUNNING appears both in the header badge and the connection state is separate).
    expect(screen.getAllByText("RUNNING").length).toBeGreaterThan(0);

    const socket = FakeWebSocket.latest();
    act(() => socket.triggerOpen());

    act(() => {
      socket.triggerMessage(
        makeJobRun({ id: 42, status: "RUNNING", percent: 55, current_file: "file_0002.bak", bytes_done: 555000 }),
      );
    });

    expect(await screen.findByText("55%")).toBeInTheDocument();
    expect(screen.getByText("file_0002.bak")).toBeInTheDocument();
    expect(screen.queryByText("10%")).not.toBeInTheDocument();
  });

  it("shows a 'live' connection indicator once open, and a 'reconnecting' indicator after an abnormal drop", async () => {
    vi.mocked(getJobRun).mockResolvedValue(makeJobRun({ id: 7, status: "RUNNING", percent: 1 }));

    renderWithProviders(<RunDetailPage />, { route: "/runs/7", path: "/runs/:id" });

    expect(await screen.findByText("Run #7")).toBeInTheDocument();
    expect(screen.getByText(/Connection: connecting…/)).toBeInTheDocument();

    const socket = FakeWebSocket.latest();
    act(() => socket.triggerOpen());
    expect(await screen.findByText(/Connection: live/)).toBeInTheDocument();

    act(() => socket.triggerClose(1006, "abnormal"));
    expect(await screen.findByText(/Connection: reconnecting… \(attempt 1\)/)).toBeInTheDocument();
  });

  it("closes the socket on unmount", async () => {
    vi.mocked(getJobRun).mockResolvedValue(makeJobRun({ id: 3, status: "RUNNING" }));

    const { unmount } = renderWithProviders(<RunDetailPage />, { route: "/runs/3", path: "/runs/:id" });

    expect(await screen.findByText("Run #3")).toBeInTheDocument();
    const socket = FakeWebSocket.latest();
    act(() => socket.triggerOpen());

    expect(socket.close).not.toHaveBeenCalled();
    unmount();
    expect(socket.close).toHaveBeenCalledWith(1000, "component unmounted");
  });

  it("does not show a live connection indicator for an already-terminal run, and shows the log instead", async () => {
    vi.mocked(getJobRun).mockResolvedValue(makeJobRun({ id: 8, status: "SUCCESS", percent: 100, finished_at: "2026-08-10T11:00:00Z" }));
    vi.mocked(getJobRunLog).mockResolvedValue({ log_output: "all good" });

    renderWithProviders(<RunDetailPage />, { route: "/runs/8", path: "/runs/:id" });

    expect(await screen.findByText("Run #8")).toBeInTheDocument();
    // The run is terminal once loaded, so no "Connection: ..." banner is
    // shown and the socket the hook opens transiently before that data
    // arrives gets torn back down (enabled flips to false).
    expect(screen.queryByText(/Connection:/)).not.toBeInTheDocument();
    expect(FakeWebSocket.latest().close).toHaveBeenCalledWith(1000, "component unmounted");
    expect(await screen.findByText("all good")).toBeInTheDocument();
  });
});
