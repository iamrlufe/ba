from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import BackupType


class BackupJob(TimestampMixin, Base):
    """A scheduled backup job: copy source_path from server to disk.

    Verification is enabled if and only if `sql_instance_id` is set --
    there is intentionally no separate `verification_enabled` boolean.
    """

    __tablename__ = "backup_jobs"
    __table_args__ = (
        Index("ix_backup_jobs_server_id", "server_id"),
        Index("ix_backup_jobs_disk_id", "disk_id"),
        Index("ix_backup_jobs_sql_instance_id", "sql_instance_id"),
        Index("ix_backup_jobs_is_enabled", "is_enabled"),
        Index("ix_backup_jobs_next_run_at", "next_run_at"),
        CheckConstraint("retention_days > 0", name="retention_days_positive"),
        CheckConstraint("retention_min_copies >= 0", name="retention_min_copies_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="RESTRICT"), nullable=False
    )
    disk_id: Mapped[int] = mapped_column(
        ForeignKey("disks.id", ondelete="RESTRICT"), nullable=False
    )
    sql_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("sql_instances.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    database_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    backup_type: Mapped[BackupType] = mapped_column(
        Enum(BackupType, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=BackupType.FULL,
        server_default=BackupType.FULL.value,
    )
    schedule_cron: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    retention_min_copies: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    verification_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    expected_max_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missed_run_grace_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    server: Mapped["Server"] = relationship("Server", back_populates="backup_jobs")
    disk: Mapped["Disk"] = relationship("Disk", back_populates="backup_jobs")
    sql_instance: Mapped["SqlInstance | None"] = relationship(
        "SqlInstance", back_populates="backup_jobs"
    )
    runs: Mapped[list["JobRun"]] = relationship(
        "JobRun", back_populates="backup_job", cascade="all, delete-orphan"
    )
    backup_records: Mapped[list["BackupRecord"]] = relationship(
        "BackupRecord", back_populates="backup_job", cascade="all, delete-orphan"
    )
    verification_runs: Mapped[list["VerificationRun"]] = relationship(
        "VerificationRun", back_populates="backup_job", cascade="all, delete-orphan"
    )
