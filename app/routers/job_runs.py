from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_admin_or_agent_key, require_role
from app.core.db import get_db
from app.core.timeutils import as_naive_utc as _as_naive_utc
from app.core.ws_manager import manager
from app.models.alert import Alert
from app.models.backup_job import BackupJob
from app.models.enums import (
    JOB_RUN_TERMINAL_STATUSES,
    AlertSeverity,
    AlertType,
    JobRunStatus,
    TriggerMode,
    UserRole,
)
from app.models.job_run import JobRun
from app.models.user import User
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


async def _auto_acknowledge_cancelled(session: AsyncSession, job_run_id: int) -> None:
    """Best-effort side effect: if this run is CANCELLED and not yet
    acknowledged, mark it acknowledged -- called right before
    update_job_run/complete_job_run raise their "already terminal" 409, on
    the theory that whatever just PATCHed/completed a CANCELLED run has, by
    virtue of that very call, observably reacted to the cancellation. No-op
    (and must never itself raise or block the caller's 409) if the terminal
    status wasn't CANCELLED or was already acknowledged.
    """
    await session.execute(
        update(JobRun)
        .where(
            JobRun.id == job_run_id,
            JobRun.status == JobRunStatus.CANCELLED,
            JobRun.cancel_acknowledged_at.is_(None),
        )
        .values(cancel_acknowledged_at=datetime.now(UTC))
    )
    await session.commit()


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
    if payload.triggered_by == "manual" and job.trigger_mode == TriggerMode.WATCH:
        # WATCH jobs have no source_path -- a manual run could never
        # actually execute anyway.
        raise HTTPException(
            status_code=409,
            detail="Manual triggering is not supported for WATCH-mode jobs",
        )

    # Deliberately no local try/except around the INSERT: if there is
    # already an active (PENDING/RUNNING) run for this backup_job_id, the
    # partial unique index raises IntegrityError on commit, which the
    # global handler converts to 409.
    #
    # dispatched_at is set immediately for scheduler/watch-triggered runs
    # (the scheduler/watch-detector is itself the dispatch mechanism -- it
    # only creates the run once it's ready to act on it); manual runs stay
    # NULL until a human/agent explicitly claims them via
    # POST /api/job-runs/{id}/claim. See
    # app.workers.alert_worker.check_stuck_pending_dispatch, which watches
    # for dispatched_at staying NULL too long.
    run = JobRun(
        backup_job_id=payload.backup_job_id,
        triggered_by=payload.triggered_by,
        dispatched_at=datetime.now(UTC) if payload.triggered_by in ("scheduler", "watch") else None,
    )
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
        await _auto_acknowledge_cancelled(session, job_run_id)
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
        await _auto_acknowledge_cancelled(session, job_run_id)
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


@router.post(
    "/{job_run_id}/claim",
    response_model=JobRunRead,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def claim_job_run(job_run_id: int, session: AsyncSession = Depends(get_db)) -> JobRun:
    """Marks a manually-triggered PENDING run as dispatched (claimed) by an
    agent, without creating anything -- POST /api/job-runs remains the sole
    creation endpoint. Only manual-trigger runs are ever claimable:
    scheduler/watch-triggered runs already have dispatched_at set at INSERT
    time (see create_job_run) and are never claim-eligible.
    """
    run = await get_or_404(session, JobRun, job_run_id)

    # CAS guard, same pattern as update_job_run/complete_job_run: the
    # UPDATE only succeeds if the run is still exactly in the claimable
    # state (PENDING, never dispatched, manual trigger) at write time.
    result = await session.execute(
        update(JobRun)
        .where(
            JobRun.id == job_run_id,
            JobRun.status == JobRunStatus.PENDING,
            JobRun.dispatched_at.is_(None),
            JobRun.triggered_by == "manual",
        )
        .values(dispatched_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail="Job run is not claimable (already claimed, already started, or not a manual trigger)",
        )
    await session.commit()
    await session.refresh(run)

    return run


@router.post(
    "/{job_run_id}/cancel",
    response_model=JobRunRead,
)
async def cancel_job_run(
    job_run_id: int,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> JobRun:
    """Admin-only, human-initiated cancellation of a PENDING/RUNNING run.
    Never touches Alert state (mirrors complete_job_run's own CANCELLED
    handling) -- JOB_STUCK_PENDING is exclusively the stuck-pending
    detector's responsibility (app.workers.alert_worker.
    check_stuck_pending_dispatch), not this endpoint's.
    """
    run = await get_or_404(session, JobRun, job_run_id)

    now = datetime.now(UTC)
    duration_seconds = (
        int((_as_naive_utc(now) - _as_naive_utc(run.started_at)).total_seconds())
        if run.started_at is not None
        else None
    )

    # CAS guard, same pattern as update_job_run/complete_job_run: the
    # UPDATE only succeeds if the run is still in an active status (PENDING
    # or RUNNING) at write time, regardless of what it was when read above.
    result = await session.execute(
        update(JobRun)
        .where(JobRun.id == job_run_id, JobRun.status.in_((JobRunStatus.PENDING, JobRunStatus.RUNNING)))
        .values(
            status=JobRunStatus.CANCELLED,
            finished_at=now,
            duration_seconds=duration_seconds,
            cancel_requested_at=now,
            cancel_requested_by=current_user.username,
            error_message=f"Cancelled by operator {current_user.username}",
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=409, detail="Job run is already terminal and cannot be cancelled"
        )
    await session.refresh(run)

    job = await get_or_404(session, BackupJob, run.backup_job_id)
    job.last_run_at = now
    # CANCELLED: no alert changes -- see complete_job_run's identical
    # "CANCELLED: no alert changes" comment.

    await session.commit()
    await session.refresh(run)

    run_read = JobRunRead.model_validate(run)
    await manager.broadcast(job_run_id, run_read.model_dump(mode="json"))
    await manager.close_all(job_run_id, code=1000, reason="job run cancelled")

    return run
