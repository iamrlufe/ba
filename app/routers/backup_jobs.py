import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_role
from app.core.db import async_session_maker, get_db
from app.models.backup_job import BackupJob
from app.models.disk import Disk
from app.models.enums import JobRunStatus, UserRole, VerificationRunStatus, VerificationType
from app.models.job_run import JobRun
from app.models.server import Server
from app.models.sql_instance import SqlInstance
from app.models.user import User
from app.models.verification_run import VerificationRun
from app.routers._deps import get_or_404
from app.schemas.backup_job import BackupJobCreate, BackupJobRead, BackupJobUpdate
from app.schemas.common import PaginatedResponse
from app.schemas.verification_run import VerificationRunRead
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

    for field, value in payload.model_dump(exclude_unset=True).items():
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
