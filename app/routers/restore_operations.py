from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_role
from app.core.db import get_db
from app.models.alert import Alert
from app.models.backup_record import BackupRecord
from app.models.enums import (
    RESTORE_TERMINAL_STATUSES,
    AlertSeverity,
    AlertType,
    RequestChannel,
    RestoreMode,
    RestoreStatus,
    UserRole,
)
from app.models.restore_operation import RestoreOperation
from app.models.sql_instance import SqlInstance
from app.models.user import User
from app.routers._alerts import raise_alert_if_absent
from app.routers._deps import get_or_404
from app.schemas.common import PaginatedResponse
from app.schemas.restore_operation import (
    RestoreOperationCreate,
    RestoreOperationLogRead,
    RestoreOperationRead,
    RestoreOperationUpdate,
    is_valid_transition,
)

router = APIRouter(tags=["restore-operations"])


@router.post("", response_model=RestoreOperationRead, status_code=201)
async def create_restore_operation(
    payload: RestoreOperationCreate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RestoreOperation:
    if current_user.role == UserRole.OPERATOR and payload.mode != RestoreMode.MISSING:
        raise HTTPException(
            status_code=403,
            detail="Operators may only create restore operations with mode=MISSING",
        )
    # role is ADMIN, or role is OPERATOR with mode==MISSING -- both permitted

    await get_or_404(session, BackupRecord, payload.backup_record_id)

    sql_instance = await get_or_404(session, SqlInstance, payload.sql_instance_id)
    if sql_instance.is_deleted:
        raise HTTPException(status_code=409, detail="Cannot restore into a deleted sql instance")

    requested_by = current_user.username
    requested_by_channel = RequestChannel.WEB

    # Deliberately no local try/except: a conflicting active
    # (PENDING/RUNNING) restore for the same (sql_instance_id,
    # database_name) raises IntegrityError on commit, converted to 409 by
    # the global handler.
    restore = RestoreOperation(
        backup_record_id=payload.backup_record_id,
        sql_instance_id=payload.sql_instance_id,
        server_id=sql_instance.server_id,
        database_name=payload.database_name,
        mode=payload.mode,
        requested_by=requested_by,
        requested_by_channel=requested_by_channel,
    )
    session.add(restore)
    await session.commit()
    await session.refresh(restore)
    return restore


@router.get(
    "", response_model=PaginatedResponse[RestoreOperationRead], dependencies=[Depends(get_current_user)]
)
async def list_restore_operations(
    status: RestoreStatus | None = None,
    sql_instance_id: int | None = None,
    backup_record_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[RestoreOperationRead]:
    filters = []
    if status is not None:
        filters.append(RestoreOperation.status == status)
    if sql_instance_id is not None:
        filters.append(RestoreOperation.sql_instance_id == sql_instance_id)
    if backup_record_id is not None:
        filters.append(RestoreOperation.backup_record_id == backup_record_id)

    total_stmt = select(func.count()).select_from(RestoreOperation)
    items_stmt = select(RestoreOperation)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(RestoreOperation.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[RestoreOperationRead](
        items=[RestoreOperationRead.model_validate(r) for r in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{restore_operation_id}",
    response_model=RestoreOperationRead,
    dependencies=[Depends(get_current_user)],
)
async def get_restore_operation(
    restore_operation_id: int, session: AsyncSession = Depends(get_db)
) -> RestoreOperation:
    return await get_or_404(session, RestoreOperation, restore_operation_id)


@router.get(
    "/{restore_operation_id}/log",
    response_model=RestoreOperationLogRead,
    dependencies=[Depends(get_current_user)],
)
async def get_restore_operation_log(
    restore_operation_id: int, session: AsyncSession = Depends(get_db)
) -> RestoreOperation:
    return await get_or_404(session, RestoreOperation, restore_operation_id)


@router.patch(
    "/{restore_operation_id}",
    response_model=RestoreOperationRead,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def update_restore_operation(
    restore_operation_id: int, payload: RestoreOperationUpdate, session: AsyncSession = Depends(get_db)
) -> RestoreOperation:
    restore = await get_or_404(session, RestoreOperation, restore_operation_id)
    expected_status = restore.status

    if expected_status in RESTORE_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot modify a restore operation that has already finished")

    if payload.status is not None:
        # This is what blocks RUNNING -> CANCELLED. No separate "cancel"
        # endpoint is added, precisely so nothing can bypass this check.
        if not is_valid_transition(expected_status, payload.status):
            raise HTTPException(
                status_code=409,
                detail=f"Invalid status transition: {expected_status.value} -> {payload.status.value}",
            )

    update_data = payload.model_dump(exclude_unset=True)

    if payload.status is not None:
        update_data["status"] = payload.status
        if payload.status in (RestoreStatus.DONE, RestoreStatus.FAILED) and "completed_at" not in update_data:
            update_data["completed_at"] = datetime.now(UTC)

    if update_data:
        # CAS guard: reject the write if another request already changed
        # the status out from under us, instead of silently overwriting a
        # concurrent transition (see job_runs.py for the same pattern).
        result = await session.execute(
            update(RestoreOperation)
            .where(RestoreOperation.id == restore_operation_id, RestoreOperation.status == expected_status)
            .values(**update_data)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=409,
                detail="Restore operation was concurrently modified; retry with the latest state",
            )
        await session.refresh(restore)

    if payload.status == RestoreStatus.FAILED:
        # Dedup by restore_operation_id: each restore is one-shot, so no
        # collapsing across runs is needed here (unlike JOB_FAILED, which
        # dedups by backup_job_id).
        await raise_alert_if_absent(
            session,
            alert_type=AlertType.RESTORE_FAILED,
            severity=AlertSeverity.CRITICAL,
            entity_type="restore_operation",
            entity_column=Alert.restore_operation_id,
            entity_id=restore.id,
            title=f"Restore operation {restore.id} failed",
            message=(
                f"RestoreOperation {restore.id} (database '{restore.database_name}') failed."
            ),
        )

    await session.commit()
    await session.refresh(restore)
    return restore
