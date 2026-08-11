"""Schemas for the agent heartbeat endpoint.

IMPORTANT: there is no ORM entity `Agent`. An "agent" is a process running
on a host that is represented in this system by a `Server` row. The
`server_id` path parameter used by the heartbeat endpoint
(`POST /api/agents/{server_id}/heartbeat`, see app/routers/agents.py) is
literally `Server.id` -- it is not a separate agent identifier.
"""
from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProtocolType
from app.schemas.alert import AlertRead
from app.schemas.disk import AgentDiskUsageItem, DiskRead
from app.schemas.server import ServerRead
from app.schemas.server_metrics import AgentMetricsItem, AgentServiceStatusItem


class AgentHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reachable: bool
    disks: list[AgentDiskUsageItem] = Field(default_factory=list)
    metrics: AgentMetricsItem | None = None
    # Deliberately `None` by default (NOT `Field(default_factory=list)` like
    # `disks` above): None/omitted = "this agent build doesn't report
    # service status at all, don't touch stored services_status". An
    # explicit [] = "agent checked, found/resolved zero services to
    # monitor, overwrite stored services_status to empty." See
    # app/routers/agents.py::agent_heartbeat.
    services: list[AgentServiceStatusItem] | None = None


class AgentHeartbeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server: ServerRead
    disks: list[DiskRead]
    alerts_raised: list[AlertRead]
    alerts_resolved: list[AlertRead]


class AgentMonitoringConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    server_id: int
    service_names: list[str]


class AgentConnectionConfigResponse(BaseModel):
    """Response for `GET /api/agents/{server_id}/connection-config`.

    `from_attributes=False` is deliberate: this is the ONE sanctioned
    exception to app/models/server.py's "credentials must never be exposed"
    rule (see that module's class docstring), and it must stay that way
    only by construction discipline -- always build this manually,
    field-by-field, from freshly-decrypted plaintext in the route handler.
    Never `.model_validate(server, ...)` this schema: `Server` has no
    plaintext `username`/`password`/`ssh_private_key` attributes to
    validate from anyway (only the `*_encrypted` columns), but the
    from_attributes=False here is a deliberate belt-and-suspenders guard
    against a future refactor accidentally adding such attributes and
    silently making this schema attribute-validatable.
    """

    model_config = ConfigDict(from_attributes=False)

    server_id: int
    host: str
    port: int
    protocol: ProtocolType
    username: str | None = None
    password: str | None = None
    ssh_private_key: str | None = None
