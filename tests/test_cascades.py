"""FK cascade/restrict/set-null behavior tests.

These are the single most important tests in this suite: SQLite ignores
foreign keys entirely unless `PRAGMA foreign_keys=ON` is set on every raw
connection (see app/core/db.py), and that's easy to silently regress.

All deletes here go through Core `delete()` statements (not
`session.delete(orm_obj)`), specifically so we're exercising the database's
own ON DELETE behavior rather than SQLAlchemy's ORM-level unit-of-work
cascade/nullify logic, which could otherwise mask a missing/broken DB-level
constraint.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.models import Alert, BackupJob, BackupRecord, Disk, JobRun, RestoreOperation, Server, SqlInstance
from tests.conftest import (
    build_alert,
    build_backup_job,
    build_backup_record,
    build_disk,
    build_job_run,
    build_restore_operation,
    build_server,
    build_sql_instance,
)


async def _server_with_disk(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    return server, disk


async def _server_disk_job(session, **job_overrides):
    server, disk = await _server_with_disk(session)
    job = build_backup_job(server.id, disk.id, **job_overrides)
    session.add(job)
    await session.commit()
    return server, disk, job


# --------------------------------------------------------------------------
# Server / Disk / SqlInstance / BackupJob
# --------------------------------------------------------------------------


async def test_delete_server_cascades_to_disks(session):
    server, disk = await _server_with_disk(session)
    disk_id = disk.id

    await session.execute(delete(Server).where(Server.id == server.id))
    await session.commit()
    session.expire_all()

    assert await session.get(Disk, disk_id) is None
    assert await session.get(Server, server.id) is None


async def test_delete_server_with_backup_job_is_restricted(session):
    server, disk, job = await _server_disk_job(session)
    server_id, job_id = server.id, job.id

    with pytest.raises(IntegrityError):
        await session.execute(delete(Server).where(Server.id == server_id))
        await session.commit()
    await session.rollback()

    # Nothing was actually removed. Note: rollback() expires all objects in
    # the session, so we must use the plain int ids captured above (not
    # `server.id`/`job.id` attribute access) -- touching an expired
    # attribute on an AsyncSession-bound object outside of an awaited ORM
    # call raises MissingGreenlet, it does not implicitly refresh.
    session.expire_all()
    assert await session.get(Server, server_id) is not None
    assert await session.get(BackupJob, job_id) is not None


async def test_delete_disk_with_backup_job_is_restricted(session):
    server, disk, job = await _server_disk_job(session)
    disk_id = disk.id

    with pytest.raises(IntegrityError):
        await session.execute(delete(Disk).where(Disk.id == disk_id))
        await session.commit()
    await session.rollback()

    session.expire_all()
    assert await session.get(Disk, disk_id) is not None


async def test_delete_sql_instance_sets_backup_job_fk_null(session):
    server, disk = await _server_with_disk(session)
    sql_instance = build_sql_instance()
    session.add(sql_instance)
    await session.commit()

    job = build_backup_job(
        server.id, disk.id, sql_instance_id=sql_instance.id, verification_method="RESTORE_VERIFY"
    )
    session.add(job)
    await session.commit()
    job_id = job.id

    await session.execute(delete(SqlInstance).where(SqlInstance.id == sql_instance.id))
    await session.commit()
    session.expire_all()

    refreshed = await session.get(BackupJob, job_id)
    assert refreshed is not None
    assert refreshed.sql_instance_id is None


async def test_insert_backup_job_with_missing_server_id_fails(session):
    server, disk = await _server_with_disk(session)

    with pytest.raises(IntegrityError):
        job = build_backup_job(server_id=999_999, disk_id=disk.id)
        session.add(job)
        await session.commit()
    await session.rollback()


async def test_insert_backup_job_with_missing_disk_id_fails(session):
    server, disk = await _server_with_disk(session)

    with pytest.raises(IntegrityError):
        job = build_backup_job(server_id=server.id, disk_id=999_999)
        session.add(job)
        await session.commit()
    await session.rollback()


# --------------------------------------------------------------------------
# BackupJob -> JobRun / BackupRecord / RestoreOperation
# --------------------------------------------------------------------------


async def test_delete_backup_job_cascades_runs_and_records(session):
    server, disk, job = await _server_disk_job(session)

    run = build_job_run(job.id)
    session.add(run)
    await session.commit()

    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    run_id, record_id = run.id, record.id

    await session.execute(delete(BackupJob).where(BackupJob.id == job.id))
    await session.commit()
    session.expire_all()

    assert await session.get(JobRun, run_id) is None
    assert await session.get(BackupRecord, record_id) is None


async def test_delete_backup_job_with_restore_operation_is_restricted(session):
    server, disk, job = await _server_disk_job(session)
    sql_instance = build_sql_instance()
    session.add(sql_instance)
    await session.commit()

    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    restore = build_restore_operation(record.id, sql_instance.id)
    session.add(restore)
    await session.commit()

    job_id, record_id, restore_id = job.id, record.id, restore.id

    with pytest.raises(IntegrityError):
        await session.execute(delete(BackupJob).where(BackupJob.id == job_id))
        await session.commit()
    await session.rollback()

    session.expire_all()
    # Nothing was deleted -- the whole cascaded delete was rolled back.
    assert await session.get(BackupJob, job_id) is not None
    assert await session.get(BackupRecord, record_id) is not None
    assert await session.get(RestoreOperation, restore_id) is not None


# --------------------------------------------------------------------------
# Alert SET NULL fan-in
# --------------------------------------------------------------------------


async def test_delete_server_sets_alert_server_id_null(session):
    server = build_server()
    session.add(server)
    await session.commit()

    alert = build_alert(entity_type="server", server_id=server.id)
    session.add(alert)
    await session.commit()
    alert_id = alert.id

    await session.execute(delete(Server).where(Server.id == server.id))
    await session.commit()
    session.expire_all()

    refreshed = await session.get(Alert, alert_id)
    assert refreshed is not None
    assert refreshed.server_id is None


async def test_delete_disk_sets_alert_disk_id_null(session):
    server, disk = await _server_with_disk(session)

    alert = build_alert(entity_type="disk", disk_id=disk.id)
    session.add(alert)
    await session.commit()
    alert_id = alert.id

    await session.execute(delete(Disk).where(Disk.id == disk.id))
    await session.commit()
    session.expire_all()

    refreshed = await session.get(Alert, alert_id)
    assert refreshed is not None
    assert refreshed.disk_id is None


async def test_delete_backup_job_sets_alert_backup_job_id_null(session):
    server, disk, job = await _server_disk_job(session)

    alert = build_alert(entity_type="backup_job", backup_job_id=job.id)
    session.add(alert)
    await session.commit()
    alert_id = alert.id

    await session.execute(delete(BackupJob).where(BackupJob.id == job.id))
    await session.commit()
    session.expire_all()

    refreshed = await session.get(Alert, alert_id)
    assert refreshed is not None
    assert refreshed.backup_job_id is None


async def test_delete_job_run_sets_alert_job_run_id_null(session):
    server, disk, job = await _server_disk_job(session)
    run = build_job_run(job.id)
    session.add(run)
    await session.commit()

    alert = build_alert(entity_type="job_run", job_run_id=run.id)
    session.add(alert)
    await session.commit()
    alert_id = alert.id

    await session.execute(delete(JobRun).where(JobRun.id == run.id))
    await session.commit()
    session.expire_all()

    refreshed = await session.get(Alert, alert_id)
    assert refreshed is not None
    assert refreshed.job_run_id is None


async def test_delete_restore_operation_sets_alert_restore_operation_id_null(session):
    server, disk, job = await _server_disk_job(session)
    sql_instance = build_sql_instance()
    session.add(sql_instance)
    await session.commit()
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()
    restore = build_restore_operation(record.id, sql_instance.id)
    session.add(restore)
    await session.commit()

    alert = build_alert(entity_type="restore_operation", restore_operation_id=restore.id)
    session.add(alert)
    await session.commit()
    alert_id = alert.id

    await session.execute(delete(RestoreOperation).where(RestoreOperation.id == restore.id))
    await session.commit()
    session.expire_all()

    refreshed = await session.get(Alert, alert_id)
    assert refreshed is not None
    assert refreshed.restore_operation_id is None
