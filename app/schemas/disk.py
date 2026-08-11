from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.schemas.common import UtcDatetime


class DiskBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str = Field(min_length=1, max_length=255)
    mount_path: str = Field(min_length=1, max_length=500)
    warning_threshold_pct: int = Field(default=80, gt=0, lt=100)
    critical_threshold_pct: int = Field(default=90, gt=0, le=100)

    @model_validator(mode="after")
    def _critical_above_warning(self) -> "DiskBase":
        if self.critical_threshold_pct <= self.warning_threshold_pct:
            raise ValueError("critical_threshold_pct must be greater than warning_threshold_pct")
        return self


class DiskCreate(DiskBase):
    server_id: int


class DiskUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=255)
    mount_path: str | None = Field(default=None, min_length=1, max_length=500)
    warning_threshold_pct: int | None = Field(default=None, gt=0, lt=100)
    critical_threshold_pct: int | None = Field(default=None, gt=0, le=100)
    is_active: bool | None = None
    # server_id is intentionally NOT editable here.

    @model_validator(mode="after")
    def _critical_above_warning_if_both_present(self) -> "DiskUpdate":
        if self.warning_threshold_pct is not None and self.critical_threshold_pct is not None:
            if self.critical_threshold_pct <= self.warning_threshold_pct:
                raise ValueError(
                    "critical_threshold_pct must be greater than warning_threshold_pct"
                )
        return self


class DiskRead(DiskBase):
    id: int
    server_id: int
    total_bytes: int | None
    free_bytes: int | None
    usage_checked_at: UtcDatetime | None
    is_active: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def used_pct(self) -> float | None:
        if self.total_bytes is None or self.free_bytes is None or self.total_bytes == 0:
            return None
        used = self.total_bytes - self.free_bytes
        return round((used / self.total_bytes) * 100, 2)


class DiskUsageUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    total_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _free_within_total(self) -> "DiskUsageUpdate":
        if self.free_bytes > self.total_bytes:
            raise ValueError("free_bytes cannot exceed total_bytes")
        return self


class AgentDiskUsageItem(DiskUsageUpdate):
    """One disk-usage sample reported by an agent heartbeat.

    Identifies the disk by `(server_id, mount_path)` -- `server_id` comes
    from the heartbeat path parameter, not this schema. See
    app/schemas/agent.py::AgentHeartbeatRequest / app/routers/agents.py.
    """

    mount_path: str = Field(min_length=1, max_length=500)
    label: str | None = Field(default=None, max_length=255)
