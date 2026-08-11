"""Agent heartbeat endpoint.

IMPORTANT: there is no ORM entity `Agent`. An "agent" is a process running
on a host that is represented in this system by a `Server` row. The
`server_id` path parameter below is literally `Server.id` -- this endpoint
updates the Server (and its Disks/Alerts), not a separate agent record.
"""
from datetime import UTC, datetime

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_agent_credential_access
from app.core.auth import require_admin_or_agent_key, require_connection_config_key, require_role
from app.core.db import get_db
from app.core.security import decrypt_secret
from app.models.agent_credential_access_log import AgentCredentialAccessLog
from app.models.alert import Alert
from app.models.backup_job import BackupJob
from app.models.disk import Disk
from app.models.enums import (
    AgentCredentialAccessAuthMethod,
    AgentCredentialAccessOutcome,
    AlertSeverity,
    AlertType,
    ServerStatus,
    UserRole,
)
from app.models.server import Server
from app.routers._alerts import raise_alert_if_absent, resolve_active_alert
from app.routers._deps import get_or_404
from app.schemas.agent import AgentConnectionConfigResponse, AgentHeartbeatRequest, AgentHeartbeatResponse
from app.schemas.agent_credential_access_log import AgentCredentialAccessLogRead
from app.schemas.alert import AlertRead
from app.schemas.backup_job import BackupJobRead
from app.schemas.common import PaginatedResponse
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


@router.get(
    "/{server_id}/jobs",
    response_model=PaginatedResponse[BackupJobRead],
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def list_agent_jobs(
    server_id: int,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[BackupJobRead]:
    """Full-snapshot poll of a server's enabled BackupJobs, for the C#/.NET
    agent to discover what it should be backing up. No destination-path
    field here -- the agent decides the remote path itself and reports it
    back via POST /api/backup-records (BackupRecord.remote_path).
    """
    await get_or_404(session, Server, server_id)

    filters = (BackupJob.server_id == server_id, BackupJob.is_enabled.is_(True))
    total_stmt = select(func.count()).select_from(BackupJob).where(*filters)
    items_stmt = (
        select(BackupJob).where(*filters).order_by(BackupJob.id.desc()).limit(limit).offset(offset)
    )

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[BackupJobRead](
        items=[BackupJobRead.model_validate(j) for j in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{server_id}/connection-config",
    response_model=AgentConnectionConfigResponse,
)
async def get_agent_connection_config(
    server_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db),
    principal=Depends(require_connection_config_key),
) -> AgentConnectionConfigResponse:
    """Decrypts and returns this server's FTP/SFTP credentials for the
    agent to consume. Gated by `require_connection_config_key` -- a
    SEPARATE, more restricted secret than the general X-Agent-Key (see
    that dependency's docstring). Every call is audit-logged, for every
    outcome including denials -- see app/core/audit.py.

    `principal` mirrors `require_admin_or_agent_key`'s convention: the
    authenticated admin `User` if a JWT was used, else `None` (key-header
    path has no User principal) -- used here only to fill in
    `auth_method`/`admin_username` on the audit log row, since the
    dependency itself already logged the "unauthorized" outcome (if any)
    before this handler ever started running.
    """
    auth_method = (
        AgentCredentialAccessAuthMethod.ADMIN_JWT
        if principal is not None
        else AgentCredentialAccessAuthMethod.CONNECTION_CONFIG_KEY
    )
    admin_username = principal.username if principal is not None else None

    # Not get_or_404: that raises immediately, and outcome="not_found" must
    # be audit-logged BEFORE raising (per spec).
    server = await session.get(Server, server_id)
    if server is None:
        await log_agent_credential_access(
            session,
            server_id=server_id,
            request=request,
            auth_method=auth_method,
            admin_username=admin_username,
            outcome=AgentCredentialAccessOutcome.NOT_FOUND,
        )
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    if server.is_deleted:
        await log_agent_credential_access(
            session,
            server_id=server_id,
            request=request,
            auth_method=auth_method,
            admin_username=admin_username,
            outcome=AgentCredentialAccessOutcome.DENIED_DELETED,
        )
        raise HTTPException(status_code=409, detail="Connection config unavailable: server is deleted")

    if server.status == ServerStatus.DISABLED:
        await log_agent_credential_access(
            session,
            server_id=server_id,
            request=request,
            auth_method=auth_method,
            admin_username=admin_username,
            outcome=AgentCredentialAccessOutcome.DENIED_DISABLED,
        )
        raise HTTPException(status_code=403, detail="Connection config unavailable: server is disabled")

    if not server.credentials_set and not server.ssh_key_set:
        await log_agent_credential_access(
            session,
            server_id=server_id,
            request=request,
            auth_method=auth_method,
            admin_username=admin_username,
            outcome=AgentCredentialAccessOutcome.DENIED_NO_CREDENTIALS,
        )
        raise HTTPException(status_code=409, detail="No credentials configured for this server")

    # Decrypt only what is present, at the point of building the response --
    # never cached, never logged (see app/core/security.py). A corrupted
    # ciphertext or a FERNET_KEY rotated without re-encrypting existing rows
    # must still produce an audit row -- this is the one endpoint that must
    # never fail silently/unaudited, so InvalidToken is caught here rather
    # than left to propagate as an unhandled 500.
    try:
        username = decrypt_secret(server.username_encrypted) if server.username_encrypted else None
        password = decrypt_secret(server.password_encrypted) if server.password_encrypted else None
        ssh_private_key = (
            decrypt_secret(server.ssh_private_key_encrypted) if server.ssh_private_key_encrypted else None
        )
    except InvalidToken:
        await log_agent_credential_access(
            session,
            server_id=server_id,
            request=request,
            auth_method=auth_method,
            admin_username=admin_username,
            outcome=AgentCredentialAccessOutcome.DECRYPTION_FAILED,
        )
        raise HTTPException(status_code=500, detail="Stored credentials could not be decrypted") from None

    await log_agent_credential_access(
        session,
        server_id=server_id,
        request=request,
        auth_method=auth_method,
        admin_username=admin_username,
        outcome=AgentCredentialAccessOutcome.SUCCESS,
    )

    return AgentConnectionConfigResponse(
        server_id=server.id,
        host=server.host,
        port=server.port,
        protocol=server.protocol,
        username=username,
        password=password,
        ssh_private_key=ssh_private_key,
    )


@router.get(
    "/credential-access-log",
    response_model=PaginatedResponse[AgentCredentialAccessLogRead],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def list_agent_credential_access_log(
    server_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AgentCredentialAccessLogRead]:
    filters = []
    if server_id is not None:
        filters.append(AgentCredentialAccessLog.server_id == server_id)

    total_stmt = select(func.count()).select_from(AgentCredentialAccessLog)
    items_stmt = select(AgentCredentialAccessLog)
    if filters:
        total_stmt = total_stmt.where(*filters)
        items_stmt = items_stmt.where(*filters)
    items_stmt = items_stmt.order_by(AgentCredentialAccessLog.id.desc()).limit(limit).offset(offset)

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    return PaginatedResponse[AgentCredentialAccessLogRead](
        items=[AgentCredentialAccessLogRead.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
