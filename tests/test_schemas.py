"""Pydantic v2 schema tests: secret redaction + validation rules.

These are deliberately independent from the DB-cascade/constraint tests
(test_cascades.py / test_constraints.py) since a coder could break the
`hybrid_property` -> Read-schema wiring, or a validator, without touching
any FK/CHECK/index behavior at all.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.security import decrypt_secret, encrypt_secret
from app.models.enums import (
    JobRunStatus,
    ProtocolType,
    RestoreMode,
    RestoreStatus,
)
from app.schemas.backup_job import BackupJobCreate
from app.schemas.backup_record import BackupRecordBase
from app.schemas.disk import DiskBase, DiskCreate
from app.schemas.job_run import JobRunUpdate
from app.schemas.job_run import is_valid_transition as job_run_is_valid_transition
from app.schemas.restore_operation import RestoreOperationCreate, RestoreOperationUpdate
from app.schemas.restore_operation import is_valid_transition as restore_is_valid_transition
from app.schemas.server import ServerCreate, ServerRead
from app.schemas.sql_instance import SqlInstanceRead
from tests.conftest import build_server, build_sql_instance


# --------------------------------------------------------------------------
# Secret redaction on Read schemas -- ServerRead / SqlInstanceRead
# --------------------------------------------------------------------------


async def test_server_read_hides_secrets_and_reports_flags_true(session):
    server = build_server(
        protocol=ProtocolType.SFTP,
        username_encrypted=encrypt_secret("admin"),
        password_encrypted=encrypt_secret("hunter2-plaintext"),
    )
    session.add(server)
    await session.commit()
    await session.refresh(server)

    # Sanity check the "encryption actually happened" premise directly on
    # the ORM object before we even touch the schema layer.
    assert server.password_encrypted is not None
    assert "hunter2-plaintext" not in server.password_encrypted
    assert decrypt_secret(server.password_encrypted) == "hunter2-plaintext"

    read = ServerRead.model_validate(server, from_attributes=True)
    dumped = read.model_dump()
    dumped_json = read.model_dump_json()

    for forbidden in ("password", "username_encrypted", "password_encrypted", "ssh_private_key_encrypted"):
        assert forbidden not in dumped

    assert "hunter2-plaintext" not in dumped_json
    assert "admin" not in dumped_json  # the encrypted username token also must not surface as plaintext username value

    assert dumped["credentials_set"] is True
    assert dumped["ssh_key_set"] is False


async def test_server_read_reports_flags_false_when_no_secrets_stored(session):
    server = build_server(protocol=ProtocolType.FTP)
    session.add(server)
    await session.commit()
    await session.refresh(server)

    read = ServerRead.model_validate(server, from_attributes=True)
    assert read.credentials_set is False
    assert read.ssh_key_set is False


async def test_server_read_ssh_key_set_true_when_only_ssh_key_present(session):
    server = build_server(
        protocol=ProtocolType.SFTP,
        ssh_private_key_encrypted=encrypt_secret("-----BEGIN KEY-----..."),
    )
    session.add(server)
    await session.commit()
    await session.refresh(server)

    read = ServerRead.model_validate(server, from_attributes=True)
    assert read.credentials_set is False
    assert read.ssh_key_set is True
    assert "BEGIN KEY" not in read.model_dump_json()


async def test_sql_instance_read_hides_secrets_and_reports_credentials_set(session):
    instance = build_sql_instance(
        use_windows_auth=False,
        username_encrypted=encrypt_secret("sa"),
        password_encrypted=encrypt_secret("supersecret-plaintext"),
    )
    session.add(instance)
    await session.commit()
    await session.refresh(instance)

    assert "supersecret-plaintext" not in instance.password_encrypted
    assert decrypt_secret(instance.password_encrypted) == "supersecret-plaintext"

    read = SqlInstanceRead.model_validate(instance, from_attributes=True)
    dumped = read.model_dump()

    for forbidden in ("password", "username_encrypted", "password_encrypted"):
        assert forbidden not in dumped
    assert "supersecret-plaintext" not in read.model_dump_json()
    assert dumped["credentials_set"] is True


async def test_sql_instance_read_credentials_set_false_when_windows_auth(session):
    instance = build_sql_instance(use_windows_auth=True)
    session.add(instance)
    await session.commit()
    await session.refresh(instance)

    read = SqlInstanceRead.model_validate(instance, from_attributes=True)
    assert read.credentials_set is False


# --------------------------------------------------------------------------
# ServerCreate validation
# --------------------------------------------------------------------------


def test_server_create_sftp_without_password_or_key_is_rejected():
    with pytest.raises(ValidationError):
        ServerCreate(name="s1", host="h1", port=22, protocol=ProtocolType.SFTP)


def test_server_create_sftp_with_password_is_accepted():
    server = ServerCreate(name="s1", host="h1", port=22, protocol=ProtocolType.SFTP, password="pw")
    assert server.password == "pw"


def test_server_create_ftp_without_credentials_is_accepted():
    server = ServerCreate(name="s1", host="h1", port=21, protocol=ProtocolType.FTP)
    assert server.password is None


# --------------------------------------------------------------------------
# DiskBase / DiskCreate threshold validation (Pydantic-level, independent
# of the DB CHECK constraint tested in test_constraints.py)
# --------------------------------------------------------------------------


def test_disk_base_rejects_critical_not_above_warning():
    with pytest.raises(ValidationError):
        DiskBase(label="d1", mount_path="/mnt/a", warning_threshold_pct=90, critical_threshold_pct=80)


def test_disk_base_rejects_critical_equal_to_warning():
    with pytest.raises(ValidationError):
        DiskBase(label="d1", mount_path="/mnt/a", warning_threshold_pct=85, critical_threshold_pct=85)


def test_disk_create_accepts_valid_thresholds():
    disk = DiskCreate(
        server_id=1, label="d1", mount_path="/mnt/a", warning_threshold_pct=80, critical_threshold_pct=90
    )
    assert disk.critical_threshold_pct > disk.warning_threshold_pct


# --------------------------------------------------------------------------
# BackupJobCreate: verification_method required when sql_instance_id is set
# --------------------------------------------------------------------------


def test_backup_job_create_requires_verification_method_with_sql_instance():
    with pytest.raises(ValidationError):
        BackupJobCreate(
            name="job1",
            source_path="/src",
            schedule_cron="* * * * *",
            server_id=1,
            disk_id=1,
            sql_instance_id=5,
            verification_method=None,
        )


def test_backup_job_create_allows_no_verification_method_without_sql_instance():
    job = BackupJobCreate(
        name="job1",
        source_path="/src",
        schedule_cron="* * * * *",
        server_id=1,
        disk_id=1,
        sql_instance_id=None,
    )
    assert job.verification_method is None


def test_backup_job_create_allows_sql_instance_with_verification_method():
    job = BackupJobCreate(
        name="job1",
        source_path="/src",
        schedule_cron="* * * * *",
        server_id=1,
        disk_id=1,
        sql_instance_id=5,
        verification_method="RESTORE_VERIFY",
    )
    assert job.sql_instance_id == 5


# --------------------------------------------------------------------------
# RestoreOperationCreate: confirmation_database_name must match database_name
# --------------------------------------------------------------------------


def test_restore_operation_create_rejects_confirmation_mismatch():
    with pytest.raises(ValidationError):
        RestoreOperationCreate(
            database_name="orders",
            mode=RestoreMode.ALL,
            backup_record_id=1,
            sql_instance_id=1,
            confirmation_database_name="orders_typo",
        )


def test_restore_operation_create_accepts_matching_confirmation():
    op = RestoreOperationCreate(
        database_name="orders",
        mode=RestoreMode.ALL,
        backup_record_id=1,
        sql_instance_id=1,
        confirmation_database_name="orders",
    )
    assert op.confirmation_database_name == op.database_name


# --------------------------------------------------------------------------
# RestoreOperationUpdate / is_valid_transition
# --------------------------------------------------------------------------


def test_restore_running_to_cancelled_is_forbidden():
    assert restore_is_valid_transition(RestoreStatus.RUNNING, RestoreStatus.CANCELLED) is False


def test_restore_pending_to_cancelled_is_allowed():
    assert restore_is_valid_transition(RestoreStatus.PENDING, RestoreStatus.CANCELLED) is True


@pytest.mark.parametrize("terminal", [RestoreStatus.DONE, RestoreStatus.FAILED, RestoreStatus.CANCELLED])
@pytest.mark.parametrize("target", [RestoreStatus.PENDING, RestoreStatus.RUNNING])
def test_restore_terminal_to_nonterminal_is_forbidden(terminal, target):
    assert restore_is_valid_transition(terminal, target) is False


def test_restore_operation_update_structural_validation_still_works():
    update = RestoreOperationUpdate(status=RestoreStatus.RUNNING)
    assert update.status == RestoreStatus.RUNNING
    with pytest.raises(ValidationError):
        RestoreOperationUpdate(status=RestoreStatus.RUNNING, not_a_real_field="x")


# --------------------------------------------------------------------------
# JobRunUpdate / is_valid_transition
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "terminal",
    [JobRunStatus.SUCCESS, JobRunStatus.FAILED, JobRunStatus.WARNING, JobRunStatus.CANCELLED],
)
@pytest.mark.parametrize("target", [JobRunStatus.PENDING, JobRunStatus.RUNNING])
def test_job_run_terminal_to_active_is_forbidden(terminal, target):
    assert job_run_is_valid_transition(terminal, target) is False


def test_job_run_pending_to_running_is_allowed():
    assert job_run_is_valid_transition(JobRunStatus.PENDING, JobRunStatus.RUNNING) is True


def test_job_run_update_structural_validation_still_works():
    update = JobRunUpdate(status=JobRunStatus.RUNNING)
    assert update.status == JobRunStatus.RUNNING
    with pytest.raises(ValidationError):
        JobRunUpdate(status=JobRunStatus.RUNNING, not_a_real_field="x")


# --------------------------------------------------------------------------
# BackupRecordBase: checksum / checksum_algorithm must travel together
# --------------------------------------------------------------------------


def test_backup_record_checksum_without_algorithm_is_rejected():
    with pytest.raises(ValidationError):
        BackupRecordBase(
            file_name="f.bak", remote_path="/r/f.bak", file_size_bytes=10, checksum="abc123", checksum_algorithm=None
        )


def test_backup_record_algorithm_without_checksum_is_rejected():
    with pytest.raises(ValidationError):
        BackupRecordBase(
            file_name="f.bak", remote_path="/r/f.bak", file_size_bytes=10, checksum=None, checksum_algorithm="sha256"
        )


def test_backup_record_neither_checksum_nor_algorithm_is_accepted():
    record = BackupRecordBase(file_name="f.bak", remote_path="/r/f.bak", file_size_bytes=10)
    assert record.checksum is None
    assert record.checksum_algorithm is None


def test_backup_record_both_checksum_and_algorithm_is_accepted():
    record = BackupRecordBase(
        file_name="f.bak", remote_path="/r/f.bak", file_size_bytes=10, checksum="abc123", checksum_algorithm="sha256"
    )
    assert record.checksum == "abc123"
    assert record.checksum_algorithm == "sha256"
