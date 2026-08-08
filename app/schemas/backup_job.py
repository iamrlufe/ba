from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import BackupType


class BackupJobBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(min_length=1, max_length=255)
    database_name: str | None = Field(default=None, max_length=255)
    source_path: str = Field(min_length=1, max_length=500)
    backup_type: BackupType = BackupType.FULL
    schedule_cron: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="UTC", max_length=64)
    retention_days: int = Field(default=30, gt=0)
    retention_min_copies: int = Field(default=1, ge=0)
    verification_method: str | None = Field(default=None, max_length=50)
    expected_max_duration_minutes: int | None = Field(default=None, gt=0)
    missed_run_grace_minutes: int = Field(default=60, gt=0)


class BackupJobCreate(BackupJobBase):
    server_id: int
    disk_id: int
    sql_instance_id: int | None = None
    is_enabled: bool = True

    @model_validator(mode="after")
    def _verification_method_required_when_verifying(self) -> "BackupJobCreate":
        if self.sql_instance_id is not None and self.verification_method is None:
            raise ValueError(
                "verification_method is required when sql_instance_id is set "
                "(verification is enabled iff sql_instance_id is set)"
            )
        return self


class BackupJobUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    database_name: str | None = Field(default=None, max_length=255)
    source_path: str | None = Field(default=None, min_length=1, max_length=500)
    backup_type: BackupType | None = None
    schedule_cron: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    retention_days: int | None = Field(default=None, gt=0)
    retention_min_copies: int | None = Field(default=None, ge=0)
    verification_method: str | None = Field(default=None, max_length=50)
    expected_max_duration_minutes: int | None = Field(default=None, gt=0)
    missed_run_grace_minutes: int | None = Field(default=None, gt=0)
    is_enabled: bool | None = None

    # sql_instance_id follows the "exclude_unset" pattern: if the key is
    # absent from the incoming payload, leave the stored value untouched;
    # if the key is present with value null, clear it (disable
    # verification). Callers MUST build this schema with
    # `model_validate(payload)` from a payload where absent keys are truly
    # absent (not defaulted to null), and apply changes via
    # `.model_dump(exclude_unset=True)`.
    sql_instance_id: int | None = None

    # server_id / disk_id are intentionally NOT editable here.


class BackupJobRead(BackupJobBase):
    id: int
    server_id: int
    disk_id: int
    sql_instance_id: int | None
    is_enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
