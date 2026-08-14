#!/bin/bash
set -euo pipefail

echo "[docker-entrypoint] running alembic upgrade head..."
uv run alembic upgrade head

echo "[docker-entrypoint] starting uvicorn..."
# --limit-concurrency 200: defense-in-depth against connection bursts (e.g. an agent replaying a large backlog of queued offline events after reconnecting)
# --timeout-keep-alive 90: uvicorn's default (5s) closes idle keep-alive connections faster than the agent's polling cadence (scheduler tick 15s / job-poll 30s / heartbeat 60s) reuses them, causing client-side SocketException 10054 + a server-side teardown-race RuntimeError; 90s comfortably exceeds the tightest 15s cadence with margin while still being bounded, not infinite
# NOTE: an idle keep-alive connection occupies a --limit-concurrency slot for its whole idle period, so raising keep-alive from 5s to 90s means a stuck/abandoned connection now holds a slot ~18x longer. Considered acceptable at the current small agent-fleet scale; revisit this trade-off (e.g. lower --limit-concurrency, or a stricter idle timeout) if the agent count grows enough that idle-slot exhaustion becomes plausible.
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --limit-concurrency 200 --timeout-keep-alive 90
