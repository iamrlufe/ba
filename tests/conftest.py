"""Shared pytest fixtures + tiny model-builder helpers for the model/schema tests.

Uses an in-memory SQLite database via `sqlite+aiosqlite:///:memory:` with a
`StaticPool` so that a single underlying DBAPI connection is reused for the
whole engine lifetime (a bare in-memory SQLite connection is otherwise a
fresh, empty database per checkout, which would make tables invisible
across the multiple connections a normal async pool hands out).

Importing `app.core.db` here (even though we build our own engine) is
required: that module registers `PRAGMA foreign_keys=ON` via
`@event.listens_for(Engine, "connect")`, and that decorator targets the
*base* `sqlalchemy.engine.Engine` class, not one specific engine instance.
So the same listener fires for any engine created anywhere in the process,
including the throwaway test engine created below, as long as
`app.core.db` has been imported at least once first.
"""
from __future__ import annotations

import itertools

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.db  # noqa: F401 -- registers the PRAGMA foreign_keys=ON listener globally
import app.models  # noqa: F401 -- populates Base.metadata with every model
from app.models import (
    Alert,
    BackupJob,
    BackupRecord,
    Disk,
    JobRun,
    RestoreOperation,
    Server,
    SqlInstance,
)
from app.models.base import Base
from app.models.enums import (
    AlertSeverity,
    AlertType,
    ProtocolType,
    RequestChannel,
    RestoreMode,
)

_counter = itertools.count(1)


def _next() -> int:
    return next(_counter)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()


# --------------------------------------------------------------------------
# Builder helpers -- construct (but do not add/commit) ORM instances with
# sane defaults + unique names, so tests only need to override what matters
# for the behavior under test.
# --------------------------------------------------------------------------


def build_server(**overrides) -> Server:
    n = _next()
    defaults = dict(
        name=f"server-{n}",
        host="10.0.0.1",
        port=22,
        protocol=ProtocolType.SFTP,
    )
    defaults.update(overrides)
    return Server(**defaults)


def build_sql_instance(**overrides) -> SqlInstance:
    n = _next()
    defaults = dict(
        name=f"sql-instance-{n}",
        host="10.0.0.2",
        use_windows_auth=True,
    )
    defaults.update(overrides)
    return SqlInstance(**defaults)


def build_disk(server_id: int, **overrides) -> Disk:
    n = _next()
    defaults = dict(
        server_id=server_id,
        label=f"disk-{n}",
        mount_path=f"/mnt/data-{n}",
    )
    defaults.update(overrides)
    return Disk(**defaults)


def build_backup_job(server_id: int, disk_id: int, **overrides) -> BackupJob:
    n = _next()
    defaults = dict(
        server_id=server_id,
        disk_id=disk_id,
        name=f"job-{n}",
        source_path=f"/data/source-{n}",
        schedule_cron="0 * * * *",
    )
    defaults.update(overrides)
    return BackupJob(**defaults)


def build_job_run(backup_job_id: int, **overrides) -> JobRun:
    defaults: dict = dict(backup_job_id=backup_job_id)
    defaults.update(overrides)
    return JobRun(**defaults)


def build_backup_record(backup_job_id: int, **overrides) -> BackupRecord:
    n = _next()
    defaults = dict(
        backup_job_id=backup_job_id,
        file_name=f"file-{n}.bak",
        remote_path=f"/remote/path-{n}",
        file_size_bytes=1024,
    )
    defaults.update(overrides)
    return BackupRecord(**defaults)


def build_restore_operation(backup_record_id: int, sql_instance_id: int, **overrides) -> RestoreOperation:
    n = _next()
    defaults = dict(
        backup_record_id=backup_record_id,
        sql_instance_id=sql_instance_id,
        database_name=f"db-{n}",
        mode=RestoreMode.ALL,
        requested_by="tester",
        requested_by_channel=RequestChannel.WEB,
    )
    defaults.update(overrides)
    return RestoreOperation(**defaults)


def build_alert(**overrides) -> Alert:
    defaults = dict(
        alert_type=AlertType.JOB_FAILED,
        severity=AlertSeverity.WARNING,
        entity_type="server",
        title="something happened",
        message="details",
    )
    defaults.update(overrides)
    return Alert(**defaults)
