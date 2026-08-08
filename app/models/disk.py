from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Disk(TimestampMixin, Base):
    """A monitored storage location (mount point) on a Server."""

    __tablename__ = "disks"
    __table_args__ = (
        Index("ix_disks_server_id", "server_id"),
        UniqueConstraint("server_id", "mount_path", name="uq_disks_server_id_mount_path"),
        CheckConstraint(
            "warning_threshold_pct > 0 AND warning_threshold_pct < 100",
            name="warning_threshold_range",
        ),
        CheckConstraint(
            "critical_threshold_pct > warning_threshold_pct AND critical_threshold_pct <= 100",
            name="critical_threshold_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    mount_path: Mapped[str] = mapped_column(String(500), nullable=False)
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warning_threshold_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=80, server_default="80")
    critical_threshold_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=90, server_default="90")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    server: Mapped["Server"] = relationship("Server", back_populates="disks")
    backup_jobs: Mapped[list["BackupJob"]] = relationship("BackupJob", back_populates="disk")
