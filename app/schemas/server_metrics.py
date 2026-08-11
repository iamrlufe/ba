from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.schemas.common import UtcDatetime


class AgentProcessItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_name: str = Field(min_length=1, max_length=255)
    pid: int | None = None
    cpu_pct: float = Field(ge=0)  # unbounded above -- can exceed 100 on multi-core
    memory_bytes: int = Field(ge=0)


class AgentMetricsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu_usage_pct: float = Field(ge=0, le=100)
    memory_used_bytes: int = Field(ge=0)
    memory_total_bytes: int = Field(ge=0)
    top_processes: list[AgentProcessItem] = Field(default_factory=list, max_length=10)


class AgentServiceStatusItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=1, max_length=255)
    status: str = Field(min_length=1, max_length=64)  # free-text, owned by .NET's ServiceControllerStatus, not a backend enum


class ServerMetricsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: int
    cpu_usage_pct: float | None
    memory_used_bytes: int | None
    memory_total_bytes: int | None
    top_processes: list[dict] | None
    services_status: list[dict] | None
    checked_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def memory_used_pct(self) -> float | None:
        if self.memory_total_bytes is None or self.memory_used_bytes is None or self.memory_total_bytes == 0:
            return None
        return round((self.memory_used_bytes / self.memory_total_bytes) * 100, 2)


class ServerMetricsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: int
    metrics: ServerMetricsRead | None
