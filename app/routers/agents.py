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
from sqlalchemy.orm import selectinload

from app.core.audit import log_agent_credential_access
from app.core.auth import require_admin_or_agent_key, require_connection_config_key, require_role
from app.core.config import settings
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
    JobRunStatus,
    ServerStatus,
    UserRole,
)
from app.models.job_run import JobRun
from app.models.server import Server
from app.models.server_metrics import ServerMetrics
from app.routers._alerts import raise_alert_if_absent, resolve_active_alert
from app.routers._deps import get_or_404
from app.schemas.agent import (
    AgentConnectionConfigResponse,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentMonitoringConfigResponse,
)
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

    # --- extended monitoring snapshot (CPU/memory/top-processes/services) ---
    # Snapshot only -- overwritten in place, no history. Row is created
    # lazily here (not eagerly on Server creation) only if this heartbeat
    # actually reports `metrics` and/or `services`. No downstream use of
    # metrics_row.id within this request (unlike the Disk find-or-create
    # block above, which flushes to get disk.id for the threshold-alert
    # loop), so no explicit flush is needed -- session.commit() below
    # persists the new row via SQLAlchemy's unit-of-work regardless.
    if payload.metrics is not None or payload.services is not None:
        stmt = select(ServerMetrics).where(ServerMetrics.server_id == server_id)
        metrics_row = (await session.execute(stmt)).scalar_one_or_none()
        if metrics_row is None:
            metrics_row = ServerMetrics(server_id=server_id)
            session.add(metrics_row)

        if payload.metrics is not None:
            metrics_row.cpu_usage_pct = payload.metrics.cpu_usage_pct
            metrics_row.memory_used_bytes = payload.metrics.memory_used_bytes
            metrics_row.memory_total_bytes = payload.metrics.memory_total_bytes
            metrics_row.top_processes = [p.model_dump() for p in payload.metrics.top_processes]

        if payload.services is not None:
            # Always a full overwrite, never a merge with the previous
            # snapshot -- even an explicit [] replaces whatever was stored
            # before.
            metrics_row.services_status = [s.model_dump() for s in payload.services]

        metrics_row.checked_at = now

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
    agent to discover what it should be backing up. Each job's resolved
    `remote_directory` (see BackupJobRead.remote_directory /
    BackupJob.remote_directory hybrid_property) tells the agent exactly
    where on the FTP/SFTP server to place its files -- the agent still
    reports the actual path used back via POST /api/backup-records
    (BackupRecord.remote_path), but no longer has to invent the directory
    structure itself.
    """
    await get_or_404(session, Server, server_id)

    filters = (BackupJob.server_id == server_id, BackupJob.is_enabled.is_(True))
    total_stmt = select(func.count()).select_from(BackupJob).where(*filters)
    items_stmt = (
        select(BackupJob)
        # sql_instance eager-loaded so BackupJobRead's sql_instance_host/
        # port/instance_name/use_windows_auth hybrid_properties (see
        # app/models/backup_job.py) actually resolve real values here --
        # this is the only BackupJobRead call site that needs them
        # (WATCH-mode msdb-priority detection on the .NET agent). server
        # eager-loaded so BackupJobRead.remote_directory resolves a real
        # value too (server_id is NOT NULL on every BackupJob).
        .options(selectinload(BackupJob.sql_instance), selectinload(BackupJob.server))
        .where(*filters)
        .order_by(BackupJob.id.desc())
        .limit(limit)
        .offset(offset)
    )

    total = (await session.execute(total_stmt)).scalar_one()
    items = (await session.execute(items_stmt)).scalars().all()

    job_ids = [j.id for j in items]
    pending_manual_run_by_job: dict[int, int] = {}
    cancel_requested_run_by_job: dict[int, int] = {}
    if job_ids:
        pending_manual_rows = (
            await session.execute(
                select(JobRun.backup_job_id, JobRun.id).where(
                    JobRun.backup_job_id.in_(job_ids),
                    JobRun.status == JobRunStatus.PENDING,
                    JobRun.triggered_by == "manual",
                )
            )
        ).all()
        for backup_job_id, run_id in pending_manual_rows:
            # At most one PENDING run per backup_job_id (see
            # uq_job_runs_active_per_backup_job), so no first-wins ordering
            # concern here -- unlike cancel_requested_run_by_job below.
            pending_manual_run_by_job[backup_job_id] = run_id

        cancel_requested_rows = (
            await session.execute(
                select(JobRun.backup_job_id, JobRun.id)
                .where(
                    JobRun.backup_job_id.in_(job_ids),
                    JobRun.status == JobRunStatus.CANCELLED,
                    JobRun.cancel_acknowledged_at.is_(None),
                )
                .order_by(JobRun.id.desc())
            )
        ).all()
        for backup_job_id, run_id in cancel_requested_rows:
            # First row encountered per backup_job_id is the highest id
            # (most recent), due to the ORDER BY above -- setdefault keeps
            # only that one.
            cancel_requested_run_by_job.setdefault(backup_job_id, run_id)

    job_reads: list[BackupJobRead] = []
    for j in items:
        job_read = BackupJobRead.model_validate(j)
        job_read.pending_manual_run_id = pending_manual_run_by_job.get(j.id)
        job_read.cancel_requested_run_id = cancel_requested_run_by_job.get(j.id)
        job_reads.append(job_read)

    return PaginatedResponse[BackupJobRead](items=job_reads, total=total, limit=limit, offset=offset)


@router.get(
    "/{server_id}/monitoring-config",
    response_model=AgentMonitoringConfigResponse,
    dependencies=[Depends(require_admin_or_agent_key)],
)
async def get_agent_monitoring_config(
    server_id: int, session: AsyncSession = Depends(get_db)
) -> AgentMonitoringConfigResponse:
    """Resolves which Windows service names this server's agent should
    check on each heartbeat: the per-server `Server.monitored_service_names`
    override if set (including an explicit empty list -- "monitor
    nothing"), else the global `Settings.DEFAULT_MONITORED_SERVICE_NAMES`
    default. Deliberately `is not None`, never a truthiness check -- an
    explicit `[]` override must not silently fall through to the global
    default.
    """
    server = await get_or_404(session, Server, server_id)
    service_names = (
        server.monitored_service_names
        if server.monitored_service_names is not None
        else settings.default_monitored_service_names
    )
    return AgentMonitoringConfigResponse(server_id=server_id, service_names=service_names)


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
