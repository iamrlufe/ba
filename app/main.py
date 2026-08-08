"""FastAPI application entrypoint.

Wires together the DB engine lifecycle, the global IntegrityError -> 409
handler, the in-memory JobRun WebSocket connection manager, the
bootstrap-admin startup step, and every resource router.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.auth import hash_password
from app.core.config import settings
from app.core.db import async_session_maker, engine
from app.core.errors import integrity_error_handler
from app.core.ws_manager import manager
from app.models.enums import UserRole
from app.models.user import User
from app.routers import (
    agents,
    alerts,
    auth,
    backup_jobs,
    backup_records,
    disks,
    job_run_ws,
    job_runs,
    restore_operations,
    servers,
    sql_instances,
    users,
)


async def _bootstrap_admin() -> None:
    if not settings.BOOTSTRAP_ADMIN_USERNAME and not settings.BOOTSTRAP_ADMIN_PASSWORD:
        return
    if bool(settings.BOOTSTRAP_ADMIN_USERNAME) != bool(settings.BOOTSTRAP_ADMIN_PASSWORD):
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD must both be set, or both unset"
        )
    async with async_session_maker() as session:
        existing = await session.execute(
            select(User).where(User.username == settings.BOOTSTRAP_ADMIN_USERNAME)
        )
        if existing.scalar_one_or_none() is not None:
            return
        session.add(User(
            username=settings.BOOTSTRAP_ADMIN_USERNAME,
            hashed_password=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
            role=UserRole.ADMIN,
            is_active=True,
        ))
        try:
            await session.commit()
        except IntegrityError:
            # Concurrent worker/process already created it -- treat as success,
            # not a startup failure (see §10.2 concurrency note in the auth spec).
            await session.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _bootstrap_admin()
    yield
    await engine.dispose()


app = FastAPI(title="Backup Orchestrator", lifespan=lifespan)

app.add_exception_handler(IntegrityError, integrity_error_handler)

# Kept on app.state for testability (e.g. tests overriding/inspecting the
# manager via the app instance); routers themselves import the module-level
# `manager` singleton directly, not via `request.app.state`.
app.state.job_run_ws_manager = manager

app.include_router(auth.router, prefix="/api/auth")
app.include_router(users.router, prefix="/api/users")
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
