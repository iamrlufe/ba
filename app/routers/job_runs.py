from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_admin_or_agent_key
from app.core.db import get_db
from app.core.ws_manager import manager
from app.models.alert import Alert
from app.models.backup_job import BackupJob
from app.models.enums import JOB_RUN_TERMINAL_STATUSES, AlertSeverity, AlertType, JobRunStatus
from app.models.job_run import JobRun
from app.routers._alerts import raise_alert_if_absent, resolve_active_alert
from app.routers._deps import get_or_404
from app.schemas.common import PaginatedResponse
from app.schemas.job_run import (
    JobRunCompleteRequest,
    JobRunCreate,
    JobRunLogRead,
    JobRunRead,
    JobRunUpdate,
    is_valid_transition,
)

router = APIRouter(tags=["job-runs"])


def _as_naive_utc(dt: datetime) -> datetime:
    """Normalize a datetime for arithmetic against SQLite-round-tripped values.

    SQLite has no native timezone-aware datetime type: even though the ORM
    columns are declared `DateTime(timezone=True)`, values read back after
    a round trip through the DB come back tz-naive, while freshly
    constructed Python datetimes (e.g. `datetime.now(UTC)`) are
    tz-aware. Subtracting a naive and an aware datetime raises `TypeError`,
    so both operands are normalized to naive UTC before arithmetic here.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


@router.post(
    "",
    response_model=JobRunRead,
    status_code=201,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def create_job_run(payload: JobRunCreate, session: AsyncSession = Depends(get_db)) -> JobRun:
    job = await get_or_404(session, BackupJob, payload.backup_job_id)
    if not job.is_enabled:
        raise HTTPException(status_code=409, detail="Cannot start a run for a disabled backup job")

    # Deliberately no local try/except around the INSERT: if there is
    # already an active (PENDING/RUNNING) run for this backup_job_id, the
    # partial unique index raises IntegrityError on commit, which the
    # global handler converts to 409.
    run = JobRun(backup_job_id=payload.backup_job_id, triggered_by=payload.triggered_by)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.get("", response_model=PaginatedResponse[JobRunRead], dependencies=[Depends(get_current_user)])
async def list_job_runs(
    backup_job_id: int | None = None,
    status: JobRunStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[JobRunRead]:
    filters = []
    if backup_job_id is not None:
        filters.append(JobRun.backup_job_id == backup_job_id)
    if status is not None:
        filters.append(JobRun.status == status)

    total_stmt = select(func.count()).select_from(JobRun)
    items_stmt = select(JobRun)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(JobRun.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[JobRunRead](
        items=[JobRunRead.model_validate(r) for r in items], total=total, limit=limit, offset=offset
    )


@router.get("/{job_run_id}", response_model=JobRunRead, dependencies=[Depends(get_current_user)])
async def get_job_run(job_run_id: int, session: AsyncSession = Depends(get_db)) -> JobRun:
    return await get_or_404(session, JobRun, job_run_id)


@router.get(
    "/{job_run_id}/log", response_model=JobRunLogRead, dependencies=[Depends(get_current_user)]
)
async def get_job_run_log(job_run_id: int, session: AsyncSession = Depends(get_db)) -> JobRun:
    return await get_or_404(session, JobRun, job_run_id)


@router.patch(
    "/{job_run_id}",
    response_model=JobRunRead,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def update_job_run(
    job_run_id: int, payload: JobRunUpdate, session: AsyncSession = Depends(get_db)
) -> JobRun:
    run = await get_or_404(session, JobRun, job_run_id)
    expected_status = run.status

    if expected_status in JOB_RUN_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot modify a job run that has already finished")

    if payload.status is not None:
        if payload.status in JOB_RUN_TERMINAL_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Terminal status transitions are not allowed via PATCH -- "
                    "use POST /api/job-runs/{id}/complete instead"
                ),
            )
        if not is_valid_transition(expected_status, payload.status):
            raise HTTPException(
                status_code=409,
                detail=f"Invalid status transition: {expected_status.value} -> {payload.status.value}",
            )

    update_data = payload.model_dump(exclude_unset=True)
    if update_data:
        # Guard the write on the status we validated the transition against:
        # if a concurrent request (another PATCH, or /complete) already
        # changed the status, this matches zero rows instead of silently
        # clobbering that concurrent change.
        result = await session.execute(
            update(JobRun)
            .where(JobRun.id == job_run_id, JobRun.status == expected_status)
            .values(**update_data)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=409,
                detail="Job run was concurrently modified; retry with the latest state",
            )
        await session.refresh(run)

    await session.commit()

    run_read = JobRunRead.model_validate(run)
    await manager.broadcast(job_run_id, run_read.model_dump(mode="json"))

    return run


@router.post(
    "/{job_run_id}/complete",
    response_model=JobRunRead,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def complete_job_run(
    job_run_id: int, payload: JobRunCompleteRequest, session: AsyncSession = Depends(get_db)
) -> JobRun:
    run = await get_or_404(session, JobRun, job_run_id)
    expected_status = run.status

    if expected_status in JOB_RUN_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Job run has already been completed")

    finished_at = payload.finished_at or datetime.now(UTC)
    duration_seconds = (
        int((_as_naive_utc(finished_at) - _as_naive_utc(run.started_at)).total_seconds())
        if run.started_at is not None
        else None
    )

    update_data = {
        "status": payload.status,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        **payload.model_dump(exclude_unset=True, exclude={"status", "finished_at"}),
    }

    # Same CAS guard as update_job_run: a retried/duplicate completion
    # request (e.g. after a lost response) must not be able to overwrite an
    # already-completed run's terminal state.
    result = await session.execute(
        update(JobRun)
        .where(JobRun.id == job_run_id, JobRun.status == expected_status)
        .values(**update_data)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Job run has already been completed")
    await session.refresh(run)

    job = await get_or_404(session, BackupJob, run.backup_job_id)
    job.last_run_at = finished_at

    if payload.status == JobRunStatus.FAILED:
        # Dedup by backup_job_id, NOT job_run_id -- otherwise every new run
        # would create its own, never-deduplicated JOB_FAILED alert.
        await raise_alert_if_absent(
            session,
            alert_type=AlertType.JOB_FAILED,
            severity=AlertSeverity.CRITICAL,
            entity_type="backup_job",
            entity_column=Alert.backup_job_id,
            entity_id=run.backup_job_id,
            title=f"Backup job '{job.name}' failed",
            message=f"JobRun {run.id} for backup job '{job.name}' (id={job.id}) failed.",
        )
    elif payload.status in (JobRunStatus.SUCCESS, JobRunStatus.WARNING):
        await resolve_active_alert(
            session,
            alert_type=AlertType.JOB_FAILED,
            entity_type="backup_job",
            entity_column=Alert.backup_job_id,
            entity_id=run.backup_job_id,
        )
    # CANCELLED: no alert changes.

    await session.commit()
    await session.refresh(run)

    run_read = JobRunRead.model_validate(run)
    payload_json = run_read.model_dump(mode="json")
    await manager.broadcast(job_run_id, payload_json)
    await manager.close_all(job_run_id, code=1000, reason="job run finished")

    return run
