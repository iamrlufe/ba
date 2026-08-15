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
    BackupType,
    JobRunStatus,
    ProtocolType,
    RestoreMode,
    RestoreStatus,
    TriggerMode,
)
from app.schemas.backup_job import BackupJobCreate, BackupJobRead, BackupJobUpdate
from app.schemas.backup_record import BackupRecordBase
from app.schemas.copy_verification import AgentCopyVerificationStatus, CopyVerificationReportRequest
from app.schemas.disk import DiskBase, DiskCreate
from app.schemas.job_run import JobRunUpdate
from app.schemas.job_run import is_valid_transition as job_run_is_valid_transition
from app.schemas.restore_operation import RestoreOperationCreate, RestoreOperationUpdate
from app.schemas.restore_operation import is_valid_transition as restore_is_valid_transition
from app.schemas.server import ServerCreate, ServerRead
from app.schemas.sql_instance import SqlInstanceCreate, SqlInstanceRead, SqlInstanceUpdate
from tests.conftest import build_backup_job, build_disk, build_server, build_sql_instance


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
# SqlInstanceCreate / SqlInstanceUpdate: port and instance_name are
# mutually exclusive (both set together must be rejected).
# --------------------------------------------------------------------------


def test_sql_instance_create_rejects_port_and_instance_name_together():
    with pytest.raises(ValidationError):
        SqlInstanceCreate(
            name="sql-1",
            host="10.0.0.1",
            port=1433,
            instance_name="SQLEXPRESS",
            use_windows_auth=True,
        )


def test_sql_instance_create_allows_port_alone():
    instance = SqlInstanceCreate(
        name="sql-1", host="10.0.0.1", port=1433, instance_name=None, use_windows_auth=True
    )
    assert instance.port == 1433
    assert instance.instance_name is None


def test_sql_instance_create_allows_instance_name_alone():
    instance = SqlInstanceCreate(
        name="sql-1", host="10.0.0.1", port=None, instance_name="SQLEXPRESS", use_windows_auth=True
    )
    assert instance.instance_name == "SQLEXPRESS"
    assert instance.port is None


def test_sql_instance_update_rejects_port_and_instance_name_together():
    with pytest.raises(ValidationError):
        SqlInstanceUpdate(port=1433, instance_name="SQLEXPRESS")


def test_sql_instance_update_allows_port_alone():
    update = SqlInstanceUpdate(port=1433)
    assert update.port == 1433
    assert update.instance_name is None


def test_sql_instance_update_allows_instance_name_alone():
    update = SqlInstanceUpdate(instance_name="SQLEXPRESS")
    assert update.instance_name == "SQLEXPRESS"
    assert update.port is None


def test_sql_instance_update_allows_neither_set():
    update = SqlInstanceUpdate()
    assert update.port is None
    assert update.instance_name is None


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
        database_name="orders",
    )
    assert job.sql_instance_id == 5


def test_backup_job_create_requires_database_name_with_sql_instance():
    with pytest.raises(ValidationError):
        BackupJobCreate(
            name="job1",
            source_path="/src",
            schedule_cron="* * * * *",
            server_id=1,
            disk_id=1,
            sql_instance_id=5,
            verification_method="RESTORE_VERIFY",
            database_name=None,
        )


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


# --------------------------------------------------------------------------
# BackupJobCreate / BackupJobUpdate: schedule_cron syntax validation
# (app.schemas.backup_job._check_cron_syntax, via croniter.is_valid) --
# regression coverage for the invalid-cron-crashes-the-.NET-agent fix. See
# AlertType.JOB_CRON_INVALID / POST /{id}/schedule-errors /
# check_missed_runs for the rest of the fix's surface.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad_cron", ["invalid cron", "not a valid cron", "* * * *", "99 * * * *"])
def test_backup_job_create_rejects_invalid_cron_syntax(bad_cron):
    with pytest.raises(ValidationError):
        BackupJobCreate(
            name="job1",
            source_path="/src",
            schedule_cron=bad_cron,
            server_id=1,
            disk_id=1,
        )


def test_backup_job_create_accepts_valid_cron_syntax():
    job = BackupJobCreate(
        name="job1",
        source_path="/src",
        schedule_cron="0 * * * *",
        server_id=1,
        disk_id=1,
    )
    assert job.schedule_cron == "0 * * * *"


def test_backup_job_update_rejects_invalid_cron_syntax():
    with pytest.raises(ValidationError):
        BackupJobUpdate(schedule_cron="invalid cron")


def test_backup_job_update_accepts_valid_cron_syntax():
    update = BackupJobUpdate(schedule_cron="0 * * * *")
    assert update.schedule_cron == "0 * * * *"


def test_backup_job_update_without_schedule_cron_key_skips_cron_check():
    """exclude_unset convention: schedule_cron absent from the patch entirely
    must not trigger cron validation at all (it's simply not part of this
    patch) -- only setting schedule_cron to a syntactically invalid string
    should fail."""
    update = BackupJobUpdate(name="renamed")
    assert update.schedule_cron is None


async def test_backup_job_read_does_not_validate_schedule_cron(session):
    """Key regression-protecting invariant: BackupJobBase/BackupJobRead
    (unlike BackupJobCreate/BackupJobUpdate) must NOT re-validate
    schedule_cron. A legacy row with an invalid cron already sitting in the
    DB (inserted directly via the ORM, bypassing the Pydantic schema layer
    entirely -- the only way such a row could exist post-fix) must still be
    readable: BackupJobRead.model_validate() must not raise, or every
    GET/LIST call touching that row would 500 and the agent's polling loop
    would be starved for that job."""
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()

    # Bypasses BackupJobCreate/BackupJobUpdate entirely -- the ORM layer
    # itself has no cron-syntax constraint (see app/models/backup_job.py),
    # simulating a legacy row from before this fix (or one that became
    # invalid by some other route).
    job = build_backup_job(server.id, disk.id, schedule_cron="not a valid cron")
    session.add(job)
    await session.commit()
    await session.refresh(job)

    read = BackupJobRead.model_validate(job)
    assert read.schedule_cron == "not a valid cron"


# --------------------------------------------------------------------------
# BackupJobCreate / BackupJobUpdate: remote_directory_override validation
# (app.schemas.backup_job._check_remote_directory_override) -- the escape
# hatch for BackupJob.remote_directory (app/models/backup_job.py). See also
# tests/test_remote_paths.py for the computed-default resolution logic and
# its own path-traversal security regression coverage.
# --------------------------------------------------------------------------


def _create_payload(**overrides) -> dict:
    payload = dict(
        name="job1",
        source_path="/src",
        schedule_cron="0 * * * *",
        server_id=1,
        disk_id=1,
    )
    payload.update(overrides)
    return payload


def test_backup_job_create_accepts_valid_remote_directory_override():
    job = BackupJobCreate(**_create_payload(remote_directory_override="custom/path"))
    assert job.remote_directory_override == "custom/path"


def test_backup_job_create_remote_directory_override_defaults_to_none():
    job = BackupJobCreate(**_create_payload())
    assert job.remote_directory_override is None


def test_backup_job_create_rejects_empty_string_override():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_create_payload(remote_directory_override=""))


@pytest.mark.parametrize(
    "bad_value", [" custom/path", "custom/path ", " custom/path ", "\tcustom/path"]
)
def test_backup_job_create_rejects_leading_or_trailing_whitespace_override(bad_value):
    with pytest.raises(ValidationError):
        BackupJobCreate(**_create_payload(remote_directory_override=bad_value))


@pytest.mark.parametrize(
    "bad_value",
    [
        "..",
        "a/../b",
        "a\\..\\b",
        "../escape",
        "escape/..",
        "a/..\\b",
    ],
)
def test_backup_job_create_rejects_dotdot_traversal_override(bad_value):
    with pytest.raises(ValidationError):
        BackupJobCreate(**_create_payload(remote_directory_override=bad_value))


def test_backup_job_update_accepts_valid_remote_directory_override():
    update = BackupJobUpdate(remote_directory_override="custom/path")
    assert update.remote_directory_override == "custom/path"


def test_backup_job_update_remote_directory_override_none_is_valid_and_clears():
    update = BackupJobUpdate(remote_directory_override=None)
    assert update.remote_directory_override is None


def test_backup_job_update_omitted_remote_directory_override_is_valid():
    update = BackupJobUpdate(name="renamed")
    assert "remote_directory_override" not in update.model_dump(exclude_unset=True)


def test_backup_job_update_rejects_empty_string_override():
    with pytest.raises(ValidationError):
        BackupJobUpdate(remote_directory_override="")


@pytest.mark.parametrize("bad_value", [" custom/path", "custom/path "])
def test_backup_job_update_rejects_whitespace_override(bad_value):
    with pytest.raises(ValidationError):
        BackupJobUpdate(remote_directory_override=bad_value)


@pytest.mark.parametrize("bad_value", ["..", "a/../b", "a\\..\\b"])
def test_backup_job_update_rejects_dotdot_traversal_override(bad_value):
    with pytest.raises(ValidationError):
        BackupJobUpdate(remote_directory_override=bad_value)


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


# --------------------------------------------------------------------------
# CopyVerificationReportRequest: actual_checksum required/forbidden per status
# --------------------------------------------------------------------------


def test_copy_verification_ok_requires_actual_checksum():
    with pytest.raises(ValidationError):
        CopyVerificationReportRequest(
            status=AgentCopyVerificationStatus.OK, checked_at="2026-08-10T00:00:00Z"
        )


def test_copy_verification_mismatch_requires_actual_checksum():
    with pytest.raises(ValidationError):
        CopyVerificationReportRequest(
            status=AgentCopyVerificationStatus.MISMATCH, checked_at="2026-08-10T00:00:00Z"
        )


