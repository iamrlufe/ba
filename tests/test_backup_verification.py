"""Tests for `app.workers.backup_verification` -- the SQL Server backup
verification worker.

Mirrors `tests/test_alert_worker.py`'s established pattern: call the worker
functions directly against the `session_maker` fixture, with a `now=`
parameter-injection for deterministic time. The injectable seam under test
is `sql_client_factory: SqlClientFactory` -- a fake `SqlClient` stub is
built per test case and injected directly; no real `pytds` connection is
ever attempted here (that is covered by tests/test_core_sql_client.py).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.security import encrypt_secret
from app.core.sql_client import MsdbBackupInfo, SqlConnectionParams, VerifyOnlyResult
from app.models.alert import Alert
from app.models.enums import AlertSeverity, AlertStatus, AlertType, VerificationRunStatus
from app.models.verification_run import VerificationRun
from app.workers.backup_verification import (
    check_backup_verifications,
    check_stuck_verifications,
    create_pending_verification_run,
    execute_verification_run,
)
from tests.conftest import (
    build_backup_job,
    build_backup_record,
    build_disk,
    build_server,
    build_sql_instance,
    build_verification_run,
)

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

# Recognizable marker used as the fake SqlInstance password -- assertions
# check this string never leaks into VerificationRun.error_message.
FAKE_PASSWORD_MARKER = "S3cr3t-Marker-Not-For-Logs"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSqlClient:
    """Hand-built stub satisfying `app.core.sql_client.SqlClient`."""

    def __init__(
        self,
        *,
        backupset: MsdbBackupInfo | None = None,
        get_latest_exc: Exception | None = None,
        verify_result: VerifyOnlyResult | None = None,
        verify_exc: Exception | None = None,
    ):
        self.backupset = backupset
        self.get_latest_exc = get_latest_exc
        self.verify_result = verify_result
        self.verify_exc = verify_exc
        self.closed = False
        self.get_latest_called_with: str | None = None
        self.verify_called_with: tuple | None = None

    def get_latest_backupset(self, database_name: str) -> MsdbBackupInfo | None:
        self.get_latest_called_with = database_name
        if self.get_latest_exc is not None:
            raise self.get_latest_exc
        return self.backupset

    def restore_verifyonly(self, disk_path: str, *, timeout_seconds: int) -> VerifyOnlyResult:
        self.verify_called_with = (disk_path, timeout_seconds)
        if self.verify_exc is not None:
            raise self.verify_exc
        return self.verify_result

    def close(self) -> None:
        self.closed = True


def _factory_for(client: FakeSqlClient, calls: list | None = None):
    def factory(params: SqlConnectionParams) -> FakeSqlClient:
        if calls is not None:
            calls.append(params)
        return client

    return factory


def _factory_raising(exc: Exception, calls: list | None = None):
    def factory(params: SqlConnectionParams):
        if calls is not None:
            calls.append(params)
        raise exc

    return factory


_OK_VERIFY = VerifyOnlyResult(succeeded=True, output="verify ok", error_message=None, error_number=None)


def _damaged_info(physical_device_name: str | None = None) -> MsdbBackupInfo:
    return MsdbBackupInfo(
        backup_finish_date=NOW, is_damaged=True, physical_device_name=physical_device_name
    )


def _clean_info(physical_device_name: str | None = None) -> MsdbBackupInfo:
    return MsdbBackupInfo(
        backup_finish_date=NOW, is_damaged=False, physical_device_name=physical_device_name
    )


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_username_counter = iter(range(1, 100_000))


async def _job_with_instance(
    session,
    *,
    with_record: bool = True,
    password: str = FAKE_PASSWORD_MARKER,
    username: str | None = None,
    **job_overrides,
):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()

    if username is None:
        username = f"svc_backup_verify_{next(_username_counter)}"

    instance = build_sql_instance(
        use_windows_auth=False,
        username_encrypted=encrypt_secret(username),
        password_encrypted=encrypt_secret(password),
    )
    session.add(instance)
    await session.commit()

    job_overrides.setdefault("is_enabled", True)
    job = build_backup_job(
        server.id,
        disk.id,
        sql_instance_id=instance.id,
        verification_method="RESTORE_VERIFY",
        database_name="orders",
        **job_overrides,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    record = None
    if with_record:
        record = build_backup_record(job.id, file_name="nightly.bak", remote_path=r"\\fileserver\backups")
        session.add(record)
        await session.commit()
        await session.refresh(record)

    return job, instance, record


async def _pending_run_id(session_maker, job_id: int) -> int:
    async with session_maker() as s:
        run = await create_pending_verification_run(s, job_id, triggered_by="tester")
        await s.commit()
        return run.id


async def _active_alert_count(
    session_maker, alert_type: AlertType = AlertType.BACKUP_VERIFICATION_FAILED, status: AlertStatus = AlertStatus.ACTIVE
) -> int:
    async with session_maker() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(Alert)
                .where(Alert.alert_type == alert_type, Alert.status == status)
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# execute_verification_run -- classification rules
# ---------------------------------------------------------------------------


async def test_no_backup_record_classifies_missing_no_sql_connection_attempted(session, session_maker):
    job, _instance, _record = await _job_with_instance(session, with_record=False)
    run_id = await _pending_run_id(session_maker, job.id)

    calls: list = []
    factory = _factory_raising(AssertionError("factory should never be called"), calls)

    run = await execute_verification_run(session_maker, run_id, sql_client_factory=factory, now=NOW)

    assert run is not None
    assert run.status == VerificationRunStatus.MISSING
    assert calls == []


async def test_no_msdb_row_classifies_missing(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(backupset=None)
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.MISSING
    assert client.closed is True


async def test_is_damaged_true_classifies_corrupt(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)
    run_id = await _pending_run_id(session_maker, job.id)

    # `_run_blocking` calls get_latest_backupset AND restore_verifyonly
    # unconditionally on the same connection before classifying -- so the
    # fake must supply a (valid) verify_result even in the is_damaged=True
    # case, since restore_verifyonly is still invoked; only the
    # *classification* short-circuits to CORRUPT once is_damaged is known.
    client = FakeSqlClient(backupset=_damaged_info(), verify_result=_OK_VERIFY)
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.CORRUPT
    assert run.msdb_is_damaged is True


@pytest.mark.parametrize("error_number", [3201, 5133])
async def test_verify_infrastructure_error_numbers_classify_error(session, session_maker, error_number):
    job, _instance, _record = await _job_with_instance(session)
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(),
        verify_result=VerifyOnlyResult(
            succeeded=False, output=None, error_message="cannot open backup device", error_number=error_number
        ),
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.ERROR


async def test_verify_other_error_number_classifies_corrupt(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(),
        verify_result=VerifyOnlyResult(
            succeeded=False, output=None, error_message="checksum mismatch", error_number=3013
        ),
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.CORRUPT


async def test_connect_failure_classifies_error_and_never_leaks_password(session, session_maker):
    job, _instance, _record = await _job_with_instance(session, password=FAKE_PASSWORD_MARKER)
    run_id = await _pending_run_id(session_maker, job.id)

    factory = _factory_raising(TimeoutError("connection timed out"))
    run = await execute_verification_run(session_maker, run_id, sql_client_factory=factory, now=NOW)

    assert run.status == VerificationRunStatus.ERROR
    assert run.error_message is not None
    assert FAKE_PASSWORD_MARKER not in run.error_message
    assert "svc_backup_verify" not in run.error_message


async def test_everything_succeeds_classifies_ok(session, session_maker):
    job, _instance, record = await _job_with_instance(session)
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(backupset=_clean_info(), verify_result=_OK_VERIFY)
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.OK
    assert run.error_message is None
    assert run.backup_record_id == record.id
    assert run.sql_instance_id == job.sql_instance_id
    assert run.finished_at is not None
    # disk_path built from BackupRecord.remote_path + file_name.
    disk_path, timeout_seconds = client.verify_called_with
    assert disk_path == r"\\fileserver\backups\nightly.bak"
    assert client.get_latest_called_with == "orders"


async def test_execute_verification_run_never_raises_on_exotic_factory_exception(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)
    run_id = await _pending_run_id(session_maker, job.id)

    factory = _factory_raising(RuntimeError("something exotic and unexpected"))

    run = await execute_verification_run(session_maker, run_id, sql_client_factory=factory, now=NOW)

    assert run is not None
    assert run.status == VerificationRunStatus.ERROR


async def test_execute_verification_run_missing_run_returns_none(session_maker):
    run = await execute_verification_run(session_maker, 999999, now=NOW)
    assert run is None


# ---------------------------------------------------------------------------
# local_backup_path_pattern soft-compare
# ---------------------------------------------------------------------------


async def test_local_backup_path_pattern_match_appends_no_note(session, session_maker):
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern=r"\\fileserver\backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(physical_device_name=r"\\fileserver\backups\nightly.bak"),
        verify_result=_OK_VERIFY,
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.OK
    assert run.verifyonly_output == _OK_VERIFY.output


async def test_local_backup_path_pattern_mismatch_ok_run_appends_note_status_unaffected(
    session, session_maker
):
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern=r"\\otherserver\backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(physical_device_name=r"\\fileserver\backups\nightly.bak"),
        verify_result=_OK_VERIFY,
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.OK
    assert run.error_message is None
    assert "local_backup_path_pattern mismatch" in run.verifyonly_output
    assert _OK_VERIFY.output in run.verifyonly_output  # appended, not replaced


async def test_local_backup_path_pattern_mismatch_corrupt_run_appends_note(session, session_maker):
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern=r"\\otherserver\backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_damaged_info(physical_device_name=r"\\fileserver\backups\nightly.bak"),
        verify_result=_OK_VERIFY,
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.CORRUPT
    assert run.msdb_is_damaged is True
    assert run.error_message == "msdb.dbo.backupset.is_damaged=1 for the latest backup"
    assert "local_backup_path_pattern mismatch" in run.verifyonly_output

    async with session_maker() as s:
        alert = (
            await s.execute(
                select(Alert).where(
                    Alert.alert_type == AlertType.BACKUP_VERIFICATION_FAILED,
                    Alert.backup_job_id == job.id,
                )
            )
        ).scalar_one()
    assert alert.severity == AlertSeverity.CRITICAL


async def test_local_backup_path_pattern_unset_never_appends_note(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)
    assert job.local_backup_path_pattern is None
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(physical_device_name=r"\\totally\unrelated\path.bak"),
        verify_result=_OK_VERIFY,
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.verifyonly_output == _OK_VERIFY.output


async def test_local_backup_path_pattern_set_but_physical_device_name_none_no_note(
    session, session_maker
):
    """physical_device_name is None when the LEFT JOIN to backupmediafamily
    misses -- the soft-compare must be skipped entirely, not treated as a
    mismatch."""
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern=r"\\fileserver\backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(backupset=_clean_info(physical_device_name=None), verify_result=_OK_VERIFY)
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.verifyonly_output == _OK_VERIFY.output


async def test_local_backup_path_pattern_set_but_run_missing_no_note(session, session_maker):
    job, _instance, _record = await _job_with_instance(
        session, with_record=False, local_backup_path_pattern=r"\\fileserver\backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    calls: list = []
    factory = _factory_raising(AssertionError("factory should never be called"), calls)

    run = await execute_verification_run(session_maker, run_id, sql_client_factory=factory, now=NOW)

    assert run.status == VerificationRunStatus.MISSING
    assert run.verifyonly_output is None


async def test_local_backup_path_pattern_set_but_connect_fails_no_note(session, session_maker):
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern=r"\\fileserver\backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    factory = _factory_raising(TimeoutError("connection timed out"))
    run = await execute_verification_run(session_maker, run_id, sql_client_factory=factory, now=NOW)

    assert run.status == VerificationRunStatus.ERROR
    assert run.verifyonly_output is None


async def test_local_backup_path_pattern_case_insensitive_match_no_note(session, session_maker):
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern=r"\\FileServer\Backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(physical_device_name=r"\\fileserver\backups\nightly.bak"),
        verify_result=_OK_VERIFY,
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.verifyonly_output == _OK_VERIFY.output


async def test_local_backup_path_pattern_trailing_slash_normalized_match_no_note(
    session, session_maker
):
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern="\\\\fileserver\\backups\\"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(physical_device_name=r"\\fileserver\backups\nightly.bak"),
        verify_result=_OK_VERIFY,
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.verifyonly_output == _OK_VERIFY.output


async def test_local_backup_path_pattern_mid_path_substring_match_no_note(session, session_maker):
    """Containment semantics, not startswith -- a pattern matching mid-path
    (not anchored to the start) still counts as a match."""
    job, _instance, _record = await _job_with_instance(session, local_backup_path_pattern="backups")
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(physical_device_name=r"\\fileserver\backups\nightly.bak"),
        verify_result=_OK_VERIFY,
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.verifyonly_output == _OK_VERIFY.output


async def test_local_backup_path_pattern_mismatch_on_verify_failure_appends_note(
    session, session_maker
):
    """Third eligible branch (`not verify_result.succeeded`, CORRUPT/ERROR via
    error-number classification) -- the note must still be appended, and
    status/error_message must remain driven only by the verify failure, not
    by the pattern mismatch."""
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern=r"\\otherserver\backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(
        backupset=_clean_info(physical_device_name=r"\\fileserver\backups\nightly.bak"),
        verify_result=VerifyOnlyResult(
            succeeded=False, output=None, error_message="checksum mismatch", error_number=3013
        ),
    )
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.CORRUPT
    assert run.error_message == "checksum mismatch"
    assert "local_backup_path_pattern mismatch" in run.verifyonly_output


async def test_local_backup_path_pattern_set_but_no_msdb_row_no_note(session, session_maker):
    """MISSING via `msdb_info is None` (BackupRecord exists, but no
    msdb.dbo.backupset row) -- distinct from the `with_record=False` MISSING
    path already covered above. The soft-compare guard must exclude this
    branch too, since there is no physical_device_name to compare against."""
    job, _instance, _record = await _job_with_instance(
        session, local_backup_path_pattern=r"\\fileserver\backups"
    )
    run_id = await _pending_run_id(session_maker, job.id)

    client = FakeSqlClient(backupset=None)
    run = await execute_verification_run(
        session_maker, run_id, sql_client_factory=_factory_for(client), now=NOW
    )

    assert run.status == VerificationRunStatus.MISSING
    assert run.verifyonly_output is None


# ---------------------------------------------------------------------------
# create_pending_verification_run -- partial unique index guard
# ---------------------------------------------------------------------------


async def test_create_pending_verification_run_duplicate_active_raises_integrity_error(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)

    async with session_maker() as s:
        await create_pending_verification_run(s, job.id, triggered_by="first")
        await s.commit()

    async with session_maker() as s:
        with pytest.raises(IntegrityError):
            await create_pending_verification_run(s, job.id, triggered_by="second")


# ---------------------------------------------------------------------------
# Alert dedup correctness -- check_backup_verifications
# ---------------------------------------------------------------------------


async def test_check_backup_verifications_dedup_across_ticks(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)
    client = FakeSqlClient(backupset=_damaged_info())
    factory = _factory_for(client)

    first = await check_backup_verifications(session_maker, now=NOW, sql_client_factory=factory)
    assert first == 1
    assert await _active_alert_count(session_maker) == 1

    second_client = FakeSqlClient(backupset=_damaged_info())
    second = await check_backup_verifications(
        session_maker, now=NOW + timedelta(minutes=5), sql_client_factory=_factory_for(second_client)
    )
    assert second == 0
    assert await _active_alert_count(session_maker) == 1


async def test_check_backup_verifications_resolves_alert_on_recovery(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)

    bad_client = FakeSqlClient(backupset=_damaged_info())
    await check_backup_verifications(session_maker, now=NOW, sql_client_factory=_factory_for(bad_client))
    assert await _active_alert_count(session_maker) == 1

    good_client = FakeSqlClient(backupset=_clean_info(), verify_result=_OK_VERIFY)
    count = await check_backup_verifications(
        session_maker, now=NOW + timedelta(minutes=5), sql_client_factory=_factory_for(good_client)
    )
    assert count == 0
    assert await _active_alert_count(session_maker, status=AlertStatus.ACTIVE) == 0
    assert await _active_alert_count(session_maker, status=AlertStatus.RESOLVED) == 1


@pytest.mark.parametrize(
    "status,expected_severity",
    [
        (VerificationRunStatus.CORRUPT, AlertSeverity.CRITICAL),
        (VerificationRunStatus.MISSING, AlertSeverity.CRITICAL),
        (VerificationRunStatus.ERROR, AlertSeverity.WARNING),
    ],
)
async def test_alert_severity_by_status(session, session_maker, status, expected_severity):
    job, _instance, _record = await _job_with_instance(session)

    if status == VerificationRunStatus.CORRUPT:
        client = FakeSqlClient(backupset=_damaged_info())
        factory = _factory_for(client)
    elif status == VerificationRunStatus.MISSING:
        client = FakeSqlClient(backupset=None)
        factory = _factory_for(client)
    else:
        factory = _factory_raising(TimeoutError("connect timed out"))

    run_id = await _pending_run_id(session_maker, job.id)
    run = await execute_verification_run(session_maker, run_id, sql_client_factory=factory, now=NOW)
    assert run.status == status

    async with session_maker() as s:
        alert = (
            await s.execute(
                select(Alert).where(
                    Alert.alert_type == AlertType.BACKUP_VERIFICATION_FAILED,
                    Alert.backup_job_id == job.id,
                )
            )
        ).scalar_one()
    assert alert.severity == expected_severity


# ---------------------------------------------------------------------------
# check_backup_verifications -- concurrency/isolation
# ---------------------------------------------------------------------------


async def test_check_backup_verifications_one_job_failing_weirdly_does_not_block_others(session, session_maker):
    job_a, instance_a, _record_a = await _job_with_instance(session, username="job-a-user")
    job_b, instance_b, _record_b = await _job_with_instance(session, username="job-b-user")

    weird_client = FakeSqlClient(get_latest_exc=RuntimeError("driver blew up unexpectedly"))
    ok_client = FakeSqlClient(backupset=_clean_info(), verify_result=_OK_VERIFY)

    def factory(params: SqlConnectionParams):
        # Route based on the decrypted username, distinct per job's
        # SqlInstance -- SqlConnectionParams carries no job/database
        # identifier directly.
        if params.username == "job-a-user":
            return weird_client
        return ok_client

    count = await check_backup_verifications(session_maker, now=NOW, sql_client_factory=factory)

    async with session_maker() as s:
        runs = (await s.execute(select(VerificationRun))).scalars().all()
    runs_by_job = {r.backup_job_id: r for r in runs}

    assert runs_by_job[job_a.id].status == VerificationRunStatus.ERROR
    assert runs_by_job[job_b.id].status == VerificationRunStatus.OK
    assert count == 1  # only job_a's ERROR raises a new alert


# ---------------------------------------------------------------------------
# check_stuck_verifications
# ---------------------------------------------------------------------------


async def test_check_stuck_verifications_forces_error_past_cutoff(session, session_maker):
    from app.core.config import settings

    job, _instance, _record = await _job_with_instance(session)
    cutoff_seconds = (
        settings.BACKUP_VERIFICATION_CONNECT_TIMEOUT_SECONDS
        + settings.BACKUP_VERIFICATION_RESTORE_TIMEOUT_SECONDS
        + settings.BACKUP_VERIFICATION_STUCK_RUN_GRACE_SECONDS
    )
    stale_started_at = NOW.replace(tzinfo=None) - timedelta(seconds=cutoff_seconds + 60)

    run = build_verification_run(
        job.id, status=VerificationRunStatus.RUNNING, started_at=stale_started_at, triggered_by="scheduler"
    )
    session.add(run)
    await session.commit()
    run_id = run.id

    count = await check_stuck_verifications(session_maker, now=NOW)
    assert count == 1

    async with session_maker() as s:
        refreshed = await s.get(VerificationRun, run_id)
        assert refreshed.status == VerificationRunStatus.ERROR
        assert refreshed.finished_at is not None

    assert await _active_alert_count(session_maker) == 1


async def test_check_stuck_verifications_leaves_recent_running_run_alone(session, session_maker):
    job, _instance, _record = await _job_with_instance(session)
    recent_started_at = NOW.replace(tzinfo=None) - timedelta(seconds=60)

    run = build_verification_run(
        job.id, status=VerificationRunStatus.RUNNING, started_at=recent_started_at, triggered_by="scheduler"
    )
    session.add(run)
    await session.commit()
    run_id = run.id

    count = await check_stuck_verifications(session_maker, now=NOW)
    assert count == 0

    async with session_maker() as s:
        refreshed = await s.get(VerificationRun, run_id)
        assert refreshed.status == VerificationRunStatus.RUNNING


async def test_check_stuck_verifications_does_not_double_count_preexisting_alert(session, session_maker):
    """The exact counting-bug class already fixed once in this codebase
    (see check_missed_runs's fix in app.workers.alert_worker):
    raise_alert_if_absent returns the PRE-EXISTING alert too, so a naive
    "not None" count double-counts. Pre-seed an ACTIVE alert for this job
    before the stuck run is even discovered, and confirm the tick's own
    returned count is 0 (not 1), with still exactly one ACTIVE alert after."""
    from app.routers._alerts import raise_alert_if_absent

    job, _instance, _record = await _job_with_instance(session)

    async with session_maker() as s:
        await raise_alert_if_absent(
            s,
            alert_type=AlertType.BACKUP_VERIFICATION_FAILED,
            severity=AlertSeverity.WARNING,
            entity_type="backup_job",
            entity_column=Alert.backup_job_id,
            entity_id=job.id,
            title="pre-existing",
            message="pre-existing failure",
        )
        await s.commit()

    assert await _active_alert_count(session_maker) == 1

    stale_started_at = NOW.replace(tzinfo=None) - timedelta(hours=2)
    run = build_verification_run(
        job.id, status=VerificationRunStatus.RUNNING, started_at=stale_started_at, triggered_by="scheduler"
    )
    session.add(run)
    await session.commit()
    run_id = run.id

    count = await check_stuck_verifications(session_maker, now=NOW)
    assert count == 0  # must NOT count the pre-existing alert as newly raised

    async with session_maker() as s:
        refreshed = await s.get(VerificationRun, run_id)
        assert refreshed.status == VerificationRunStatus.ERROR  # run is still transitioned

    assert await _active_alert_count(session_maker) == 1  # still exactly one, not two
