from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin_or_agent_key
from app.core.db import get_db
from app.models.backup_job import BackupJob
from app.models.backup_record import BackupRecord
from app.models.job_run import JobRun
from app.routers._deps import get_or_404
from app.schemas.backup_record import BackupRecordCreate, BackupRecordRead

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
