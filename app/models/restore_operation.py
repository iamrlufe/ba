from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import RequestChannel, RestoreMode, RestoreStatus

# Kept in sync (by hand) with alembic/versions/0001_initial_schema.py.
_ACTIVE_RESTORE_WHERE = "status IN ('PENDING','RUNNING')"


class RestoreOperation(TimestampMixin, Base):
    """A request to restore a BackupRecord into a SQL Server database.

    IMPORTANT: cancellation (status -> CANCELLED) is only valid from
    PENDING. The RUNNING -> CANCELLED transition is NOT allowed: a SQL
    Server `RESTORE DATABASE` in progress cannot be safely interrupted
    without risking leaving the database stuck in RESTORING/SUSPECT state.
    This is enforced in `app.schemas.restore_operation.RestoreOperationUpdate`
    / `is_valid_transition`, not just documented here.
    """

    __tablename__ = "restore_operations"
    __table_args__ = (
        Index("ix_restore_operations_status", "status"),
        Index("ix_restore_operations_sql_instance_id", "sql_instance_id"),
        Index("ix_restore_operations_backup_record_id", "backup_record_id"),
        Index("ix_restore_operations_requested_by", "requested_by"),
        # At most one active (PENDING/RUNNING) restore per (sql_instance_id, database_name).
        Index(
            "uq_restore_operations_active_per_instance_db",
            "sql_instance_id",
            "database_name",
            unique=True,
            sqlite_where=text(_ACTIVE_RESTORE_WHERE),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_record_id: Mapped[int] = mapped_column(
        ForeignKey("backup_records.id", ondelete="RESTRICT"), nullable=False
    )
    sql_instance_id: Mapped[int] = mapped_column(
        ForeignKey("sql_instances.id", ondelete="RESTRICT"), nullable=False
    )
    # Denormalized sql_instance.server_id at creation time -- computed by
    # the service/CRUD layer, never accepted from the client.
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), nullable=True
    )
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[RestoreMode] = mapped_column(
        Enum(RestoreMode, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by_channel: Mapped[RequestChannel] = mapped_column(
        Enum(RequestChannel, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    status: Mapped[RestoreStatus] = mapped_column(
        Enum(RestoreStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=RestoreStatus.PENDING,
        server_default=RestoreStatus.PENDING.value,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    backup_record: Mapped["BackupRecord"] = relationship(
        "BackupRecord", back_populates="restore_operations"
    )
    sql_instance: Mapped["SqlInstance"] = relationship(
        "SqlInstance", back_populates="restore_operations"
    )
