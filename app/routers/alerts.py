from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_role
from app.core.db import get_db
from app.models.alert import Alert
from app.models.enums import AlertSeverity, AlertStatus, AlertType, UserRole
from app.models.user import User
from app.routers._deps import get_or_404
from app.schemas.alert import AlertAcknowledgeRequest, AlertRead, AlertResolveRequest
from app.schemas.common import PaginatedResponse

router = APIRouter(tags=["alerts"])


@router.get("", response_model=PaginatedResponse[AlertRead], dependencies=[Depends(get_current_user)])
async def list_alerts(
    status: AlertStatus | None = None,
    severity: AlertSeverity | None = None,
    alert_type: AlertType | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AlertRead]:
    filters = []
    if status is not None:
        filters.append(Alert.status == status)
    if severity is not None:
        filters.append(Alert.severity == severity)
    if alert_type is not None:
        filters.append(Alert.alert_type == alert_type)

    total_stmt = select(func.count()).select_from(Alert)
    items_stmt = select(Alert)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(Alert.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[AlertRead](
        items=[AlertRead.model_validate(a) for a in items], total=total, limit=limit, offset=offset
    )


@router.post(
    "/{alert_id}/acknowledge",
    response_model=AlertRead,
)
async def acknowledge_alert(
    alert_id: int,
    payload: AlertAcknowledgeRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> Alert:
    alert = await get_or_404(session, Alert, alert_id)
    if alert.status == AlertStatus.RESOLVED:
        raise HTTPException(status_code=409, detail="Cannot acknowledge an already-resolved alert")

    # Idempotent if already ACKNOWLEDGED -- acknowledged_by/acknowledged_at
    # may be overwritten, this is not treated as a conflict.
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by = current_user.username
    alert.acknowledged_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(alert)
    return alert


@router.post(
    "/{alert_id}/resolve",
    response_model=AlertRead,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def resolve_alert(
    alert_id: int, payload: AlertResolveRequest, session: AsyncSession = Depends(get_db)
) -> Alert:
    alert = await get_or_404(session, Alert, alert_id)
    if alert.status == AlertStatus.RESOLVED:
        raise HTTPException(status_code=409, detail="Alert is already resolved")

    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(UTC)
    alert.resolved_note = payload.resolved_note

    await session.commit()
    await session.refresh(alert)
    return alert
