"""Background alert-detection worker.

Runs several periodic checks (missed backup-job runs, agent-offline
staleness, stuck/timed-out job runs, stuck-PENDING-dispatch runs, stuck
verifications) plus a once-daily summary build, all driven from a single
`asyncio.Task` loop wired into `app.main`'s
`lifespan` (see `alert_worker_loop`). Never call these functions from
request-handling code -- they are intended to run only from the
background task.

All alert creation/resolution goes exclusively through
`app.routers._alerts.raise_alert_if_absent` / `resolve_active_alert` --
never ad hoc `Alert(...)` construction.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as dt_time
from datetime import timedelta

from croniter import CroniterError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.timeutils import as_naive_utc, compute_next_scheduled_run
from app.core.ws_manager import manager
from app.models.alert import Alert
from app.models.backup_job import BackupJob
from app.models.enums import AlertSeverity, AlertType, JobRunStatus, ServerStatus, TriggerMode
from app.models.job_run import JobRun
from app.models.server import Server
from app.routers._alerts import get_active_alert, raise_alert_if_absent, resolve_active_alert
from app.schemas.job_run import JobRunRead
from app.workers.backup_verification import check_backup_verifications, check_stuck_verifications
from app.workers.daily_summary import build_daily_summary

logger = logging.getLogger(__name__)


async def check_missed_runs(
    session_maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> int:
    """For every enabled SCHEDULE-mode BackupJob with no currently active
    (PENDING/RUNNING) run, raise JOB_MISSED if the next scheduled fire (per
    schedule_cron/timezone, anchored on last_run_at or, if the job has
    never run, on created_at) is overdue by more than
    missed_run_grace_minutes; resolve JOB_MISSED once the job is no longer
    overdue. Returns the number of new alerts raised.

    WATCH-mode jobs are excluded entirely -- they have no fixed schedule to
    be "on time" against (see TriggerMode.WATCH). Jobs with an active run
    are also excluded: a copy time-window (BackupJob.copy_window_*) can
    now leave a SCHEDULE-mode run legitimately PENDING for hours waiting
    for its window to open, and last_run_at-anchored missed-run detection
    would otherwise misread that as overdue.
    """
    now_naive = as_naive_utc(now or datetime.now(UTC))
    raised_count = 0

    async with session_maker() as session:
        active_run_subq = select(JobRun.backup_job_id).where(
            JobRun.status.in_((JobRunStatus.PENDING, JobRunStatus.RUNNING))
        )
        jobs = (
            (
                await session.execute(
                    select(BackupJob).where(
                        BackupJob.is_enabled.is_(True),
                        BackupJob.trigger_mode == TriggerMode.SCHEDULE,
                        BackupJob.id.not_in(active_run_subq),
                    )
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            anchor = job.last_run_at if job.last_run_at is not None else job.created_at
            try:
                expected = compute_next_scheduled_run(job.schedule_cron, job.timezone, anchor)
            except (CroniterError, TypeError):
                # CroniterError covers the common malformed-cron-string case;
                # TypeError also catches croniter's CroniterBadTypeRangeError,
                # which is NOT a CroniterError subclass (see croniter source)
                # -- without this, that exception type would escape to the
                # coarser except Exception in alert_worker_loop and skip the
                # entire tick (all three checks), not just this one job.
                logger.warning("skipping job %s: invalid schedule_cron %r", job.id, job.schedule_cron)
                continue

            overdue_by = now_naive - expected
            if overdue_by > timedelta(minutes=job.missed_run_grace_minutes):
                # raise_alert_if_absent returns the pre-existing ACTIVE alert
                # too (not just newly-created ones), so a not-None result
                # alone doesn't mean a new alert was raised on this tick --
                # check first, or every subsequent tick for the same overdue
                # job would double-count it.
                already_active = await get_active_alert(
                    session,
                    alert_type=AlertType.JOB_MISSED,
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job.id,
                )
                raised = await raise_alert_if_absent(
                    session,
                    alert_type=AlertType.JOB_MISSED,
                    severity=AlertSeverity.WARNING,
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job.id,
                    title=f"Backup job '{job.name}' missed its scheduled run",
                    message=(
                        f"Backup job '{job.name}' (id={job.id}) was expected to run at "
                        f"{expected.isoformat()} (grace {job.missed_run_grace_minutes}m) but has not."
                    ),
                )
                if raised is not None and already_active is None:
                    raised_count += 1
            else:
                await resolve_active_alert(
                    session,
                    alert_type=AlertType.JOB_MISSED,
                    entity_type="backup_job",
                    entity_column=Alert.backup_job_id,
                    entity_id=job.id,
                )
            await session.commit()

    return raised_count


async def check_agent_offline(
    session_maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> int:
    """For every non-deleted, non-DISABLED Server, mark OFFLINE + raise
    AGENT_OFFLINE if last_seen_at is stale beyond
    settings.AGENT_OFFLINE_THRESHOLD_MINUTES. Does NOT resolve
    AGENT_OFFLINE -- that happens exclusively in app.routers.agents on the
    next heartbeat (see spec §10). Returns the number of new alerts raised.
    """
    now_naive = as_naive_utc(now or datetime.now(UTC))
    raised_count = 0

    async with session_maker() as session:
        servers = (
            (
                await session.execute(
                    select(Server).where(
                        Server.is_deleted.is_(False),
                        Server.status.not_in((ServerStatus.DISABLED, ServerStatus.OFFLINE)),
                    )
                )
            )
            .scalars()
            .all()
        )
        for server in servers:
            if server.last_seen_at is None:
                continue

            seen_at_snapshot = server.last_seen_at
            stale = now_naive - as_naive_utc(seen_at_snapshot) > timedelta(
                minutes=settings.AGENT_OFFLINE_THRESHOLD_MINUTES
            )
            if not stale:
                continue

            result = await session.execute(
                update(Server)
                .where(
                    Server.id == server.id,
                    Server.last_seen_at == seen_at_snapshot,
                    Server.status != ServerStatus.DISABLED,
                )
                .values(status=ServerStatus.OFFLINE)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:
                # A concurrent heartbeat (or admin disable) already changed
                # this row -- continue without alerting.
                continue

            raised = await raise_alert_if_absent(
                session,
                alert_type=AlertType.AGENT_OFFLINE,
                severity=AlertSeverity.CRITICAL,
                entity_type="server",
                entity_column=Alert.server_id,
                entity_id=server.id,
                title=f"Server '{server.name}' agent offline",
                message=(
                    f"No heartbeat from server '{server.name}' (id={server.id}) in over "
                    f"{settings.AGENT_OFFLINE_THRESHOLD_MINUTES} minutes (last seen {seen_at_snapshot})."
                ),
            )
            if raised is not None:
                raised_count += 1
            await session.commit()

    return raised_count


async def check_job_timeouts(
    session_maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> int:
    """For every RUNNING JobRun whose BackupJob has a non-null
    expected_max_duration_minutes, transition to TIMEOUT (CAS-guarded,
    mirroring app.routers.job_runs.complete_job_run) once started_at is
    older than that duration, update BackupJob.last_run_at, notify/close
    any connected WebSocket clients for that run (see CONFIRMED DECISIONS
    #5), and raise JOB_TIMEOUT. Jobs with expected_max_duration_minutes
    IS NULL are never considered. Returns the number of runs timed out.
    """
    now_naive = as_naive_utc(now or datetime.now(UTC))
    timed_out_count = 0

    async with session_maker() as session:
        stmt = (
            select(JobRun, BackupJob)
            .join(BackupJob, JobRun.backup_job_id == BackupJob.id)
            .where(JobRun.status == JobRunStatus.RUNNING, BackupJob.expected_max_duration_minutes.is_not(None))
        )
        rows = (await session.execute(stmt)).all()

        for run, job in rows:
            if run.started_at is None:
                logger.warning("job run %s is RUNNING with no started_at; skipping timeout check", run.id)
                continue

            elapsed = now_naive - as_naive_utc(run.started_at)
            if elapsed <= timedelta(minutes=job.expected_max_duration_minutes):
                continue

            duration_seconds = int(elapsed.total_seconds())
            result = await session.execute(
                update(JobRun)
                .where(JobRun.id == run.id, JobRun.status == JobRunStatus.RUNNING)
                .values(
                    status=JobRunStatus.TIMEOUT,
                    finished_at=now_naive,
                    duration_seconds=duration_seconds,
                    error_message=(
                        f"Run exceeded expected_max_duration_minutes="
                        f"{job.expected_max_duration_minutes} and was marked TIMEOUT "
                        f"by the alert worker."
                    ),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:
                # Concurrently completed/cancelled by something else between
                # the SELECT and this UPDATE -- no error, no alert.
                continue

            job.last_run_at = now_naive
            await raise_alert_if_absent(
                session,
                alert_type=AlertType.JOB_TIMEOUT,
                severity=AlertSeverity.CRITICAL,
                entity_type="backup_job",
                entity_column=Alert.backup_job_id,
                entity_id=job.id,
                title=f"Backup job '{job.name}' timed out",
                message=(
                    f"JobRun {run.id} for backup job '{job.name}' (id={job.id}) exceeded its "
                    f"{job.expected_max_duration_minutes}-minute timeout and was forcibly marked TIMEOUT."
                ),
            )
            await session.commit()
            timed_out_count += 1

            # Per CONFIRMED DECISIONS #5: `run` is stale after the Core
            # UPDATE above (synchronize_session=False) -- re-fetch it first
            # so the broadcast payload reflects the actual persisted
            # TIMEOUT state, not the pre-update RUNNING snapshot. Mirrors
            # complete_job_run's own broadcast/close-all sequence in
            # app/routers/job_runs.py exactly.
            await session.refresh(run)
            run_read = JobRunRead.model_validate(run)
            await manager.broadcast(run.id, run_read.model_dump(mode="json"))
            await manager.close_all(run.id, code=1000, reason="job run timed out")

    return timed_out_count


async def check_stuck_pending_dispatch(
    session_maker: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> int:
    """For every PENDING JobRun that has never been dispatched
    (dispatched_at IS NULL -- see create_job_run / POST /api/job-runs/{id}/
    claim in app.routers.job_runs), auto-mark it STUCK once it has sat that
    way longer than its BackupJob's pending_to_running_grace_minutes.

    Deliberately NOT filtered on BackupJob.is_enabled -- a disabled job's
    stuck manual run must still be caught. missed_run_grace_minutes-style
    per-job-varying threshold, so the comparison is done in Python (can't
    express a varying-interval comparison cleanly in the WHERE clause), same
    as check_missed_runs. Returns the number of runs marked STUCK.
    """
    now_naive = as_naive_utc(now or datetime.now(UTC))
    stuck_count = 0

    async with session_maker() as session:
        stmt = (
            select(JobRun, BackupJob)
            .join(BackupJob, JobRun.backup_job_id == BackupJob.id)
            .where(JobRun.status == JobRunStatus.PENDING, JobRun.dispatched_at.is_(None))
        )
        rows = (await session.execute(stmt)).all()

        for run, job in rows:
            elapsed = now_naive - as_naive_utc(run.created_at)
            if elapsed <= timedelta(minutes=job.pending_to_running_grace_minutes):
                continue

            result = await session.execute(
                update(JobRun)
                .where(
                    JobRun.id == run.id,
                    JobRun.status == JobRunStatus.PENDING,
                    JobRun.dispatched_at.is_(None),
                )
                .values(
                    status=JobRunStatus.STUCK,
                    finished_at=now_naive,
                    cancel_requested_at=now_naive,
                    cancel_requested_by="system:stuck_pending_detector",
                    cancel_acknowledged_at=now_naive,
                    error_message=(
                        f"Run sat PENDING for over {job.pending_to_running_grace_minutes}m with no "
                        f"confirmation the agent ever received it; auto-marked STUCK by the "
                        f"stuck-pending detector."
                    ),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:
                # Concurrently claimed/completed/cancelled between the
                # SELECT and this UPDATE -- no error, no alert.
                continue

            job.last_run_at = now_naive

            already_active = await get_active_alert(
                session,
                alert_type=AlertType.JOB_STUCK_PENDING,
                entity_type="backup_job",
                entity_column=Alert.backup_job_id,
                entity_id=job.id,
            )
            raised = await raise_alert_if_absent(
                session,
                alert_type=AlertType.JOB_STUCK_PENDING,
                severity=AlertSeverity.CRITICAL,
                entity_type="backup_job",
                entity_column=Alert.backup_job_id,
                entity_id=job.id,
                title=f"Backup job '{job.name}' has a stuck PENDING run",
                message=(
                    f"JobRun {run.id} for backup job '{job.name}' (id={job.id}) sat PENDING for over "
                    f"{job.pending_to_running_grace_minutes}m without being dispatched and was "
                    f"auto-marked STUCK."
                ),
            )
            if raised is not None and already_active is None:
                stuck_count += 1

            await session.commit()

            # `run` is stale after the Core UPDATE above
            # (synchronize_session=False) -- re-fetch it first so the
            # broadcast payload reflects the actual persisted STUCK state.
            # Mirrors check_job_timeouts's own broadcast/close-all sequence.
            await session.refresh(run)
            run_read = JobRunRead.model_validate(run)
            await manager.broadcast(run.id, run_read.model_dump(mode="json"))
            await manager.close_all(run.id, code=1000, reason="job run finished")

    return stuck_count


@dataclass
class _WorkerState:
    last_daily_summary_date: date | None = None
    last_backup_verification_at: datetime | None = None
    last_stuck_verification_check_at: datetime | None = None


async def _run_periodic_checks(session_maker: async_sessionmaker[AsyncSession]) -> None:
    await check_missed_runs(session_maker)
    await check_agent_offline(session_maker)
    await check_job_timeouts(session_maker)
    await check_stuck_pending_dispatch(session_maker)
    # Cheap (one indexed query) and needs to catch stuck runs reasonably
    # promptly, so it runs on the regular tick cadence unconditionally,
    # like the three checks above -- NOT gated behind
    # BACKUP_VERIFICATION_INTERVAL_SECONDS (see
    # app.workers.backup_verification.check_stuck_verifications docstring).
    await check_stuck_verifications(session_maker)


async def _maybe_run_daily_summary(
    session_maker: async_sessionmaker[AsyncSession], state: _WorkerState, *, now: datetime | None = None
) -> None:
    now = now or datetime.now(UTC)
    today = now.date()
    due = now.time() >= dt_time(settings.DAILY_SUMMARY_HOUR_UTC, settings.DAILY_SUMMARY_MINUTE_UTC)
    if state.last_daily_summary_date == today or not due:
        return
    async with session_maker() as session:
        summary = await build_daily_summary(session, now=now)
    logger.info("daily summary: %s", summary.model_dump(mode="json"))
    state.last_daily_summary_date = today


async def _maybe_run_backup_verifications(
    session_maker: async_sessionmaker[AsyncSession], state: _WorkerState, *, now: datetime | None = None
) -> None:
    now = now or datetime.now(UTC)
    if not settings.BACKUP_VERIFICATION_ENABLED:
        return
    due = (
        state.last_backup_verification_at is None
        or (now - state.last_backup_verification_at).total_seconds()
        >= settings.BACKUP_VERIFICATION_INTERVAL_SECONDS
    )
    if not due:
        return
    await check_backup_verifications(session_maker, now=now)
    state.last_backup_verification_at = now


async def alert_worker_loop(
    session_maker: async_sessionmaker[AsyncSession], *, stop_event: asyncio.Event | None = None
) -> None:
    """Runs forever until `stop_event` is set (or the task is cancelled):
    every settings.ALERT_WORKER_TICK_INTERVAL_SECONDS, runs the three
    periodic checks (plus check_stuck_verifications), then (if due) the
    daily summary and the backup-verification sweep. Intended to be
    wrapped in asyncio.create_task() from app.main.lifespan -- never call
    directly from request-handling code. A single tick's exception is
    caught and logged, not propagated, so one bad tick never kills the
    worker permanently.
    """
    state = _WorkerState()
    while stop_event is None or not stop_event.is_set():
        try:
            await _run_periodic_checks(session_maker)
            await _maybe_run_daily_summary(session_maker, state)
            await _maybe_run_backup_verifications(session_maker, state)
        except Exception:
            logger.exception("alert worker tick failed; will retry next tick")
        await asyncio.sleep(settings.ALERT_WORKER_TICK_INTERVAL_SECONDS)
