from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_role
from app.core.db import get_db
from app.core.security import encrypt_secret
from app.models.backup_job import BackupJob
from app.models.enums import ServerStatus, UserRole
from app.models.server import Server
from app.routers._deps import get_or_404
from app.schemas.common import PaginatedResponse
from app.schemas.server import ServerCreate, ServerRead, ServerUpdate

router = APIRouter(tags=["servers"])


@router.post(
    "",
    response_model=ServerRead,
    status_code=201,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def create_server(payload: ServerCreate, session: AsyncSession = Depends(get_db)) -> Server:
    data = payload.model_dump(exclude={"username", "password", "ssh_private_key"})
    server = Server(**data)
    if payload.username:
        server.username_encrypted = encrypt_secret(payload.username)
    if payload.password:
        server.password_encrypted = encrypt_secret(payload.password)
    if payload.ssh_private_key:
        server.ssh_private_key_encrypted = encrypt_secret(payload.ssh_private_key)

    session.add(server)
    await session.commit()
    await session.refresh(server)
    return server


@router.get("", response_model=PaginatedResponse[ServerRead], dependencies=[Depends(get_current_user)])
async def list_servers(
    status: ServerStatus | None = None,
    include_deleted: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ServerRead]:
    filters = []
    if status is not None:
        filters.append(Server.status == status)
    if include_deleted is not True:
        filters.append(Server.is_deleted.is_(False))

    total_stmt = select(func.count()).select_from(Server)
    items_stmt = select(Server)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(Server.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[ServerRead](
        items=[ServerRead.model_validate(s) for s in items], total=total, limit=limit, offset=offset
    )


@router.get("/{server_id}", response_model=ServerRead, dependencies=[Depends(get_current_user)])
async def get_server(server_id: int, session: AsyncSession = Depends(get_db)) -> Server:
    return await get_or_404(session, Server, server_id)


@router.patch(
    "/{server_id}",
    response_model=ServerRead,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_server(
    server_id: int, payload: ServerUpdate, session: AsyncSession = Depends(get_db)
) -> Server:
    server = await get_or_404(session, Server, server_id)
    if server.is_deleted:
        raise HTTPException(status_code=409, detail="Cannot modify a deleted server")

    update_data = payload.model_dump(exclude_unset=True)

    # Secret fields: only apply keys present in the payload. Within those,
    # an explicit null means "do not change" (same as absent); only an
    # explicit empty string clears the corresponding *_encrypted column.
    for field, encrypted_attr in (
        ("username", "username_encrypted"),
        ("password", "password_encrypted"),
        ("ssh_private_key", "ssh_private_key_encrypted"),
    ):
        if field not in update_data:
            continue
        value = update_data.pop(field)
        if value is None:
            continue
        if value == "":
            setattr(server, encrypted_attr, None)
        else:
            setattr(server, encrypted_attr, encrypt_secret(value))

    for field, value in update_data.items():
        setattr(server, field, value)

    await session.commit()
    await session.refresh(server)
    return server


@router.delete(
    "/{server_id}",
    status_code=204,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def delete_server(server_id: int, session: AsyncSession = Depends(get_db)) -> None:
    server = await get_or_404(session, Server, server_id)
    if server.is_deleted:
        return None

    active_jobs_stmt = select(func.count()).select_from(BackupJob).where(
        BackupJob.server_id == server_id, BackupJob.is_enabled.is_(True)
    )
    active_jobs = (await session.execute(active_jobs_stmt)).scalar_one()
    if active_jobs > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete server: it has enabled BackupJob(s) referencing it",
        )

    server.is_deleted = True
    await session.commit()
    return None
