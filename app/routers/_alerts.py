"""Alert raise/resolve/dedupe helpers, shared by the agent heartbeat,
JobRun completion, and RestoreOperation PATCH handlers.

Not a router itself -- no endpoints are defined here. See
app.models.alert.Alert's class docstring for why alerts must always be
created through a single helper (this module) rather than ad hoc
`Alert(...)` construction.
"""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.alert import Alert
from app.models.enums import AlertSeverity, AlertStatus, AlertType


async def get_active_alert(
    session: AsyncSession,
    *,
    alert_type: AlertType,
    entity_type: str,
    entity_column: InstrumentedAttribute,
    entity_id: int,
) -> Alert | None:
    stmt = select(Alert).where(
        Alert.status == AlertStatus.ACTIVE,
        Alert.alert_type == alert_type,
        Alert.entity_type == entity_type,
        entity_column == entity_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def raise_alert_if_absent(
    session: AsyncSession,
    *,
    alert_type: AlertType,
    severity: AlertSeverity,
    entity_type: str,
    entity_column: InstrumentedAttribute,
    entity_id: int,
    title: str,
    message: str,
) -> Alert | None:
    """Raise a new ACTIVE alert unless one already exists for this entity.

    If `get_active_alert` finds an existing ACTIVE alert, it is returned
    unchanged (no new alert, no update). Otherwise, attempts to insert a
    new `Alert` inside a SAVEPOINT (`session.begin_nested()`). If a
    concurrent request wins the race and the partial unique dedupe index
    (`uq_alerts_active_dedupe`) rejects the insert with an `IntegrityError`,
    that error is caught *locally* (rolling back only the SAVEPOINT) and
    `None` is returned -- this must never propagate out to the global
    IntegrityError -> 409 handler, since callers of this helper (e.g. the
    heartbeat endpoint) may be in the middle of a larger transaction that
    updates unrelated rows (Server, Disk, ...) and must not be aborted by
    an alert-dedupe race.
    """
    existing = await get_active_alert(
        session,
        alert_type=alert_type,
        entity_type=entity_type,
        entity_column=entity_column,
        entity_id=entity_id,
    )
    if existing is not None:
        return existing

    alert = Alert(
        alert_type=alert_type,
        severity=severity,
        entity_type=entity_type,
        title=title,
        message=message,
        **{entity_column.key: entity_id},
    )
    try:
        async with session.begin_nested():
            session.add(alert)
            await session.flush()
    except IntegrityError:
        return None
    return alert


async def resolve_active_alert(
    session: AsyncSession,
    *,
    alert_type: AlertType,
    entity_type: str,
    entity_column: InstrumentedAttribute,
    entity_id: int,
    note: str | None = None,
) -> Alert | None:
    alert = await get_active_alert(
        session,
        alert_type=alert_type,
        entity_type=entity_type,
        entity_column=entity_column,
        entity_id=entity_id,
    )
    if alert is None:
        return None
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(UTC)
    alert.resolved_note = note
    await session.flush()
    # `updated_at` is server-computed (onupdate=func.now()): the UPDATE
    # above leaves it expired, and a later sync attribute access (e.g.
    # Pydantic serialization) would trigger an unawaitable lazy-refresh --
    # refresh explicitly while still in an async context.
    await session.refresh(alert)
    return alert
