#!/bin/bash
set -euo pipefail

echo "[docker-entrypoint] running alembic upgrade head..."
uv run alembic upgrade head

echo "[docker-entrypoint] starting uvicorn..."
# --limit-concurrency 200: defense-in-depth against connection bursts (e.g. an agent replaying a large backlog of queued offline events after reconnecting)
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --limit-concurrency 200
