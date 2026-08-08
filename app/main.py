"""FastAPI application entrypoint.

Wires together the DB engine lifecycle, the global IntegrityError -> 409
handler, the in-memory JobRun WebSocket connection manager, and every
resource router.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from app.core.db import engine
from app.core.errors import integrity_error_handler
from app.core.ws_manager import manager
from app.routers import (
    agents,
    alerts,
    backup_jobs,
    backup_records,
    disks,
    job_run_ws,
    job_runs,
    restore_operations,
    servers,
    sql_instances,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app = FastAPI(title="Backup Orchestrator", lifespan=lifespan)

app.add_exception_handler(IntegrityError, integrity_error_handler)

# Kept on app.state for testability (e.g. tests overriding/inspecting the
# manager via the app instance); routers themselves import the module-level
# `manager` singleton directly, not via `request.app.state`.
app.state.job_run_ws_manager = manager

app.include_router(servers.router, prefix="/api/servers")
app.include_router(sql_instances.router, prefix="/api/sql-instances")
app.include_router(disks.router, prefix="/api/disks")
app.include_router(backup_jobs.router, prefix="/api/backup-jobs")
app.include_router(agents.router, prefix="/api/agents")
app.include_router(job_runs.router, prefix="/api/job-runs")
app.include_router(backup_records.router, prefix="/api/backup-records")
app.include_router(restore_operations.router, prefix="/api/restore-operations")
app.include_router(alerts.router, prefix="/api/alerts")
app.include_router(job_run_ws.router)
