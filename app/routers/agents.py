"""Agent heartbeat endpoint.

IMPORTANT: there is no ORM entity `Agent`. An "agent" is a process running
on a host that is represented in this system by a `Server` row. The
`server_id` path parameter below is literally `Server.id` -- this endpoint
updates the Server (and its Disks/Alerts), not a separate agent record.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_admin_or_agent_key
from app.core.db import get_db
from app.models.alert import Alert
from app.models.disk import Disk
from app.models.enums import AlertSeverity, AlertType, ServerStatus
from app.models.server import Server
from app.routers._alerts import raise_alert_if_absent, resolve_active_alert
from app.routers._deps import get_or_404
from app.schemas.agent import AgentHeartbeatRequest, AgentHeartbeatResponse
from app.schemas.alert import AlertRead
from app.schemas.disk import DiskRead
from app.schemas.server import ServerRead

router = APIRouter(tags=["agents"])


@router.post(
    "/{server_id}/heartbeat",
    response_model=AgentHeartbeatResponse,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def agent_heartbeat(
    server_id: int, payload: AgentHeartbeatRequest, session: AsyncSession = Depends(get_db)
) -> AgentHeartbeatResponse:
    server = await get_or_404(session, Server, server_id)

    now = datetime.now(UTC)
    server.last_seen_at = now
    # An operator-DISABLED server is never reactivated by a heartbeat --
    # only last_seen_at is updated.
    if server.status != ServerStatus.DISABLED:
        server.status = ServerStatus.ACTIVE if payload.reachable else ServerStatus.UNREACHABLE

    alerts_raised: list[Alert] = []
    alerts_resolved: list[Alert] = []

    # Any heartbeat -- regardless of the `reachable` payload field --
    # proves the agent process is alive, so it always clears AGENT_OFFLINE
    # (set by the background worker on last_seen_at staleness; see
    # app/workers/alert_worker.py::check_agent_offline). Resolved here
    # (not by the worker on a later tick) to avoid up to
    # ALERT_WORKER_TICK_INTERVAL_SECONDS of lag before the alert clears
    # after the agent actually recovers.
    resolved_offline = await resolve_active_alert(
        session,
        alert_type=AlertType.AGENT_OFFLINE,
        entity_type="server",
        entity_column=Alert.server_id,
        entity_id=server_id,
    )
    if resolved_offline is not None:
        alerts_resolved.append(resolved_offline)

    if payload.reachable is False:
        alert = await raise_alert_if_absent(
            session,
            alert_type=AlertType.SERVER_UNREACHABLE,
            severity=AlertSeverity.CRITICAL,
            entity_type="server",
            entity_column=Alert.server_id,
            entity_id=server_id,
            title=f"Server '{server.name}' unreachable",
            message=f"Agent heartbeat reported server '{server.name}' (id={server_id}) unreachable.",
        )
        if alert is not None:
            alerts_raised.append(alert)
    else:
        resolved = await resolve_active_alert(
            session,
            alert_type=AlertType.SERVER_UNREACHABLE,
            entity_type="server",
            entity_column=Alert.server_id,
            entity_id=server_id,
        )
        if resolved is not None:
            alerts_resolved.append(resolved)

    # --- disk usage sync -----------------------------------------------
    touched_disks: list[Disk] = []
    for item in payload.disks:
        stmt = select(Disk).where(Disk.server_id == server_id, Disk.mount_path == item.mount_path)
        disk = (await session.execute(stmt)).scalar_one_or_none()
        if disk is not None:
            disk.total_bytes = item.total_bytes
            disk.free_bytes = item.free_bytes
            disk.usage_checked_at = now
        else:
            # New disk: waits for manual operator review (is_active=False)
            # before threshold alerting kicks in for it.
            disk = Disk(
                server_id=server_id,
                label=item.label or item.mount_path,
                mount_path=item.mount_path,
                total_bytes=item.total_bytes,
                free_bytes=item.free_bytes,
                usage_checked_at=now,
                is_active=False,
            )
            session.add(disk)
            await session.flush()
        touched_disks.append(disk)

    # --- threshold alerting (only already-active disks) -----------------
    for disk in touched_disks:
        if not disk.is_active:
            continue
        if not disk.total_bytes:
            # Avoid division by zero; nothing meaningful to alert on.
            continue

        used_pct = ((disk.total_bytes - disk.free_bytes) / disk.total_bytes) * 100

        if used_pct >= disk.critical_threshold_pct:
            raised = await raise_alert_if_absent(
                session,
                alert_type=AlertType.DISK_SPACE_CRITICAL,
                severity=AlertSeverity.CRITICAL,
                entity_type="disk",
                entity_column=Alert.disk_id,
                entity_id=disk.id,
                title=f"Disk '{disk.label}' critically low on space",
                message=(
                    f"Disk '{disk.label}' (id={disk.id}) is at {used_pct:.1f}% used "
                    f"(critical threshold {disk.critical_threshold_pct}%)."
                ),
            )
            if raised is not None:
                alerts_raised.append(raised)
            resolved = await resolve_active_alert(
                session,
                alert_type=AlertType.DISK_SPACE_LOW,
                entity_type="disk",
                entity_column=Alert.disk_id,
                entity_id=disk.id,
            )
            if resolved is not None:
                alerts_resolved.append(resolved)
        elif used_pct >= disk.warning_threshold_pct:
            raised = await raise_alert_if_absent(
                session,
                alert_type=AlertType.DISK_SPACE_LOW,
                severity=AlertSeverity.WARNING,
                entity_type="disk",
                entity_column=Alert.disk_id,
                entity_id=disk.id,
                title=f"Disk '{disk.label}' low on space",
                message=(
                    f"Disk '{disk.label}' (id={disk.id}) is at {used_pct:.1f}% used "
                    f"(warning threshold {disk.warning_threshold_pct}%)."
                ),
            )
            if raised is not None:
                alerts_raised.append(raised)
            resolved = await resolve_active_alert(
                session,
                alert_type=AlertType.DISK_SPACE_CRITICAL,
                entity_type="disk",
                entity_column=Alert.disk_id,
                entity_id=disk.id,
            )
            if resolved is not None:
                alerts_resolved.append(resolved)
        else:
            resolved_low = await resolve_active_alert(
                session,
                alert_type=AlertType.DISK_SPACE_LOW,
                entity_type="disk",
                entity_column=Alert.disk_id,
                entity_id=disk.id,
            )
            if resolved_low is not None:
                alerts_resolved.append(resolved_low)
            resolved_critical = await resolve_active_alert(
                session,
                alert_type=AlertType.DISK_SPACE_CRITICAL,
                entity_type="disk",
                entity_column=Alert.disk_id,
                entity_id=disk.id,
            )
            if resolved_critical is not None:
                alerts_resolved.append(resolved_critical)

    await session.commit()
    await session.refresh(server)
    for disk in touched_disks:
        await session.refresh(disk)

    return AgentHeartbeatResponse(
        server=ServerRead.model_validate(server),
        disks=[DiskRead.model_validate(d) for d in touched_disks],
        alerts_raised=[AlertRead.model_validate(a) for a in alerts_raised],
        alerts_resolved=[AlertRead.model_validate(a) for a in alerts_resolved],
    )
