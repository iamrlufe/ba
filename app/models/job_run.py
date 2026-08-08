from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import JobRunStatus, VerificationStatus

# Kept in sync (by hand) with alembic/versions/0001_initial_schema.py --
# both the model's __table_args__ index below and the migration's
# op.create_index(...) call must describe the exact same partial unique
# index, or SQLite/Alembic drift is possible.
_ACTIVE_RUN_WHERE = "status IN ('PENDING','RUNNING')"


class JobRun(TimestampMixin, Base):
    """A single execution (attempt) of a BackupJob."""

    __tablename__ = "job_runs"
    __table_args__ = (
        Index("ix_job_runs_backup_job_id", "backup_job_id"),
        Index("ix_job_runs_status", "status"),
        Index("ix_job_runs_started_at", "started_at"),
        Index("ix_job_runs_backup_job_id_status", "backup_job_id", "status"),
        # At most one PENDING or RUNNING run per backup_job_id.
        Index(
            "uq_job_runs_active_per_backup_job",
            "backup_job_id",
            unique=True,
            sqlite_where=text(_ACTIVE_RUN_WHERE),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_job_id: Mapped[int] = mapped_column(
        ForeignKey("backup_jobs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JobRunStatus] = mapped_column(
        Enum(JobRunStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=JobRunStatus.PENDING,
        server_default=JobRunStatus.PENDING.value,
    )
    triggered_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="scheduler", server_default="scheduler"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=VerificationStatus.NOT_REQUESTED,
        server_default=VerificationStatus.NOT_REQUESTED.value,
    )
    verification_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    backup_job: Mapped["BackupJob"] = relationship("BackupJob", back_populates="runs")
    backup_records: Mapped[list["BackupRecord"]] = relationship(
        "BackupRecord", back_populates="job_run"
    )