def test_copy_verification_missing_sidecar_forbids_actual_checksum():
    with pytest.raises(ValidationError):
        CopyVerificationReportRequest(
            status=AgentCopyVerificationStatus.MISSING_SIDECAR,
            actual_checksum="abc",
            checked_at="2026-08-10T00:00:00Z",
        )


def test_copy_verification_file_unreadable_forbids_actual_checksum():
    with pytest.raises(ValidationError):
        CopyVerificationReportRequest(
            status=AgentCopyVerificationStatus.FILE_UNREADABLE,
            actual_checksum="abc",
            checked_at="2026-08-10T00:00:00Z",
        )


def test_copy_verification_ok_with_checksum_is_accepted():
    req = CopyVerificationReportRequest(
        status=AgentCopyVerificationStatus.OK, actual_checksum="abc", checked_at="2026-08-10T00:00:00Z"
    )
    assert req.actual_checksum == "abc"


def test_copy_verification_missing_sidecar_without_checksum_is_accepted():
    req = CopyVerificationReportRequest(
        status=AgentCopyVerificationStatus.MISSING_SIDECAR, checked_at="2026-08-10T00:00:00Z"
    )
    assert req.actual_checksum is None


def test_copy_verification_extra_field_is_rejected():
    with pytest.raises(ValidationError):
        CopyVerificationReportRequest(
            status=AgentCopyVerificationStatus.OK,
            actual_checksum="abc",
            checked_at="2026-08-10T00:00:00Z",
            extra_field="nope",
        )


# --------------------------------------------------------------------------
# BackupJobCreate: trigger_mode (SCHEDULE/WATCH) conditional-required
# fields + copy-window validation (app/schemas/backup_job.py)
# --------------------------------------------------------------------------


def _schedule_payload(**overrides) -> dict:
    payload = dict(
        name="job1",
        server_id=1,
        disk_id=1,
        source_path="/src",
        schedule_cron="* * * * *",
    )
    payload.update(overrides)
    return payload


def _watch_payload(**overrides) -> dict:
    payload = dict(
        name="job1",
        server_id=1,
        disk_id=1,
        trigger_mode=TriggerMode.WATCH,
        watch_directory="/watch",
    )
    payload.update(overrides)
    return payload


def test_backup_job_create_schedule_without_schedule_cron_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_schedule_payload(schedule_cron=None))


def test_backup_job_create_schedule_without_source_path_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_schedule_payload(source_path=None))


def test_backup_job_create_schedule_with_watch_directory_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_schedule_payload(watch_directory="/watch"))


def test_backup_job_create_watch_without_watch_directory_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_watch_payload(watch_directory=None))


def test_backup_job_create_watch_with_schedule_cron_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_watch_payload(schedule_cron="* * * * *"))


def test_backup_job_create_watch_with_source_path_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_watch_payload(source_path="/src"))


@pytest.mark.parametrize("backup_type", [BackupType.TRANSACTION_LOG, BackupType.CUSTOM])
def test_backup_job_create_watch_rejects_unsupported_backup_types(backup_type):
    with pytest.raises(ValidationError):
        BackupJobCreate(**_watch_payload(backup_type=backup_type))


@pytest.mark.parametrize("backup_type", [BackupType.FULL, BackupType.DIFFERENTIAL])
def test_backup_job_create_watch_accepts_supported_backup_types(backup_type):
    job = BackupJobCreate(**_watch_payload(backup_type=backup_type))
    assert job.trigger_mode == TriggerMode.WATCH
    assert job.backup_type == backup_type


def test_backup_job_create_valid_schedule_job_still_accepted():
    """Regression check: today's existing SCHEDULE-job shape must still
    validate cleanly now that trigger_mode-conditional validators exist."""
    job = BackupJobCreate(**_schedule_payload())
    assert job.trigger_mode == TriggerMode.SCHEDULE
    assert job.schedule_cron == "* * * * *"
    assert job.source_path == "/src"
    assert job.watch_directory is None


def test_backup_job_create_copy_window_start_without_end_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_schedule_payload(copy_window_start_hour=18))


def test_backup_job_create_copy_window_end_without_start_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_schedule_payload(copy_window_end_hour=9))


def test_backup_job_create_copy_window_start_equals_end_is_rejected():
    with pytest.raises(ValidationError):
        BackupJobCreate(**_schedule_payload(copy_window_start_hour=9, copy_window_end_hour=9))


def test_backup_job_create_copy_window_midnight_wraparound_is_accepted():
    """start_hour=18, end_hour=9 spans midnight -- must NOT be rejected;
    the validator only checks both-or-neither + distinctness, not
    start < end."""
    job = BackupJobCreate(**_schedule_payload(copy_window_start_hour=18, copy_window_end_hour=9))
    assert job.copy_window_start_hour == 18
    assert job.copy_window_end_hour == 9
