"""ORM-level tests for hybrid_properties defined directly on model classes
(as opposed to tests/test_schemas.py, which exercises the Pydantic
Read-schema wiring on top of them).

Currently just BackupJob.remote_directory (app/models/backup_job.py) --
mirrors the sql_instance_host/-port/-instance_name/-use_windows_auth
hybrid_properties' "return None instead of raising when the relationship
isn't eager-loaded" convention documented right above it in that file.
"""
from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.orm import selectinload

from app.models.backup_job import BackupJob
from app.models.enums import BackupType
from tests.conftest import build_backup_job, build_disk, build_server


async def _server_and_disk(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    return server, disk


async def test_remote_directory_computed_when_server_loaded(session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(
        server.id, disk.id, name="Nightly AdventureWorks Diff", backup_type=BackupType.DIFFERENTIAL
    )
    session.add(job)
    await session.commit()

    # Reload via a fresh query with `server` eager-loaded, exactly like the
    # router call sites (list_backup_jobs/get_backup_job/etc.) do.
    result = await session.execute(
        select(BackupJob).options(selectinload(BackupJob.server)).where(BackupJob.id == job.id)
    )
    orm_job = result.scalar_one()

    assert "server" not in inspect(orm_job).unloaded
    assert orm_job.remote_directory == f"{server.name}/Nightly_AdventureWorks_Diff_{job.id}/DIFF/"


async def test_remote_directory_none_when_server_not_loaded(session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()
    # A plain refresh() does NOT reload relationships that were never
    # eager-loaded in the first place -- `server` stays unloaded here,
    # matching the create_backup_job docstring's explanation in
    # app/routers/backup_jobs.py.
    await session.refresh(job)

    assert "server" in inspect(job).unloaded
    assert job.remote_directory is None


async def test_remote_directory_override_returned_literally_ignoring_computed_value(session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(
        server.id, disk.id, remote_directory_override="hand/picked/path"
    )
    session.add(job)
    await session.commit()

    result = await session.execute(
        select(BackupJob).options(selectinload(BackupJob.server)).where(BackupJob.id == job.id)
    )
    orm_job = result.scalar_one()

    assert orm_job.remote_directory == "hand/picked/path"
