from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class BackupRecord(Base):
    """A backup file discovered on a remote server/disk for a BackupJob.

    Uses its own created/updated timestamp columns instead of
    TimestampMixin's `created_at` because the domain-relevant timestamp is
    `detected_at` (when the file was first observed), not a generic
    creation timestamp.
    """

    __tablename__ = "backup_records"
    __table_args__ = (
        Index("ix_backup_records_backup_job_id", "backup_job_id"),
        Index("ix_backup_records_job_run_id", "job_run_id"),
        Index("ix_backup_records_detected_at", "detected_at"),
        UniqueConstraint(
            "backup_job_id",
            "remote_path",
            "file_name",
            name="uq_backup_records_backup_job_id_remote_path_file_name",
        ),
        CheckConstraint("file_size_bytes >= 0", name="file_size_bytes_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_job_id: Mapped[int] = mapped_column(
        ForeignKey("backup_jobs.id", ondelete="CASCADE"), nullable=False
    )
    job_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    remote_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checksum_algorithm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    backup_job: Mapped["BackupJob"] = relationship("BackupJob", back_populates="backup_records")
    job_run: Mapped["JobRun | None"] = relationship("JobRun", back_populates="backup_records")
    restore_operations: Mapped[list["RestoreOperation"]] = relationship(
        "RestoreOperation", back_populates="backup_record"
    )
    verification_runs: Mapped[list["VerificationRun"]] = relationship(
        "VerificationRun", back_populates="backup_record"
    )
