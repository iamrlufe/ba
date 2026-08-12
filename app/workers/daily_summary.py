"""Assembles the once-daily alert/job-status summary.

Read-only: makes no writes, does not raise/resolve alerts (that is
exclusively the three `check_*` functions' job in
`app.workers.alert_worker`). Shared by the worker's own once-a-day
background invocation and `GET /api/summary/daily`
(`app.routers.summary`).
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import as_naive_utc
from app.models.alert import Alert
from app.models.backup_job import BackupJob
from app.models.enums import AlertStatus, AlertType, JobRunStatus
from app.models.job_run import JobRun
from app.schemas.alert import AlertRead
from app.schemas.summary import DailyJobStatus, DailySummary, DailySummaryCounts


async def build_daily_summary(session: AsyncSession, *, now: datetime | None = None) -> DailySummary:
    """Read-only. Assembles: ALL currently-ACTIVE alerts (see CONFIRMED
    DECISIONS #1 -- not filtered by age), plus an OK/FAIL/MISSED status per
    enabled BackupJob for the last 24h. Makes no writes -- does not
    raise/resolve alerts (that is exclusively the three check_* functions'
    job).

    Takes a single AsyncSession (not a session_maker): one coherent
    read-only snapshot in one transaction, and this signature lets
    GET /api/summary/daily use the same `session: AsyncSession =
    Depends(get_db)` pattern as every other GET endpoint in this codebase.
    The worker loop wraps a fresh session_maker-provided session around
    this call itself (see app.workers.alert_worker._maybe_run_daily_summary).
    """
    now = now or datetime.now(UTC)
    window_start = now - timedelta(hours=24)
    window_start_naive = as_naive_utc(window_start)
    now_naive = as_naive_utc(now)

    # CONFIRMED DECISIONS #1: ALL currently-ACTIVE alerts, no created_at filter.
    alerts = (
        (
            await session.execute(
                select(Alert)
                .where(Alert.status == AlertStatus.ACTIVE)
                .order_by(Alert.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    missed_job_ids = {
        a.backup_job_id for a in alerts if a.alert_type == AlertType.JOB_MISSED and a.backup_job_id is not None
    }

    jobs = (
        (await session.execute(select(BackupJob).where(BackupJob.is_enabled.is_(True)).order_by(BackupJob.id)))
        .scalars()
        .all()
    )
    job_ids = [j.id for j in jobs]

    latest_run_by_job: dict[int, JobRun] = {}
    if job_ids:
        run_rows = (
            (
                await session.execute(
                    select(JobRun)
                    .where(JobRun.backup_job_id.in_(job_ids), JobRun.created_at >= window_start_naive)
                    .order_by(JobRun.backup_job_id, JobRun.id.desc())
                )
            )
            .scalars()
            .all()
        )
        for run in run_rows:
            # First row encountered per backup_job_id is the highest id
            # (most recent), due to the ORDER BY above.
            latest_run_by_job.setdefault(run.backup_job_id, run)

    job_statuses: list[DailyJobStatus] = []
    for job in jobs:
        latest = latest_run_by_job.get(job.id)
        if job.id in missed_job_ids:
            status = "MISSED"
        elif latest is not None and latest.status in (
            JobRunStatus.FAILED,
            JobRunStatus.TIMEOUT,
            JobRunStatus.STUCK,
        ):
            status = "FAIL"
        else:
            # Covers: latest.status in (SUCCESS, WARNING); latest.status in
            # (PENDING, RUNNING); and "no run in the window at all".
            # CONFIRMED DECISIONS #2: collapsed to OK, no 4th status value.
            status = "OK"
        job_statuses.append(
            DailyJobStatus(
                backup_job_id=job.id,
                name=job.name,
                status=status,
                last_run_id=latest.id if latest else None,
                last_run_status=latest.status if latest else None,
                last_run_finished_at=latest.finished_at if latest else None,
            )
        )

    counts = DailySummaryCounts(
        active_alerts_total=len(alerts),
        jobs_ok=sum(1 for j in job_statuses if j.status == "OK"),
        jobs_fail=sum(1 for j in job_statuses if j.status == "FAIL"),
        jobs_missed=sum(1 for j in job_statuses if j.status == "MISSED"),
    )

    return DailySummary(
        generated_at=now,
        window_start=window_start,
        window_end=now,
        active_alerts=[AlertRead.model_validate(a) for a in alerts],
        jobs=job_statuses,
        counts=counts,
    )
