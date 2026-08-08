"""Schemas for the agent heartbeat endpoint.

IMPORTANT: there is no ORM entity `Agent`. An "agent" is a process running
on a host that is represented in this system by a `Server` row. The
`server_id` path parameter used by the heartbeat endpoint
(`POST /api/agents/{server_id}/heartbeat`, see app/routers/agents.py) is
literally `Server.id` -- it is not a separate agent identifier.
"""
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.alert import AlertRead
from app.schemas.disk import AgentDiskUsageItem, DiskRead
from app.schemas.server import ServerRead


class AgentHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reachable: bool
    disks: list[AgentDiskUsageItem] = Field(default_factory=list)


class AgentHeartbeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server: ServerRead
    disks: list[DiskRead]
    alerts_raised: list[AlertRead]
    alerts_resolved: list[AlertRead]
