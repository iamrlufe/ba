from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ServerStatus


class SqlInstance(TimestampMixin, Base):
    """A SQL Server instance used for backup verification / restore targets.

    NOTE: `server_id` is intentionally NOT required to match the
    `server_id` on any `BackupJob` that references this instance for
    verification -- a SQL instance can live on a different host than the
    file server holding the backup files. Do not add a cross-check between
    the two.
    """

    __tablename__ = "sql_instances"
    __table_args__ = (
        Index("ix_sql_instances_server_id", "server_id"),
        Index("ix_sql_instances_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instance_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    username_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_windows_auth: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    status: Mapped[ServerStatus] = mapped_column(
        Enum(ServerStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=ServerStatus.ACTIVE,
        server_default=ServerStatus.ACTIVE.value,
    )
    last_verified_connection_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    server: Mapped["Server | None"] = relationship("Server", back_populates="sql_instances")
    backup_jobs: Mapped[list["BackupJob"]] = relationship("BackupJob", back_populates="sql_instance")
    restore_operations: Mapped[list["RestoreOperation"]] = relationship(
        "RestoreOperation", back_populates="sql_instance"
    )

    @hybrid_property
    def credentials_set(self) -> bool:
        """True if a username or password is stored (encrypted) for this instance.

        Backs `app.schemas.sql_instance.SqlInstanceRead.credentials_set` --
        required so that `SqlInstanceRead.model_validate(instance,
        from_attributes=True)` can resolve the field via getattr without
        ever exposing the raw encrypted/plaintext values.
        """
        return bool(self.username_encrypted or self.password_encrypted)
