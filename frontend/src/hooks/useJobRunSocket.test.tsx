import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { useJobRunSocket } from "@/hooks/useJobRunSocket";
import { createTestQueryClient } from "@/test/renderWithProviders";
import { FakeWebSocket, installFakeWebSocket } from "@/test/fakeWebSocket";

// Fake timers are used throughout instead of RTL's `waitFor` (which polls
// via `setTimeout` and would otherwise deadlock against fake timers): every
// state transition triggered from the fake socket is synchronous once
// wrapped in `act`, so we can assert immediately.
describe("useJobRunSocket", () => {
  beforeEach(() => {
    installFakeWebSocket();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function wrapper({ children }: { children: ReactNode }) {
    const queryClient = createTestQueryClient();
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  it("connects and transitions to 'open' on the underlying socket's onopen", () => {
    const { result } = renderHook(() => useJobRunSocket({ jobRunId: 1, token: "tok" }), { wrapper });

    expect(result.current.state).toBe("connecting");

    act(() => {
      FakeWebSocket.latest().triggerOpen();
    });

    expect(result.current.state).toBe("open");
  });

  it("reconnects (with a new socket) after an abnormal close, and reports 'reconnecting' meanwhile", async () => {
    const { result } = renderHook(() => useJobRunSocket({ jobRunId: 1, token: "tok" }), { wrapper });

    act(() => {
      FakeWebSocket.latest().triggerOpen();
    });
    expect(result.current.state).toBe("open");

    const firstSocket = FakeWebSocket.latest();
    expect(FakeWebSocket.instances).toHaveLength(1);

    // Simulate an abnormal network drop (not a clean 1000 close).
    act(() => {
      firstSocket.triggerClose(1006, "abnormal closure");
    });

    expect(result.current.state).toBe("reconnecting");
    expect(result.current.reconnectAttempt).toBeGreaterThan(0);

    // Advance past the (jittered, <=1200ms for attempt 0) backoff delay.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(FakeWebSocket.instances[1]).not.toBe(firstSocket);

    act(() => {
      FakeWebSocket.latest().triggerOpen();
    });
    expect(result.current.state).toBe("open");
    // Successful reconnect resets the attempt counter.
    expect(result.current.reconnectAttempt).toBe(0);
  });

  it("does not reconnect after a clean (code 1000) close", async () => {
    const { result } = renderHook(() => useJobRunSocket({ jobRunId: 1, token: "tok" }), { wrapper });

    act(() => {
      FakeWebSocket.latest().triggerOpen();
    });
    expect(result.current.state).toBe("open");

    act(() => {
      FakeWebSocket.latest().triggerClose(1000, "normal");
    });

    expect(result.current.state).toBe("closed");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("closes the socket (with a clean 1000 code) on unmount", () => {
    const { result, unmount } = renderHook(() => useJobRunSocket({ jobRunId: 9, token: "tok" }), { wrapper });

    act(() => {
      FakeWebSocket.latest().triggerOpen();
    });
    expect(result.current.state).toBe("open");

    const socket = FakeWebSocket.latest();
    expect(socket.close).not.toHaveBeenCalled();

    unmount();

    expect(socket.close).toHaveBeenCalledWith(1000, "component unmounted");
  });

  it("does not open a new socket after unmount even if a reconnect was scheduled", async () => {
    const { result, unmount } = renderHook(() => useJobRunSocket({ jobRunId: 9, token: "tok" }), { wrapper });

    act(() => {
      FakeWebSocket.latest().triggerOpen();
    });
    act(() => {
      FakeWebSocket.latest().triggerClose(1006, "abnormal closure");
    });
    expect(result.current.state).toBe("reconnecting");

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(35000);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("stays idle when there is no token", () => {
    const { result } = renderHook(() => useJobRunSocket({ jobRunId: 1, token: null }), { wrapper });
    expect(result.current.state).toBe("idle");
    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});
