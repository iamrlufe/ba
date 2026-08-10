import { vi } from "vitest";

/**
 * Minimal fake WebSocket for testing `useJobRunSocket` and anything that
 * consumes it, without spinning up a real socket/server. Install on
 * `globalThis.WebSocket` before rendering, and drive it from the test via
 * `triggerOpen`/`triggerMessage`/`triggerClose`.
 */
export class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState: number = FakeWebSocket.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;

  close = vi.fn((_code?: number, _reason?: string) => {
    this.readyState = FakeWebSocket.CLOSED;
  });

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  triggerOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  triggerMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  triggerClose(code: number, reason = "") {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code, reason }));
  }

  static reset() {
    FakeWebSocket.instances = [];
  }

  static latest(): FakeWebSocket {
    const instance = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    if (!instance) throw new Error("No FakeWebSocket instance has been created yet");
    return instance;
  }
}

export function installFakeWebSocket() {
  FakeWebSocket.reset();
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
  return FakeWebSocket;
}
