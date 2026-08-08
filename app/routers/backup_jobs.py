from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_role
from app.core.db import get_db
from app.models.backup_job import BackupJob
from app.models.disk import Disk
from app.models.enums import JobRunStatus, UserRole
from app.models.job_run import JobRun
from app.models.server import Server
from app.models.sql_instance import SqlInstance
from app.routers._deps import get_or_404
from app.schemas.backup_job import BackupJobCreate, BackupJobRead, BackupJobUpdate
from app.schemas.common import PaginatedResponse

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

    # No further manual pre-check: if any BackupRecord of this job ever had
    # a RestoreOperation (RESTRICT, not limited to active ones), the
    # cascade delete of backup_records will fail and the whole transaction
    # rolls back -> 409 via the global IntegrityError handler.
    await session.delete(job)
    await session.commit()
    return None
