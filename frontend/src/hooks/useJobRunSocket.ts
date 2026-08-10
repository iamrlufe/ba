import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/api/queryKeys";
import { isJobRunTerminal, type JobRunRead } from "@/api/types";

export type JobRunSocketState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error";

export interface UseJobRunSocketOptions {
  jobRunId: number;
  /** From AuthContext; the hook stays "idle" while null. */
  token: string | null;
  enabled?: boolean;
}

export interface UseJobRunSocketResult {
  state: JobRunSocketState;
  lastMessage: JobRunRead | null;
  lastError: string | null;
  /** For "reconnecting… (attempt N)" UI. */
  reconnectAttempt: number;
}

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;

function backoffDelay(attempt: number): number {
  const raw = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS);
  const jitter = raw * 0.2 * (Math.random() * 2 - 1); // +/-20%
  return Math.max(0, Math.round(raw + jitter));
}

/**
 * Subscribes to `/ws/job-runs/{jobRunId}` (no `/api` prefix -- same-origin
 * via the dev proxy / prod reverse proxy). Every parsed message is written
 * straight into the TanStack Query cache at
 * `queryKeys.jobRuns.detail(jobRunId)`; this hook holds no parallel copy of
 * the run in component state (`lastMessage` below is exposed only for
 * connection-state UI, e.g. showing a "reconnecting" banner alongside the
 * cache-driven data).
 */
export function useJobRunSocket({ jobRunId, token, enabled = true }: UseJobRunSocketOptions): UseJobRunSocketResult {
  const queryClient = useQueryClient();
  const [state, setState] = useState<JobRunSocketState>("idle");
  const [lastMessage, setLastMessage] = useState<JobRunRead | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const stoppedRef = useRef(false);

  useEffect(() => {
    stoppedRef.current = false;

    if (!enabled || !token) {
      setState("idle");
      return;
    }

    function clearTimer() {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    }

    function scheduleReconnect() {
      if (stoppedRef.current) return;
      const attempt = attemptRef.current;
      const delay = backoffDelay(attempt);
      setState("reconnecting");
      setReconnectAttempt(attempt + 1);
      timerRef.current = setTimeout(() => {
        attemptRef.current += 1;
        connect();
      }, delay);
    }

    function connect() {
      if (stoppedRef.current) return;
      setState((prev) => (prev === "reconnecting" ? "reconnecting" : "connecting"));

      const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${wsProtocol}://${window.location.host}/ws/job-runs/${jobRunId}?token=${encodeURIComponent(
        token as string,
      )}`;

      const socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => {
        if (stoppedRef.current) return;
        attemptRef.current = 0;
        setReconnectAttempt(0);
        setState("open");
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        if (stoppedRef.current) return;
        let parsed: JobRunRead | null = null;
        try {
          parsed = JSON.parse(event.data) as JobRunRead;
        } catch {
          return;
        }
        if (!parsed || typeof parsed !== "object" || !("status" in parsed)) return;

        setLastMessage(parsed);
        queryClient.setQueryData(queryKeys.jobRuns.detail(jobRunId), parsed);

        if (isJobRunTerminal(parsed.status)) {
          // Defensive: stop reconnecting even before a close frame arrives.
          stoppedRef.current = true;
          clearTimer();
        }
      };

      socket.onclose = (event: CloseEvent) => {
        if (socketRef.current !== socket) return; // stale handler from a previous socket
        socketRef.current = null;

        if (event.code === 1000) {
          stoppedRef.current = true;
          setState("closed");
          return;
        }
        if (event.code === 4401) {
          stoppedRef.current = true;
          setLastError("Session expired, please refresh.");
          setState("error");
          return;
        }
        if (event.code === 4404) {
          stoppedRef.current = true;
          setLastError("Job run not found.");
          setState("error");
          return;
        }
        if (stoppedRef.current) {
          // Already stopped defensively (terminal status seen in a message).
          setState("closed");
          return;
        }
        // Network drop / server restart / anything else: reconnect.
        scheduleReconnect();
      };

      socket.onerror = () => {
        // onclose fires right after onerror for a failed connection; let
        // onclose own the state transition/reconnect scheduling.
      };
    }

    connect();

    return () => {
      stoppedRef.current = true;
      clearTimer();
      const socket = socketRef.current;
      socketRef.current = null;
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
          socket.close(1000, "component unmounted");
        }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobRunId, token, enabled]);

  return { state, lastMessage, lastError, reconnectAttempt };
}
