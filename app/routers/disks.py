from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_role
from app.core.db import get_db
from app.models.disk import Disk
from app.models.enums import UserRole
from app.models.server import Server
from app.routers._deps import get_or_404
from app.schemas.common import PaginatedResponse
from app.schemas.disk import DiskCreate, DiskRead, DiskUpdate

router = APIRouter(tags=["disks"])


@router.post(
    "",
    response_model=DiskRead,
    status_code=201,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def create_disk(payload: DiskCreate, session: AsyncSession = Depends(get_db)) -> Disk:
    server = await get_or_404(session, Server, payload.server_id)
    if server.is_deleted:
        raise HTTPException(status_code=409, detail="Cannot attach a disk to a deleted server")

    disk = Disk(**payload.model_dump())
    session.add(disk)
    await session.commit()
    await session.refresh(disk)
    return disk


@router.get("", response_model=PaginatedResponse[DiskRead], dependencies=[Depends(get_current_user)])
async def list_disks(
    server_id: int | None = None,
    is_active: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DiskRead]:
    filters = []
    if server_id is not None:
        filters.append(Disk.server_id == server_id)
    if is_active is not None:
        filters.append(Disk.is_active == is_active)

    total_stmt = select(func.count()).select_from(Disk)
    items_stmt = select(Disk)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(Disk.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[DiskRead](
        items=[DiskRead.model_validate(d) for d in items], total=total, limit=limit, offset=offset
    )


@router.get("/{disk_id}", response_model=DiskRead, dependencies=[Depends(get_current_user)])
async def get_disk(disk_id: int, session: AsyncSession = Depends(get_db)) -> Disk:
    return await get_or_404(session, Disk, disk_id)


@router.patch(
    "/{disk_id}",
    response_model=DiskRead,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_disk(disk_id: int, payload: DiskUpdate, session: AsyncSession = Depends(get_db)) -> Disk:
    disk = await get_or_404(session, Disk, disk_id)

    # DiskUpdate deliberately has no total_bytes/free_bytes fields --
    # usage is only ever updated via the agent heartbeat.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(disk, field, value)

    await session.commit()
    await session.refresh(disk)
    return disk


@router.delete(
    "/{disk_id}",
    status_code=204,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def delete_disk(disk_id: int, session: AsyncSession = Depends(get_db)) -> None:
    disk = await get_or_404(session, Disk, disk_id)
    # No manual pre-check: backup_jobs.disk_id is ON DELETE RESTRICT
    # unconditionally, so a referenced disk raises IntegrityError here,
    # which the global handler turns into 409 automatically.
    await session.delete(disk)
    await session.commit()
    return None
