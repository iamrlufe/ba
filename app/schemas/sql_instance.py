from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ServerStatus
from app.schemas.common import UtcDatetime


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

    @model_validator(mode="after")
    def _port_and_instance_name_mutually_exclusive(self) -> "SqlInstanceCreate":
        # app/core/sql_client.py's connection builder (backup verification)
        # silently drops `port` whenever `instance_name` is set, because the
        # underlying driver raises if both are passed together -- catch this
        # at write time instead of leaving a configured port silently
        # ignored at connect time with no error anywhere.
        if self.port is not None and self.instance_name is not None:
            raise ValueError(
                "port and instance_name cannot both be set -- a named instance "
                "resolves its own port via SQL Browser; specify one or the other"
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

    @model_validator(mode="after")
    def _port_and_instance_name_mutually_exclusive(self) -> "SqlInstanceUpdate":
        # Only catches both being set together IN THIS SAME payload -- it
        # cannot see a value already stored from a previous request (Pydantic
        # schemas are stateless). Same rationale as SqlInstanceCreate's
        # identical check.
        if self.port is not None and self.instance_name is not None:
            raise ValueError(
                "port and instance_name cannot both be set -- a named instance "
                "resolves its own port via SQL Browser; specify one or the other"
            )
        return self


class SqlInstanceRead(SqlInstanceBase):
    id: int
    server_id: int | None
    credentials_set: bool
    status: ServerStatus
    last_verified_connection_at: UtcDatetime | None
    is_deleted: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime
