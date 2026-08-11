"""Shared helper for writing `AgentCredentialAccessLog` rows.

Used by both `app.core.auth.require_connection_config_key` (the
"unauthorized" outcome, written before the route handler even runs) and
`app.routers.agents.get_agent_connection_config` (every other outcome).
Kept in `app.core` (not `app.routers`) specifically so `app.core.auth` can
import it without a core -> routers dependency.
"""
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_credential_access_log import AgentCredentialAccessLog
from app.models.enums import AgentCredentialAccessAuthMethod, AgentCredentialAccessOutcome


async def log_agent_credential_access(
    session: AsyncSession,
    *,
    server_id: int,
    request: Request,
    auth_method: AgentCredentialAccessAuthMethod,
    admin_username: str | None,
    outcome: AgentCredentialAccessOutcome,
) -> None:
    """Insert one `AgentCredentialAccessLog` row and commit it immediately.

    Commits on its own (rather than relying on the caller's later
    `session.commit()`) because some call sites (the "unauthorized" path in
    `require_connection_config_key`, and every denial path in the route
    handler) raise an `HTTPException` right after calling this, which would
    otherwise discard the log row along with the rest of the (empty)
    transaction.

    Never pass decrypted credential values or the raw key header value in
    here -- only the metadata fields below.
    """
    session.add(
        AgentCredentialAccessLog(
            server_id=server_id,
            requester_ip=request.client.host if request.client else None,
            auth_method=auth_method,
            admin_username=admin_username,
            outcome=outcome,
        )
    )
    await session.commit()
