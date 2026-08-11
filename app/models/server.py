from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, JSON, String, Text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ProtocolType, ServerStatus


class Server(TimestampMixin, Base):
    """A remote file server (FTP/SFTP) that backup files are copied from.

    Credentials are stored encrypted (see app/core/security.py) and must
    never be exposed via `app.schemas.server.ServerRead`.

    The ONE sanctioned exception: `app.schemas.agent.AgentConnectionConfigResponse`,
    returned only by `GET /api/agents/{server_id}/connection-config`
    (app/routers/agents.py, gated by `app.core.auth.require_connection_config_key`
    -- a separate, more restricted secret than the general X-Agent-Key).
    That schema is built manually field-by-field from freshly-decrypted
    plaintext in the route handler (`from_attributes=False`, never
    `.model_validate(server, ...)`) -- see its docstring for the full
    reasoning. Every call to that endpoint is audit-logged (see
    `app.models.agent_credential_access_log.AgentCredentialAccessLog`).
    """

    __tablename__ = "servers"
    __table_args__ = (
        Index("ix_servers_status", "status"),
        Index("ix_servers_is_deleted", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[ProtocolType] = mapped_column(
        Enum(ProtocolType, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )

    username_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ServerStatus] = mapped_column(
        Enum(ServerStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=ServerStatus.ACTIVE,
        server_default=ServerStatus.ACTIVE.value,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Per-server override for monitored Windows service names. NULL means
    # "use Settings.DEFAULT_MONITORED_SERVICE_NAMES"; non-NULL (including an
    # empty list) fully replaces the global default -- never merged. See
    # app/schemas/server.py::ServerUpdate.monitored_service_names and
    # app/routers/agents.py::get_agent_monitoring_config.
    monitored_service_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    disks: Mapped[list["Disk"]] = relationship(
        "Disk", back_populates="server", cascade="all, delete-orphan"
    )
    backup_jobs: Mapped[list["BackupJob"]] = relationship("BackupJob", back_populates="server")
    sql_instances: Mapped[list["SqlInstance"]] = relationship(
        "SqlInstance", back_populates="server"
    )

    @hybrid_property
    def credentials_set(self) -> bool:
        """True if a username or password is stored (encrypted) for this server.

        Backs `app.schemas.server.ServerRead.credentials_set` -- required so
        that `ServerRead.model_validate(server, from_attributes=True)` can
        resolve the field via getattr without ever exposing the raw
        encrypted/plaintext values.
        """
        return bool(self.username_encrypted or self.password_encrypted)

    @hybrid_property
    def ssh_key_set(self) -> bool:
        """True if an SSH private key is stored (encrypted) for this server.

        Backs `app.schemas.server.ServerRead.ssh_key_set` -- see
        `credentials_set` docstring above.
        """
        return bool(self.ssh_private_key_encrypted)
