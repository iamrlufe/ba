from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import VerificationRunStatus

# Kept in sync (by hand) with alembic/versions/0006_verification_runs.py --
# both the model's __table_args__ index below and the migration's
# op.create_index(...) call must describe the exact same partial unique
# index, or SQLite/Alembic drift is possible. Mirrors
# app/models/job_run.py::_ACTIVE_RUN_WHERE / restore_operation.py's
# _ACTIVE_RESTORE_WHERE pattern.
_ACTIVE_VERIFICATION_RUN_WHERE = "status IN ('PENDING','RUNNING')"


class VerificationRun(TimestampMixin, Base):
    """A single execution (attempt) of backup verification for a BackupJob.

    Verification connects to `BackupJob.sql_instance`, queries
    `msdb.dbo.backupset` for the latest backup of `BackupJob.database_name`,
    and runs `RESTORE VERIFYONLY` against the most recent `BackupRecord` for
    that job -- see app/workers/backup_verification.py for the full
    orchestration and classification rules.
    """

    __tablename__ = "verification_runs"
    __table_args__ = (
        Index("ix_verification_runs_backup_job_id", "backup_job_id"),
        Index("ix_verification_runs_backup_record_id", "backup_record_id"),
        Index("ix_verification_runs_sql_instance_id", "sql_instance_id"),
        Index("ix_verification_runs_status", "status"),
        Index("ix_verification_runs_backup_job_id_status", "backup_job_id", "status"),
        Index("ix_verification_runs_started_at", "started_at"),
        # At most one PENDING/RUNNING verification run per backup_job_id.
        # Keep in sync with alembic/versions/0006_verification_runs.py.
        Index(
            "uq_verification_runs_active_per_backup_job",
            "backup_job_id",
            unique=True,
            sqlite_where=text(_ACTIVE_VERIFICATION_RUN_WHERE),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_job_id: Mapped[int] = mapped_column(
        ForeignKey("backup_jobs.id", ondelete="CASCADE"), nullable=False
    )
    backup_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("backup_records.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalized snapshot of BackupJob.sql_instance_id at dispatch time --
    # set by app.workers.backup_verification.execute_verification_run, not
    # accepted from the client.
    sql_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("sql_instances.id", ondelete="SET NULL"), nullable=True
    )
    triggered_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="scheduler", server_default="scheduler"
    )
    status: Mapped[VerificationRunStatus] = mapped_column(
        Enum(VerificationRunStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=VerificationRunStatus.PENDING,
        server_default=VerificationRunStatus.PENDING.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    msdb_backup_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    msdb_is_damaged: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verifyonly_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # MUST never contain decrypted or encrypted credential values -- see
    # app/workers/backup_verification.py / app/core/sql_client.py.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    backup_job: Mapped["BackupJob"] = relationship("BackupJob", back_populates="verification_runs")
    backup_record: Mapped["BackupRecord | None"] = relationship(
        "BackupRecord", back_populates="verification_runs"
    )
    sql_instance: Mapped["SqlInstance | None"] = relationship(
        "SqlInstance", back_populates="verification_runs"
    )
