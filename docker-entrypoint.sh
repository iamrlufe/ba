#!/bin/bash
set -euo pipefail

echo "[docker-entrypoint] running alembic upgrade head..."
uv run alembic upgrade head

echo "[docker-entrypoint] starting uvicorn..."
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
