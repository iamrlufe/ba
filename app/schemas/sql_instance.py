from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ServerStatus


class SqlInstanceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    instance_name: str | None = Field(default=None, max_length=128)
    use_windows_auth: bool = False
    notes: str | None = None


class SqlInstanceCreate(SqlInstanceBase):
    server_id: int | None = None
    username: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def _require_sql_credentials_when_not_windows_auth(self) -> "SqlInstanceCreate":
        if not self.use_windows_auth:
            if not self.username or not self.password:
                raise ValueError(
                    "username and password are required (and must be non-empty) "
                    "when use_windows_auth is False"
                )
        return self


class SqlInstanceUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    instance_name: str | None = Field(default=None, max_length=128)
    use_windows_auth: bool | None = None
    notes: str | None = None
    server_id: int | None = None
    # Same "" clears / None-not-provided-means-unchanged semantics as
    # ServerUpdate -- see app.schemas.server.ServerUpdate docstring.
    username: str | None = None
    password: str | None = None


class SqlInstanceRead(SqlInstanceBase):
    id: int
    server_id: int | None
    credentials_set: bool
    status: ServerStatus
    last_verified_connection_at: datetime | None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
