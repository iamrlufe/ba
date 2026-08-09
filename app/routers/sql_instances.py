from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_role
from app.core.db import get_db
from app.core.security import encrypt_secret
from app.models.backup_job import BackupJob
from app.models.enums import RestoreStatus, ServerStatus, UserRole, VerificationRunStatus
from app.models.restore_operation import RestoreOperation
from app.models.server import Server
from app.models.sql_instance import SqlInstance
from app.models.verification_run import VerificationRun
from app.routers._deps import get_or_404
from app.schemas.common import PaginatedResponse
from app.schemas.sql_instance import SqlInstanceCreate, SqlInstanceRead, SqlInstanceUpdate

router = APIRouter(tags=["sql-instances"])


async def _check_server_reference(session: AsyncSession, server_id: int) -> None:
    """Shared existence/soft-delete guard for `server_id` references.

    Not explicitly spelled out for SqlInstanceCreate in the spec (only for
    Disk/BackupJob POST and SqlInstance PATCH), but applied here on POST as
    well for consistency: a missing target should be a clear 404 rather
    than an opaque 409 from the FK constraint, and a soft-deleted target
    should never be attachable to a new resource.
    """
    server = await get_or_404(session, Server, server_id)
    if server.is_deleted:
        raise HTTPException(status_code=409, detail="Cannot reference a deleted server")


@router.post(
    "",
    response_model=SqlInstanceRead,
    status_code=201,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def create_sql_instance(
    payload: SqlInstanceCreate, session: AsyncSession = Depends(get_db)
) -> SqlInstance:
    if payload.server_id is not None:
        await _check_server_reference(session, payload.server_id)

    data = payload.model_dump(exclude={"username", "password"})
    instance = SqlInstance(**data)
    if not payload.use_windows_auth:
        if payload.username:
            instance.username_encrypted = encrypt_secret(payload.username)
        if payload.password:
            instance.password_encrypted = encrypt_secret(payload.password)

    session.add(instance)
    await session.commit()
    await session.refresh(instance)
    return instance


@router.get(
    "", response_model=PaginatedResponse[SqlInstanceRead], dependencies=[Depends(get_current_user)]
)
async def list_sql_instances(
    status: ServerStatus | None = None,
    server_id: int | None = None,
    include_deleted: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[SqlInstanceRead]:
    filters = []
    if status is not None:
        filters.append(SqlInstance.status == status)
    if server_id is not None:
        filters.append(SqlInstance.server_id == server_id)
    if include_deleted is not True:
        filters.append(SqlInstance.is_deleted.is_(False))

    total_stmt = select(func.count()).select_from(SqlInstance)
    items_stmt = select(SqlInstance)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(SqlInstance.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[SqlInstanceRead](
        items=[SqlInstanceRead.model_validate(i) for i in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{sql_instance_id}", response_model=SqlInstanceRead, dependencies=[Depends(get_current_user)]
)
async def get_sql_instance(sql_instance_id: int, session: AsyncSession = Depends(get_db)) -> SqlInstance:
    return await get_or_404(session, SqlInstance, sql_instance_id)


@router.patch(
    "/{sql_instance_id}",
    response_model=SqlInstanceRead,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_sql_instance(
    sql_instance_id: int, payload: SqlInstanceUpdate, session: AsyncSession = Depends(get_db)
) -> SqlInstance:
    instance = await get_or_404(session, SqlInstance, sql_instance_id)
    if instance.is_deleted:
        raise HTTPException(status_code=409, detail="Cannot modify a deleted sql instance")

    update_data = payload.model_dump(exclude_unset=True)

    if "server_id" in update_data and update_data["server_id"] is not None:
        await _check_server_reference(session, update_data["server_id"])

    for field, encrypted_attr in (
        ("username", "username_encrypted"),
        ("password", "password_encrypted"),
    ):
        if field not in update_data:
            continue
        value = update_data.pop(field)
        if value is None:
            continue
        if value == "":
            setattr(instance, encrypted_attr, None)
        else:
            setattr(instance, encrypted_attr, encrypt_secret(value))

    for field, value in update_data.items():
        setattr(instance, field, value)

    await session.commit()
    await session.refresh(instance)
    return instance


@router.delete(
    "/{sql_instance_id}",
    status_code=204,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def delete_sql_instance(sql_instance_id: int, session: AsyncSession = Depends(get_db)) -> None:
    instance = await get_or_404(session, SqlInstance, sql_instance_id)
    if instance.is_deleted:
        return None

    active_jobs_stmt = select(func.count()).select_from(BackupJob).where(
        BackupJob.sql_instance_id == sql_instance_id, BackupJob.is_enabled.is_(True)
    )
    active_jobs = (await session.execute(active_jobs_stmt)).scalar_one()
    if active_jobs > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete sql instance: it has enabled BackupJob(s) referencing it",
        )

    active_restores_stmt = select(func.count()).select_from(RestoreOperation).where(
        RestoreOperation.sql_instance_id == sql_instance_id,
        RestoreOperation.status.in_((RestoreStatus.PENDING, RestoreStatus.RUNNING)),
    )
    active_restores = (await session.execute(active_restores_stmt)).scalar_one()
    if active_restores > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete sql instance: it has active RestoreOperation(s) referencing it",
        )

    active_verification_runs_stmt = select(func.count()).select_from(VerificationRun).where(
        VerificationRun.sql_instance_id == sql_instance_id,
        VerificationRun.status.in_((VerificationRunStatus.PENDING, VerificationRunStatus.RUNNING)),
    )
    active_verification_runs = (await session.execute(active_verification_runs_stmt)).scalar_one()
    if active_verification_runs > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete sql instance: it has active VerificationRun(s) referencing it",
        )

    instance.is_deleted = True
    await session.commit()
    return None
