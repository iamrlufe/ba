from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ProtocolType, ServerStatus
from app.schemas.common import UtcDatetime


class ServerBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    protocol: ProtocolType
    notes: str | None = None


class ServerCreate(ServerBase):
    username: str | None = None
    password: str | None = None
    ssh_private_key: str | None = None

    @model_validator(mode="after")
    def _require_sftp_credentials(self) -> "ServerCreate":
        if self.protocol == ProtocolType.SFTP:
            if not self.password and not self.ssh_private_key:
                raise ValueError(
                    "SFTP servers require at least one of 'password' or 'ssh_private_key'"
                )
        return self


class ServerUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: ProtocolType | None = None
    notes: str | None = None
    status: ServerStatus | None = None

    # NOTE: for username/password/ssh_private_key, an explicit empty string
    # ("") means "clear this secret" (sets the corresponding *_encrypted
    # column to NULL). Leaving the field unset (None / not provided) means
    # "do not change it" -- the caller must use `.model_dump(exclude_unset=True)`
    # to distinguish "not provided" from "explicitly cleared". Do not treat
    # None and "" as equivalent when applying this schema.
    username: str | None = Field(default=None, description="Empty string clears the stored username.")
    password: str | None = Field(default=None, description="Empty string clears the stored password.")
    ssh_private_key: str | None = Field(
        default=None, description="Empty string clears the stored SSH private key."
    )


class ServerRead(ServerBase):
    id: int
    status: ServerStatus
    credentials_set: bool
    ssh_key_set: bool
    last_seen_at: UtcDatetime | None
    is_deleted: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime
