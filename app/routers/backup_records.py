from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_admin_or_agent_key
from app.core.db import get_db
from app.models.backup_job import BackupJob
from app.models.backup_record import BackupRecord
from app.models.job_run import JobRun
from app.models.verification_run import VerificationRun
from app.routers._deps import get_or_404
from app.schemas.backup_record import BackupRecordCreate, BackupRecordRead
from app.schemas.common import PaginatedResponse
from app.schemas.copy_verification import CopyVerificationReportRequest
from app.schemas.verification_run import VerificationRunRead
from app.workers.copy_verification import record_copy_verification_result

router = APIRouter(tags=["backup-records"])


@router.post(
    "",
    response_model=BackupRecordRead,
    status_code=200,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def upsert_backup_record(
    payload: BackupRecordCreate, session: AsyncSession = Depends(get_db)
) -> BackupRecord:
    await get_or_404(session, BackupJob, payload.backup_job_id)

    if payload.job_run_id is not None:
        run = await get_or_404(session, JobRun, payload.job_run_id)
        if run.backup_job_id != payload.backup_job_id:
            raise HTTPException(
                status_code=422,
                detail="job_run_id does not belong to the given backup_job_id",
            )

    # Atomic upsert (avoids a TOCTOU race between a SELECT and a subsequent
    # INSERT/UPDATE): a BackupRecord is uniquely identified by
    # (backup_job_id, remote_path, file_name) -- see
    # uq_backup_records_backup_job_id_remote_path_file_name.
    # `detected_at` is deliberately left out of `set_` -- it must remain the
    # first-observed timestamp and never be overwritten by later syncs.
    insert_stmt = sqlite_insert(BackupRecord).values(
        backup_job_id=payload.backup_job_id,
        job_run_id=payload.job_run_id,
        file_name=payload.file_name,
        remote_path=payload.remote_path,
        file_size_bytes=payload.file_size_bytes,
        checksum=payload.checksum,
        checksum_algorithm=payload.checksum_algorithm,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["backup_job_id", "remote_path", "file_name"],
        set_={
            "job_run_id": insert_stmt.excluded.job_run_id,
            "file_size_bytes": insert_stmt.excluded.file_size_bytes,
            "checksum": insert_stmt.excluded.checksum,
            "checksum_algorithm": insert_stmt.excluded.checksum_algorithm,
            "updated_at": func.now(),
        },
    ).returning(BackupRecord)

    result = await session.execute(upsert_stmt)
    record = result.scalar_one()
    await session.commit()
    await session.refresh(record)
    return record


@router.get(
    "", response_model=PaginatedResponse[BackupRecordRead], dependencies=[Depends(require_admin_or_agent_key)]
)
async def list_backup_records(
    backup_job_id: int | None = None,
    remote_path: str | None = Query(None, max_length=500),
    file_name: str | None = Query(None, max_length=255),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[BackupRecordRead]:
    if (remote_path is None) != (file_name is None):
        raise HTTPException(
            status_code=422, detail="remote_path and file_name must be provided together"
        )

    filters = []
    if backup_job_id is not None:
        filters.append(BackupRecord.backup_job_id == backup_job_id)
    if remote_path is not None and file_name is not None:
        filters.append(BackupRecord.remote_path == remote_path)
        filters.append(BackupRecord.file_name == file_name)

    total_stmt = select(func.count()).select_from(BackupRecord)
    items_stmt = select(BackupRecord)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(BackupRecord.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[BackupRecordRead](
        items=[BackupRecordRead.model_validate(r) for r in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{backup_record_id}", response_model=BackupRecordRead, dependencies=[Depends(get_current_user)]
)
async def get_backup_record(backup_record_id: int, session: AsyncSession = Depends(get_db)) -> BackupRecord:
    return await get_or_404(session, BackupRecord, backup_record_id)


@router.post(
    "/{backup_record_id}/report-copy-verification",
    response_model=VerificationRunRead,
    status_code=201,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def report_copy_verification(
    backup_record_id: int,
    payload: CopyVerificationReportRequest,
    session: AsyncSession = Depends(get_db),
) -> VerificationRun:
    record = await get_or_404(session, BackupRecord, backup_record_id)

    run = await record_copy_verification_result(session, record, payload)

    await session.commit()
    await session.refresh(run)
    return run
