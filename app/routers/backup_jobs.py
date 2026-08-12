import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_admin_or_agent_key, require_role
from app.core.db import async_session_maker, get_db
from app.models.alert import Alert
from app.models.backup_job import BackupJob
from app.models.disk import Disk
from app.models.enums import (
    AlertSeverity,
    AlertType,
    BackupType,
    JobRunStatus,
    TriggerMode,
    UserRole,
    VerificationRunStatus,
    VerificationType,
)
from app.models.job_run import JobRun
from app.models.server import Server
from app.models.sql_instance import SqlInstance
from app.models.user import User
from app.models.verification_run import VerificationRun
from app.routers._alerts import raise_alert_if_absent, resolve_active_alert
from app.routers._deps import get_or_404
from app.schemas.alert import AlertRead
from app.schemas.backup_job import BackupJobCreate, BackupJobRead, BackupJobUpdate
from app.schemas.common import PaginatedResponse
from app.schemas.verification_run import VerificationRunRead
from app.schemas.watch_event import WatchEventRequest, WatchEventResponse
from app.workers.backup_verification import (
    _track_background_task,
    create_pending_verification_run,
    execute_verification_run,
)

router = APIRouter(tags=["backup-jobs"])


@router.post(
    "",
    response_model=BackupJobRead,
    status_code=201,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def create_backup_job(payload: BackupJobCreate, session: AsyncSession = Depends(get_db)) -> BackupJob:
    server = await get_or_404(session, Server, payload.server_id)
    if server.is_deleted:
        raise HTTPException(status_code=409, detail="Cannot attach a backup job to a deleted server")

    disk = await get_or_404(session, Disk, payload.disk_id)
    if disk.server_id != payload.server_id:
        raise HTTPException(
            status_code=422, detail="disk_id does not belong to server_id"
        )

    if payload.sql_instance_id is not None:
        sql_instance = await get_or_404(session, SqlInstance, payload.sql_instance_id)
        if sql_instance.is_deleted:
            raise HTTPException(
                status_code=409, detail="Cannot attach a backup job to a deleted sql instance"
            )

    job = BackupJob(**payload.model_dump())
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@router.get(
    "", response_model=PaginatedResponse[BackupJobRead], dependencies=[Depends(get_current_user)]
)
async def list_backup_jobs(
    server_id: int | None = None,
    disk_id: int | None = None,
    sql_instance_id: int | None = None,
    is_enabled: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[BackupJobRead]:
    filters = []
    if server_id is not None:
        filters.append(BackupJob.server_id == server_id)
    if disk_id is not None:
        filters.append(BackupJob.disk_id == disk_id)
    if sql_instance_id is not None:
        filters.append(BackupJob.sql_instance_id == sql_instance_id)
    if is_enabled is not None:
        filters.append(BackupJob.is_enabled == is_enabled)

    total_stmt = select(func.count()).select_from(BackupJob)
    items_stmt = select(BackupJob)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(BackupJob.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[BackupJobRead](
        items=[BackupJobRead.model_validate(j) for j in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{backup_job_id}", response_model=BackupJobRead, dependencies=[Depends(get_current_user)]
)
async def get_backup_job(backup_job_id: int, session: AsyncSession = Depends(get_db)) -> BackupJob:
    return await get_or_404(session, BackupJob, backup_job_id)


@router.patch(
    "/{backup_job_id}",
    response_model=BackupJobRead,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_backup_job(
    backup_job_id: int, payload: BackupJobUpdate, session: AsyncSession = Depends(get_db)
) -> BackupJob:
    job = await get_or_404(session, BackupJob, backup_job_id)

    payload_set = payload.model_dump(exclude_unset=True)

    # Mirrors delete_backup_job's active-run check exactly. Copy windows
    # can now leave a run legitimately PENDING for hours waiting for its
    # window to open, and changing trigger_mode out from under an
    # in-flight run would leave that run referring to a mode it was never
    # created under -- block it instead.
    if "trigger_mode" in payload_set and payload_set["trigger_mode"] != job.trigger_mode:
        active_runs_stmt = select(func.count()).select_from(JobRun).where(
            JobRun.backup_job_id == backup_job_id,
            JobRun.status.in_((JobRunStatus.PENDING, JobRunStatus.RUNNING)),
        )
        active_runs = (await session.execute(active_runs_stmt)).scalar_one()
        if active_runs > 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot change trigger_mode while a run is PENDING/RUNNING",
            )

    for field, value in payload_set.items():
        setattr(job, field, value)

    # BackupJobCreate enforces this invariant at creation time, but
    # BackupJobUpdate does not re-derive it automatically -- re-check here
    # after merging the patch, before commit.
    if job.sql_instance_id is not None and job.verification_method is None:
        raise HTTPException(
            status_code=409,
            detail="verification_method is required when sql_instance_id is set",
        )
    if job.sql_instance_id is not None and not job.database_name:
        raise HTTPException(
            status_code=409,
            detail="database_name is required when sql_instance_id is set -- "
            "needed to query msdb.dbo.backupset for verification",
        )

    # Same trigger-mode-conditional-required-fields / WATCH+backup_type
    # checks BackupJobCreate's validators enforce at creation time, re-run
    # here against the MERGED post-patch state (BackupJobUpdate alone
    # can't see it -- see its docstring).
    if job.trigger_mode == TriggerMode.SCHEDULE:
        if not job.schedule_cron:
            raise HTTPException(
                status_code=409, detail="schedule_cron is required when trigger_mode is SCHEDULE"
            )
        if not job.source_path:
            raise HTTPException(
                status_code=409, detail="source_path is required when trigger_mode is SCHEDULE"
            )
        if job.watch_directory is not None:
            raise HTTPException(
                status_code=409, detail="watch_directory must not be set when trigger_mode is SCHEDULE"
            )
    elif job.trigger_mode == TriggerMode.WATCH:
        if not job.watch_directory:
            raise HTTPException(
                status_code=409, detail="watch_directory is required when trigger_mode is WATCH"
            )
        if job.schedule_cron is not None:
            raise HTTPException(
                status_code=409,
                detail="schedule_cron must not be set when trigger_mode is WATCH (no fixed schedule)",
            )
        if job.source_path is not None:
            raise HTTPException(
                status_code=409,
                detail="source_path must not be set when trigger_mode is WATCH -- use watch_directory",
            )

    if job.trigger_mode == TriggerMode.WATCH and job.backup_type in (
        BackupType.TRANSACTION_LOG,
        BackupType.CUSTOM,
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "WATCH trigger mode is not supported for TRANSACTION_LOG or CUSTOM backup "
                "types in this iteration -- sequential/cumulative or undefined-semantics "
                "backups cannot safely use latest-file-wins transfer semantics; use SCHEDULE "
                "mode instead"
            ),
        )

    await session.commit()
    await session.refresh(job)
    return job


@router.delete(
    "/{backup_job_id}",
    status_code=204,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def delete_backup_job(backup_job_id: int, session: AsyncSession = Depends(get_db)) -> None:
    job = await get_or_404(session, BackupJob, backup_job_id)

    active_runs_stmt = select(func.count()).select_from(JobRun).where(
        JobRun.backup_job_id == backup_job_id,
        JobRun.status.in_((JobRunStatus.PENDING, JobRunStatus.RUNNING)),
    )
    active_runs = (await session.execute(active_runs_stmt)).scalar_one()
    if active_runs > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete backup job: it has PENDING/RUNNING JobRun(s)",
        )

    active_verification_runs_stmt = select(func.count()).select_from(VerificationRun).where(
        VerificationRun.backup_job_id == backup_job_id,
        VerificationRun.status.in_((VerificationRunStatus.PENDING, VerificationRunStatus.RUNNING)),
    )
    active_verification_runs = (await session.execute(active_verification_runs_stmt)).scalar_one()
    if active_verification_runs > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete backup job: it has PENDING/RUNNING VerificationRun(s)",
        )

    # No further manual pre-check: if any BackupRecord of this job ever had
    # a RestoreOperation (RESTRICT, not limited to active ones), the
    # cascade delete of backup_records will fail and the whole transaction
    # rolls back -> 409 via the global IntegrityError handler.
    await session.delete(job)
    await session.commit()
    return None


@router.post(
    "/{backup_job_id}/verify",
    response_model=VerificationRunRead,
    status_code=202,
)
async def verify_backup_job(
    backup_job_id: int,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> VerificationRun:
    job = await get_or_404(session, BackupJob, backup_job_id)
    if job.sql_instance_id is None:
        raise HTTPException(
            status_code=409, detail="verification is not enabled for this job"
        )

    # Deliberately no local try/except around the INSERT: if there is
    # already an active (PENDING/RUNNING) verification run for this
    # backup_job_id, the partial unique index raises IntegrityError on
    # commit, which the global handler converts to 409.
    run = await create_pending_verification_run(
        session, backup_job_id, triggered_by=current_user.username
    )
    await session.commit()
    await session.refresh(run)

    task = asyncio.create_task(execute_verification_run(async_session_maker, run.id))
    _track_background_task(task)

    return run


@router.get(
    "/{backup_job_id}/verification-runs",
    response_model=PaginatedResponse[VerificationRunRead],
    # ADMIN-only, not get_current_user: VerificationRun.error_message can
    # contain a SQL Server driver's verbatim login-failure text (e.g. "Login
    # failed for user 'svc_backup_verify'"), which echoes the SqlInstance's
    # SQL login name -- SqlInstanceRead already treats that as admin-only
    # information (only a `credentials_set` boolean is exposed to non-admins
    # on the SqlInstance endpoints themselves); this endpoint must not open
    # a side channel to the same information for OPERATOR-role users.
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def list_backup_job_verification_runs(
    backup_job_id: int,
    status: VerificationRunStatus | None = None,
    verification_type: VerificationType | None = None,
    backup_record_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[VerificationRunRead]:
    await get_or_404(session, BackupJob, backup_job_id)

    filters = [VerificationRun.backup_job_id == backup_job_id]
    if status is not None:
        filters.append(VerificationRun.status == status)
    if verification_type is not None:
        filters.append(VerificationRun.verification_type == verification_type)
    if backup_record_id is not None:
        filters.append(VerificationRun.backup_record_id == backup_record_id)

    total_stmt = select(func.count()).select_from(VerificationRun).where(*filters)
    items_stmt = (
        select(VerificationRun)
        .where(*filters)
        .order_by(VerificationRun.id.desc())
        .limit(limit)
        .offset(offset)
    )

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[VerificationRunRead](
        items=[VerificationRunRead.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{backup_job_id}/verification-runs/{run_id}",
    response_model=VerificationRunRead,
    # ADMIN-only -- same reasoning as list_backup_job_verification_runs above.
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def get_backup_job_verification_run(
    backup_job_id: int, run_id: int, session: AsyncSession = Depends(get_db)
) -> VerificationRun:
    run = await get_or_404(session, VerificationRun, run_id)
    if run.backup_job_id != backup_job_id:
        raise HTTPException(status_code=404, detail=f"VerificationRun {run_id} not found")
    return run


@router.post(
    "/{backup_job_id}/watch-events",
    response_model=WatchEventResponse,
    status_code=202,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def report_watch_event(
    backup_job_id: int, payload: WatchEventRequest, session: AsyncSession = Depends(get_db)
) -> WatchEventResponse:
    job = await get_or_404(session, BackupJob, backup_job_id)
    if job.trigger_mode != TriggerMode.WATCH:
        raise HTTPException(status_code=409, detail="watch-events can only be reported for WATCH-mode jobs")

    alert_raised = None
    alert_resolved = None
    if payload.active:
        alert_raised = await raise_alert_if_absent(
            session,
            alert_type=AlertType.WATCH_FILE_LOCK_TIMEOUT,
            severity=AlertSeverity.WARNING,
            entity_type="backup_job",
            entity_column=Alert.backup_job_id,
            entity_id=backup_job_id,
            title=f"Backup job '{job.name}' file locked longer than expected",
            message=(
                f"File '{payload.file_path}' has been locked longer than expected."
                + (f" {payload.detail}" if payload.detail else "")
            ),
        )
    else:
        alert_resolved = await resolve_active_alert(
            session,
            alert_type=AlertType.WATCH_FILE_LOCK_TIMEOUT,
            entity_type="backup_job",
            entity_column=Alert.backup_job_id,
            entity_id=backup_job_id,
        )

    await session.commit()
    return WatchEventResponse(
        alert_raised=AlertRead.model_validate(alert_raised) if alert_raised else None,
        alert_resolved=AlertRead.model_validate(alert_resolved) if alert_resolved else None,
    )
