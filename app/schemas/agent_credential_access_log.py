from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import AgentCredentialAccessAuthMethod, AgentCredentialAccessOutcome


class AgentCredentialAccessLogRead(BaseModel):
    """Read schema for `GET /api/agents/credential-access-log` (admin-only).

    Deliberately contains only metadata -- never decrypted credential
    values or the raw key header value (those are never stored on the
    underlying `AgentCredentialAccessLog` model in the first place, see
    app/models/agent_credential_access_log.py).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: int
    accessed_at: datetime
    requester_ip: str | None
    auth_method: AgentCredentialAccessAuthMethod
    admin_username: str | None
    outcome: AgentCredentialAccessOutcome
