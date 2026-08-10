"""Partial-unique-index and CHECK-constraint regression tests.

Partial unique indexes are SQLite's `sqlite_where=...` feature and are the
only defense against double-processing (two active JobRuns for the same
job, two active RestoreOperations for the same database, two ACTIVE Alerts
of the same kind). They are easy to accidentally drop or mis-scope, so each
one gets both a "blocked" and an "allowed" test.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    JobRunStatus,
    RestoreStatus,
    VerificationRunStatus,
    VerificationType,
)
from app.models.verification_run import VerificationRun
from tests.conftest import (
    build_alert,
    build_backup_job,
    build_backup_record,
    build_disk,
    build_job_run,
    build_restore_operation,
    build_server,
    build_sql_instance,
    build_verification_run,
)


async def _server_disk_job(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()
    return server, disk, job


# --------------------------------------------------------------------------
# JobRun partial unique index: at most one PENDING/RUNNING per backup_job_id
# --------------------------------------------------------------------------


async def test_second_active_job_run_for_same_job_is_rejected(session):
    _, _, job = await _server_disk_job(session)

    run1 = build_job_run(job.id, status=JobRunStatus.PENDING)
    session.add(run1)
    await session.commit()

    run2 = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_terminal_job_run_does_not_block_new_active_run(session):
    _, _, job = await _server_disk_job(session)

    run1 = build_job_run(job.id, status=JobRunStatus.SUCCESS)
    session.add(run1)
    await session.commit()

    run2 = build_job_run(job.id, status=JobRunStatus.FAILED)
    session.add(run2)
    await session.commit()

    run3 = build_job_run(job.id, status=JobRunStatus.PENDING)
    session.add(run3)
    await session.commit()

    assert run3.id is not None


# --------------------------------------------------------------------------
# RestoreOperation partial unique index: at most one PENDING/RUNNING per
# (sql_instance_id, database_name)
# --------------------------------------------------------------------------


async def test_second_active_restore_for_same_instance_and_db_is_rejected(session):
    _, _, job = await _server_disk_job(session)
    sql_instance = build_sql_instance()
    session.add(sql_instance)
    await session.commit()
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    restore1 = build_restore_operation(
        record.id, sql_instance.id, database_name="orders", status=RestoreStatus.PENDING
    )
    session.add(restore1)
    await session.commit()

    restore2 = build_restore_operation(
        record.id, sql_instance.id, database_name="orders", status=RestoreStatus.RUNNING
    )
    session.add(restore2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_active_restores_for_different_databases_are_allowed(session):
    _, _, job = await _server_disk_job(session)
    sql_instance = build_sql_instance()
    session.add(sql_instance)
    await session.commit()
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    restore1 = build_restore_operation(
        record.id, sql_instance.id, database_name="orders", status=RestoreStatus.PENDING
    )
    session.add(restore1)
    await session.commit()

    restore2 = build_restore_operation(
        record.id, sql_instance.id, database_name="invoices", status=RestoreStatus.PENDING
    )
    session.add(restore2)
    await session.commit()

    assert restore2.id is not None


async def test_terminal_restore_does_not_block_new_active_restore(session):
    _, _, job = await _server_disk_job(session)
    sql_instance = build_sql_instance()
    session.add(sql_instance)
    await session.commit()
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    restore1 = build_restore_operation(
        record.id, sql_instance.id, database_name="orders", status=RestoreStatus.DONE
    )
    session.add(restore1)
    await session.commit()

    restore2 = build_restore_operation(
        record.id, sql_instance.id, database_name="orders", status=RestoreStatus.FAILED
    )
    session.add(restore2)
    await session.commit()

    restore3 = build_restore_operation(
        record.id, sql_instance.id, database_name="orders", status=RestoreStatus.PENDING
    )
    session.add(restore3)
    await session.commit()

    assert restore3.id is not None


# --------------------------------------------------------------------------
# BackupRecord unique(backup_job_id, remote_path, file_name) -- ordinary,
# not partial.
# --------------------------------------------------------------------------


async def test_duplicate_backup_record_identity_is_rejected(session):
    _, _, job = await _server_disk_job(session)

    record1 = build_backup_record(job.id, remote_path="/remote/a", file_name="dump.bak")
    session.add(record1)
    await session.commit()

    record2 = build_backup_record(job.id, remote_path="/remote/a", file_name="dump.bak")
    session.add(record2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


# --------------------------------------------------------------------------
# Alert active-dedupe partial unique index
# --------------------------------------------------------------------------


async def test_second_active_alert_of_same_kind_is_rejected(session):
    """Documented/intended behavior: at most one ACTIVE alert may exist per
    (entity_type, entity, alert_type). Enforced by the partial unique index
    `uq_alerts_active_dedupe` on (entity_type, entity_key, alert_type) WHERE
    status='ACTIVE', where `entity_key` is a generated column collapsing the
    five nullable per-entity FK columns via COALESCE (see
    app.models.alert.Alert docstring / `_ENTITY_KEY_SQL`). This is needed
    because a naive composite unique index directly over the five nullable
    FK columns would never fire, since SQL NULL is never equal to NULL for
    UNIQUE purposes and at least four of the five FK columns are always NULL
    on any given row.
    """
    server = build_server()
    session.add(server)
    await session.commit()

    alert1 = build_alert(
        entity_type="server",
        server_id=server.id,
        alert_type=AlertType.SERVER_UNREACHABLE,
        status=AlertStatus.ACTIVE,
    )
    session.add(alert1)
    await session.commit()

    alert2 = build_alert(
        entity_type="server",
        server_id=server.id,
        alert_type=AlertType.SERVER_UNREACHABLE,
        status=AlertStatus.ACTIVE,
    )
    session.add(alert2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_active_alerts_for_different_entities_of_same_kind_are_allowed(session):
    """Regression test for the `entity_key` generated-column fix: two ACTIVE
    alerts sharing the same entity_type and alert_type but pointing at
    *different* entities (here, two different disks) must NOT be treated as
    duplicates -- entity_key = COALESCE(...) must discriminate between
    distinct FK values, not just between NULL and non-NULL.
    """
    server = build_server()
    session.add(server)
    await session.commit()

    disk1 = build_disk(server.id)
    disk2 = build_disk(server.id)
    session.add_all([disk1, disk2])
    await session.commit()

    alert1 = build_alert(
        entity_type="disk",
        disk_id=disk1.id,
        alert_type=AlertType.DISK_SPACE_CRITICAL,
        status=AlertStatus.ACTIVE,
    )
    session.add(alert1)
    await session.commit()

    alert2 = build_alert(
        entity_type="disk",
        disk_id=disk2.id,
        alert_type=AlertType.DISK_SPACE_CRITICAL,
        status=AlertStatus.ACTIVE,
    )
    session.add(alert2)
    await session.commit()

    assert alert1.id is not None
    assert alert2.id is not None
    assert alert1.id != alert2.id


async def test_resolved_alert_does_not_block_new_active_alert_of_same_kind(session):
    server = build_server()
    session.add(server)
    await session.commit()

    alert1 = build_alert(
        entity_type="server",
        server_id=server.id,
        alert_type=AlertType.SERVER_UNREACHABLE,
        status=AlertStatus.RESOLVED,
    )
    session.add(alert1)
    await session.commit()

    alert2 = build_alert(
        entity_type="server",
        server_id=server.id,
        alert_type=AlertType.SERVER_UNREACHABLE,
        status=AlertStatus.ACTIVE,
    )
    session.add(alert2)
    await session.commit()

    assert alert2.id is not None


# --------------------------------------------------------------------------
# CHECK constraints
# --------------------------------------------------------------------------


async def test_disk_warning_threshold_not_below_critical_is_rejected(session):
    server = build_server()
    session.add(server)
    await session.commit()

    with pytest.raises(IntegrityError):
        disk = build_disk(server.id, warning_threshold_pct=90, critical_threshold_pct=90)
        session.add(disk)
        await session.commit()
    await session.rollback()


async def test_disk_warning_threshold_above_critical_is_rejected(session):
    server = build_server()
    session.add(server)
    await session.commit()

    with pytest.raises(IntegrityError):
        disk = build_disk(server.id, warning_threshold_pct=95, critical_threshold_pct=90)
        session.add(disk)
        await session.commit()
    await session.rollback()


async def test_backup_record_negative_file_size_is_rejected(session):
    _, _, job = await _server_disk_job(session)

    with pytest.raises(IntegrityError):
        record = build_backup_record(job.id, file_size_bytes=-1)
        session.add(record)
        await session.commit()
    await session.rollback()


async def test_alert_with_two_entity_fks_set_is_rejected(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()

    with pytest.raises(IntegrityError):
        alert = build_alert(entity_type="server", server_id=server.id, disk_id=disk.id)
        session.add(alert)
        await session.commit()
    await session.rollback()


async def test_alert_with_zero_entity_fks_set_is_allowed(session):
    # A legitimate state: e.g. the parent entity was since deleted (SET
    # NULL) -- the CHECK is "at most one", not "exactly one".
    alert = build_alert(entity_type="server")
    session.add(alert)
    await session.commit()
    assert alert.id is not None


# --------------------------------------------------------------------------
# FTP copy-integrity: Alert.backup_record_id (6th entity FK) + entity_key/
# uq_alerts_active_dedupe coverage, VerificationRun.verification_type.
# --------------------------------------------------------------------------


async def test_alert_with_backup_record_and_another_entity_fk_set_is_rejected(session):
    _, _, job = await _server_disk_job(session)
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    with pytest.raises(IntegrityError):
        alert = build_alert(
            entity_type="backup_record", backup_record_id=record.id, backup_job_id=job.id
        )
        session.add(alert)
        await session.commit()
    await session.rollback()


async def test_alert_with_only_backup_record_fk_set_is_allowed(session):
    _, _, job = await _server_disk_job(session)
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    alert = build_alert(entity_type="backup_record", backup_record_id=record.id)
    session.add(alert)
    await session.commit()
    assert alert.id is not None


async def test_second_active_alert_for_same_backup_record_is_rejected(session):
    _, _, job = await _server_disk_job(session)
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    alert1 = build_alert(
        entity_type="backup_record",
        backup_record_id=record.id,
        alert_type=AlertType.FTP_COPY_INTEGRITY_FAILED,
        status=AlertStatus.ACTIVE,
    )
    session.add(alert1)
    await session.commit()

    alert2 = build_alert(
        entity_type="backup_record",
        backup_record_id=record.id,
        alert_type=AlertType.FTP_COPY_INTEGRITY_FAILED,
        status=AlertStatus.ACTIVE,
    )
    session.add(alert2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_active_alerts_for_different_backup_records_are_allowed(session):
    """Regression test for the entity_key generated-column fix, specific
    to the new 6th FK column: two ACTIVE alerts sharing the same
    entity_type/alert_type but pointing at *different* backup_record_id
    values must not be treated as duplicates."""
    _, _, job = await _server_disk_job(session)
    record1 = build_backup_record(job.id)
    record2 = build_backup_record(job.id)
    session.add_all([record1, record2])
    await session.commit()

    alert1 = build_alert(
        entity_type="backup_record",
        backup_record_id=record1.id,
        alert_type=AlertType.FTP_COPY_INTEGRITY_FAILED,
        status=AlertStatus.ACTIVE,
    )
    alert2 = build_alert(
        entity_type="backup_record",
        backup_record_id=record2.id,
        alert_type=AlertType.FTP_COPY_INTEGRITY_FAILED,
        status=AlertStatus.ACTIVE,
    )
    session.add_all([alert1, alert2])
    await session.commit()

    assert alert1.id is not None
    assert alert2.id is not None
    assert alert1.id != alert2.id


async def test_resolved_backup_record_alert_does_not_block_new_active_alert(session):
    _, _, job = await _server_disk_job(session)
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()

    alert1 = build_alert(
        entity_type="backup_record",
        backup_record_id=record.id,
        alert_type=AlertType.FTP_COPY_INTEGRITY_FAILED,
        status=AlertStatus.RESOLVED,
    )
    session.add(alert1)
    await session.commit()

    alert2 = build_alert(
        entity_type="backup_record",
        backup_record_id=record.id,
        alert_type=AlertType.FTP_COPY_INTEGRITY_FAILED,
        status=AlertStatus.ACTIVE,
    )
    session.add(alert2)
    await session.commit()

    assert alert2.id is not None


async def test_verification_run_defaults_to_restore_verifyonly(session):
    _, _, job = await _server_disk_job(session)

    run = build_verification_run(job.id)
    session.add(run)
    await session.commit()
    await session.refresh(run)

    assert run.verification_type == VerificationType.RESTORE_VERIFYONLY


async def test_multiple_terminal_ftp_copy_integrity_runs_for_same_job_are_allowed(session):
    """uq_verification_runs_active_per_backup_job only blocks PENDING/
    RUNNING rows -- FTP_COPY_INTEGRITY rows are always inserted with an
    already-terminal status, so multiple of them per backup_job_id must
    never collide with that partial unique index (full history is
    intentional -- spec section 6, item 5)."""
    _, _, job = await _server_disk_job(session)

    run1 = build_verification_run(
        job.id,
        verification_type=VerificationType.FTP_COPY_INTEGRITY,
        status=VerificationRunStatus.OK,
        triggered_by="agent",
    )
    run2 = build_verification_run(
        job.id,
        verification_type=VerificationType.FTP_COPY_INTEGRITY,
        status=VerificationRunStatus.CORRUPT,
        triggered_by="agent",
    )
    session.add_all([run1, run2])
    await session.commit()

    assert run1.id is not None
    assert run2.id is not None
    assert run1.id != run2.id


async def test_ftp_copy_integrity_run_does_not_block_pending_restore_verifyonly_run(session):
    """A terminal FTP_COPY_INTEGRITY run for a job must not interfere with
    that job's own PENDING/RUNNING RESTORE_VERIFYONLY exclusivity -- the
    partial unique index is keyed purely on (backup_job_id, status), not
    verification_type."""
    _, _, job = await _server_disk_job(session)

    copy_run = build_verification_run(
        job.id,
        verification_type=VerificationType.FTP_COPY_INTEGRITY,
        status=VerificationRunStatus.OK,
        triggered_by="agent",
    )
    session.add(copy_run)
    await session.commit()

    restore_run = VerificationRun(backup_job_id=job.id, triggered_by="scheduler")
    session.add(restore_run)
    await session.commit()
    assert restore_run.id is not None
