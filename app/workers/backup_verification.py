"""SQL Server backup-verification module.

For every enabled `BackupJob` with `sql_instance_id` set: connects to the
referenced `SqlInstance`, decrypts credentials via `app/core/security.py`
only at point of use, queries `msdb.dbo.backupset` for the latest backup of
`BackupJob.database_name`, runs `RESTORE VERIFYONLY FROM DISK = '<path>'`
against the most recent `BackupRecord` for that job on the same connection,
records the outcome in `VerificationRun`, and raises/resolves an `Alert`
(`AlertType.BACKUP_VERIFICATION_FAILED`) through
`app.routers._alerts.raise_alert_if_absent` / `resolve_active_alert`.

Sibling to `app.workers.alert_worker`, not folded into it -- wired in via
`check_backup_verifications` / `check_stuck_verifications`, called from
`alert_worker_loop` (see that module).

NEVER log a decrypted or encrypted credential value anywhere in this
module, not even at DEBUG.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.security import decrypt_secret
from app.core.sql_client import (
    SqlClientFactory,
    SqlConnectionParams,
    default_sql_client_factory,
)
from app.core.timeutils import as_naive_utc
from app.models.alert import Alert
from app.models.backup_job import BackupJob
from app.models.backup_record import BackupRecord
from app.models.enums import (
    VERIFICATION_RUN_TERMINAL_STATUSES,
    AlertSeverity,
    AlertType,
    VerificationRunStatus,
)
from app.models.sql_instance import SqlInstance
from app.models.verification_run import VerificationRun
from app.routers._alerts import get_active_alert, raise_alert_if_absent, resolve_active_alert

logger = logging.getLogger(__name__)


async def create_pending_verification_run(
    session: AsyncSession, backup_job_id: int, *, triggered_by: str
) -> VerificationRun:
    """Insert a PENDING VerificationRun and flush.

    Deliberately no local try/except: if a run is already active
    (PENDING/RUNNING) for this backup_job_id, the partial unique index
    (`uq_verification_runs_active_per_backup_job`) raises `IntegrityError`
    on flush/commit. Request-handler callers rely on the global
    IntegrityError -> 409 handler; the worker-tick caller
    (`check_backup_verifications`) MUST catch it locally and skip that job,
    not let it abort the tick.
    """
    run = VerificationRun(backup_job_id=backup_job_id, triggered_by=triggered_by)
    session.add(run)
    await session.flush()
    return run


async def _mark_run_error_best_effort(
    session_maker: async_sessionmaker[AsyncSession], run_id: int, now_naive: datetime
) -> None:
    """Last-resort cleanup for `execute_verification_run`'s outer
    try/except: CAS-update the row to ERROR, only if it is not already in a
    terminal status. Never raises.
    """
    try:
        async with session_maker() as session:
            await session.execute(
                update(VerificationRun)
                .where(
                    VerificationRun.id == run_id,
                    VerificationRun.status.not_in(VERIFICATION_RUN_TERMINAL_STATUSES),
                )
                .values(
                    status=VerificationRunStatus.ERROR,
                    finished_at=now_naive,
                    error_message=(
                        "Verification run failed unexpectedly; see server logs for the "
                        "full traceback."
                    ),
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
    except Exception:
        logger.exception("failed to best-effort mark verification run %s as ERROR", run_id)


async def execute_verification_run(
    session_maker: async_sessionmaker[AsyncSession],
    run_id: int,
    *,
    sql_client_factory: SqlClientFactory = default_sql_client_factory,
    now: datetime | None = None,
) -> VerificationRun | None:
    """Runs a single VerificationRun to completion (or a terminal ERROR).

    Must NEVER let an exception escape -- called from a detached
    `asyncio.create_task` in the POST /verify endpoint with nothing
    downstream to catch it, and also called synchronously (but still
    defensively) from `check_backup_verifications`.
    """
    now = now or datetime.now(UTC)
    now_naive = as_naive_utc(now)

    try:
        async with session_maker() as session:
            run = await session.get(VerificationRun, run_id)
            if run is None:
                logger.error("execute_verification_run: VerificationRun %s not found", run_id)
                return None

            job = await session.get(BackupJob, run.backup_job_id)
            if job is None:
                raise RuntimeError(
                    f"BackupJob {run.backup_job_id} not found for VerificationRun {run_id}"
                )

            record_stmt = (
                select(BackupRecord)
                .where(BackupRecord.backup_job_id == run.backup_job_id)
                .order_by(BackupRecord.detected_at.desc())
                .limit(1)
            )
            record = (await session.execute(record_stmt)).scalars().first()

            # Step 2: snapshot the denormalized FKs onto the row, commit.
            run.sql_instance_id = job.sql_instance_id
            run.backup_record_id = record.id if record is not None else None
            await session.commit()

            job_id = job.id
            job_name = job.name

            status: VerificationRunStatus
            error_message: str | None = None
            verifyonly_output: str | None = None
            msdb_backup_date: datetime | None = None
            msdb_is_damaged: bool | None = None

            if record is None:
                # Classification rule 1: no BackupRecord for this job ->
                # MISSING. No SQL connection attempted, no disk_path built.
                status = VerificationRunStatus.MISSING
                error_message = (
                    f"No BackupRecord found for backup_job_id={run.backup_job_id}; "
                    f"nothing to verify"
                )
            else:
                disk_path = record.remote_path.rstrip("/\\") + "\\" + record.file_name

                cas_running = await session.execute(
                    update(VerificationRun)
                    .where(
                        VerificationRun.id == run_id,
                        VerificationRun.status == VerificationRunStatus.PENDING,
                    )
                    .values(status=VerificationRunStatus.RUNNING, started_at=now_naive)
                    .execution_options(synchronize_session=False)
                )
                if cas_running.rowcount == 0:
                    logger.warning(
                        "verification run %s was not PENDING at RUNNING-transition time; "
                        "leaving it untouched (concurrent modification?)",
                        run_id,
                    )
                    await session.commit()
                    await session.refresh(run)
                    return run
                await session.commit()
                await session.refresh(run)

                sql_instance = (
                    await session.get(SqlInstance, job.sql_instance_id)
                    if job.sql_instance_id is not None
                    else None
                )
                if sql_instance is None:
                    raise RuntimeError(
                        f"SqlInstance {job.sql_instance_id} not found for BackupJob {job_id}"
                    )

                database_name = job.database_name
                host = sql_instance.host
                port = sql_instance.port
                instance_name = sql_instance.instance_name
                use_windows_auth = sql_instance.use_windows_auth
                username_encrypted = sql_instance.username_encrypted
                password_encrypted = sql_instance.password_encrypted

                # Decrypt credentials via app.core.security.decrypt_secret
                # only here, only at the point of building
                # SqlConnectionParams -- never earlier, never logged.
                username = decrypt_secret(username_encrypted) if username_encrypted else None
                password = decrypt_secret(password_encrypted) if password_encrypted else None

                params = SqlConnectionParams(
                    host=host,
                    port=port,
                    instance_name=instance_name,
                    username=username,
                    password=password,
                    use_windows_auth=use_windows_auth,
                    connect_timeout_seconds=settings.BACKUP_VERIFICATION_CONNECT_TIMEOUT_SECONDS,
                )
                restore_timeout_seconds = settings.BACKUP_VERIFICATION_RESTORE_TIMEOUT_SECONDS

                def _run_blocking():
                    client = sql_client_factory(params)
                    try:
                        info = client.get_latest_backupset(database_name)
                        verify_result = client.restore_verifyonly(
                            disk_path, timeout_seconds=restore_timeout_seconds
                        )
                        return info, verify_result
                    finally:
                        client.close()

                try:
                    msdb_info, verify_result = await asyncio.to_thread(_run_blocking)
                except Exception as exc:
                    # Connect failure/timeout, _validate_disk_path's
                    # ValueError, WindowsAuthNotSupportedError, or any other
                    # driver error. Never interpolate params.username /
                    # params.password into this message.
                    status = VerificationRunStatus.ERROR
                    error_message = f"{type(exc).__name__}: {exc}"
                    verifyonly_output = None
                else:
                    if msdb_info is None:
                        status = VerificationRunStatus.MISSING
                        error_message = (
                            f"No msdb.dbo.backupset row found for database '{database_name}'"
                        )
                    elif msdb_info.is_damaged:
                        status = VerificationRunStatus.CORRUPT
                        error_message = "msdb.dbo.backupset.is_damaged=1 for the latest backup"
                        msdb_backup_date = msdb_info.backup_finish_date
                        msdb_is_damaged = msdb_info.is_damaged
                    elif not verify_result.succeeded:
                        # 3201 ("cannot open backup device") / 5133
                        # ("directory lookup failed") are infrastructure /
                        # path problems, not proof of corruption.
                        if verify_result.error_number in (3201, 5133):
                            status = VerificationRunStatus.ERROR
                        else:
                            status = VerificationRunStatus.CORRUPT
                        error_message = verify_result.error_message
                        verifyonly_output = verify_result.output
                        msdb_backup_date = msdb_info.backup_finish_date
                        msdb_is_damaged = msdb_info.is_damaged
                    else:
                        status = VerificationRunStatus.OK
                        error_message = None
                        verifyonly_output = verify_result.output
                        msdb_backup_date = msdb_info.backup_finish_date
                        msdb_is_damaged = msdb_info.is_damaged

                    # Soft, informational-only consistency check -- see the
                    # column comment on app.models.backup_job.BackupJob
                    # .local_backup_path_pattern. status/error_message/
                    # msdb_backup_date/msdb_is_damaged are already fully
                    # decided by the classification chain above at this
                    # point; this block must NEVER touch them (or alerting
                    # in Step 5) -- it only ever appends a note to the local
                    # verifyonly_output variable, which Step 4 persists
                    # as-is.
                    if (
                        job.local_backup_path_pattern
                        and msdb_info is not None
                        and msdb_info.physical_device_name
                    ):
                        normalized_pattern = job.local_backup_path_pattern.strip().rstrip(
                            "\\/"
                        ).lower()
                        normalized_physical = msdb_info.physical_device_name.strip().lower()
                        if normalized_pattern and normalized_pattern not in normalized_physical:
                            note = (
                                f"local_backup_path_pattern mismatch: configured pattern "
                                f"{job.local_backup_path_pattern!r} was not found in the "
                                f"physical_device_name reported by msdb.dbo.backupmediafamily "
                                f"({msdb_info.physical_device_name!r}). Informational only -- "
                                f"does not affect verification status, msdb_is_damaged, or alerting."
                            )
                            verifyonly_output = (
                                note
                                if verifyonly_output is None
                                else f"{verifyonly_output}\n{note}"
                            )

            # Step 4: CAS-guarded terminal update.
            terminal_result = await session.execute(
                update(VerificationRun)
                .where(
                    VerificationRun.id == run_id,
                    VerificationRun.status.in_(
                        (VerificationRunStatus.RUNNING, VerificationRunStatus.PENDING)
                    ),
                )
                .values(
                    status=status,
                    finished_at=now_naive,
                    msdb_backup_date=msdb_backup_date,
                    msdb_is_damaged=msdb_is_damaged,
                    verifyonly_output=verifyonly_output,
                    error_message=error_message,
                )
                .execution_options(synchronize_session=False)
            )
            if terminal_result.rowcount == 0:
                logger.warning(
                    "verification run %s was not RUNNING/PENDING at terminal-update time; "
                    "leaving it untouched (concurrent modification?)",
                    run_id,
                )
                await session.commit()
                await session.refresh(run)
                return run

            await session.commit()
            await session.refresh(run)

            # Step 5: raise/resolve BACKUP_VERIFICATION_FAILED, dedup'd by
            # backup_job_id (not run_id) -- see app.routers._alerts.
            if status != VerificationRunStatus.OK:
                await raise_alert_if_absent(
                    session,
                    alert_type=AlertType.BACKUP_VERIFICATION_FAILED,
                    severity=(
                        AlertSeverity.CRITICAL
                        if status in (VerificationRunStatus.CORRUPT, VerificationRunStatus.MISSING)
                        else AlertSeverity.WARNING
                    ),
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job_id,
                    title=f"Backup verification failed for '{job_name}'",
                    message=(
                        f"VerificationRun {run.id} for backup job '{job_name}' (id={job_id}) "
                        f"came back {status.value}: {error_message}"
                    ),
                )
            else:
                await resolve_active_alert(
                    session,
                    alert_type=AlertType.BACKUP_VERIFICATION_FAILED,
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job_id,
                )

            await session.commit()
            await session.refresh(run)
            return run

    except Exception:
        logger.exception("verification run %s failed unexpectedly", run_id)
        await _mark_run_error_best_effort(session_maker, run_id, now_naive)
        return None


async def check_backup_verifications(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    sql_client_factory: SqlClientFactory = default_sql_client_factory,
) -> int:
    """Periodic entry point: for every enabled BackupJob with
    sql_instance_id set, creates + runs a VerificationRun. Returns the
    number of newly-raised BACKUP_VERIFICATION_FAILED alerts this tick (not
    the count of runs performed) -- consistent with the other three
    check_* functions' return-value convention in app.workers.alert_worker.
    """
    now = now or datetime.now(UTC)
    raised_count = 0

    async with session_maker() as session:
        job_ids = (
            (
                await session.execute(
                    select(BackupJob.id).where(
                        BackupJob.is_enabled.is_(True), BackupJob.sql_instance_id.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )

    for job_id in job_ids:
        try:
            async with session_maker() as session:
                try:
                    run = await create_pending_verification_run(
                        session, job_id, triggered_by="scheduler"
                    )
                    await session.commit()
                except IntegrityError:
                    # A run is already active (PENDING/RUNNING) for this
                    # job -- skip it this tick, don't abort the whole tick.
                    await session.rollback()
                    continue
                run_id = run.id

                # Pre-check-then-compare technique, REQUIRED to correctly
                # count only NEWLY raised alerts (mirrors
                # check_missed_runs's fix in app.workers.alert_worker --
                # see its inline comment). Because the actual
                # raise_alert_if_absent call for this alert type happens
                # deep inside execute_verification_run (shared with the
                # on-demand POST /verify path), we can't inspect its return
                # value directly here -- so we snapshot the ACTIVE-alert
                # state before running verification and compare it against
                # the state after, instead of trusting a bare "is there an
                # active alert now" check (which would double-count every
                # subsequent tick for an already-failing job).
                already_active = await get_active_alert(
                    session,
                    alert_type=AlertType.BACKUP_VERIFICATION_FAILED,
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job_id,
                )

            await execute_verification_run(
                session_maker, run_id, sql_client_factory=sql_client_factory, now=now
            )

            async with session_maker() as session:
                now_active = await get_active_alert(
                    session,
                    alert_type=AlertType.BACKUP_VERIFICATION_FAILED,
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job_id,
                )
            if already_active is None and now_active is not None:
                raised_count += 1
        except Exception:
            logger.exception("check_backup_verifications: job %s failed; skipping", job_id)
            continue

    return raised_count


async def check_stuck_verifications(
    session_maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> int:
    """Cleans up VerificationRuns orphaned in RUNNING (e.g. a process
    crash/restart mid-verification -- the `asyncio.to_thread` blocking call
    cannot be cleanly cancelled). Since the active-run unique index blocks
    all future verifications for that job while one is stuck RUNNING, this
    must run on the regular (short) alert-worker tick cadence, not the long
    BACKUP_VERIFICATION_INTERVAL_SECONDS one -- see
    app.workers.alert_worker._run_periodic_checks. Returns the number of
    newly-raised alerts.
    """
    now_naive = as_naive_utc(now or datetime.now(UTC))
    cutoff = now_naive - timedelta(
        seconds=(
            settings.BACKUP_VERIFICATION_CONNECT_TIMEOUT_SECONDS
            + settings.BACKUP_VERIFICATION_RESTORE_TIMEOUT_SECONDS
            + settings.BACKUP_VERIFICATION_STUCK_RUN_GRACE_SECONDS
        )
    )
    raised_count = 0

    async with session_maker() as session:
        stuck_stmt = (
            select(VerificationRun, BackupJob)
            .join(BackupJob, VerificationRun.backup_job_id == BackupJob.id)
            .where(
                VerificationRun.status == VerificationRunStatus.RUNNING,
                VerificationRun.started_at.is_not(None),
                VerificationRun.started_at < cutoff,
            )
        )
        rows = (await session.execute(stuck_stmt)).all()

        for run, job in rows:
            try:
                result = await session.execute(
                    update(VerificationRun)
                    .where(
                        VerificationRun.id == run.id,
                        VerificationRun.status == VerificationRunStatus.RUNNING,
                    )
                    .values(
                        status=VerificationRunStatus.ERROR,
                        finished_at=now_naive,
                        error_message=(
                            "Verification run stuck in RUNNING beyond max expected duration "
                            "(connect + restore timeout + grace); likely a process "
                            "crash/restart mid-verification"
                        ),
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount == 0:
                    # Race with a legitimately-finishing run -- already
                    # resolved by something else, nothing to do.
                    continue

                already_active = await get_active_alert(
                    session,
                    alert_type=AlertType.BACKUP_VERIFICATION_FAILED,
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job.id,
                )
                raised = await raise_alert_if_absent(
                    session,
                    alert_type=AlertType.BACKUP_VERIFICATION_FAILED,
                    severity=AlertSeverity.WARNING,
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job.id,
                    title=f"Backup verification failed for '{job.name}'",
                    message=(
                        f"VerificationRun {run.id} for backup job '{job.name}' (id={job.id}) "
                        f"came back ERROR: Verification run stuck in RUNNING beyond max "
                        f"expected duration (connect + restore timeout + grace); likely a "
                        f"process crash/restart mid-verification"
                    ),
                )
                if raised is not None and already_active is None:
                    raised_count += 1
                await session.commit()
            except Exception:
                logger.exception("check_stuck_verifications: run %s failed; skipping", run.id)
                continue

    return raised_count


# Module-level tracking for detached asyncio.create_task(...) calls (see
# POST /api/backup-jobs/{id}/verify in app.routers.backup_jobs) -- keeps a
# strong reference so the task isn't garbage-collected mid-flight, per the
# asyncio docs' recommendation for "fire and forget" tasks.
_background_tasks: set[asyncio.Task] = set()


def _track_background_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
