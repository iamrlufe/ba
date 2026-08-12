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
    inspect,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import BackupType, TriggerMode


class BackupJob(TimestampMixin, Base):
    """A scheduled backup job: copy source_path from server to disk.

    Verification is enabled if and only if `sql_instance_id` is set --
    there is intentionally no separate `verification_enabled` boolean.
    `sql_instance_id` also gates msdb-priority WATCH detection eligibility
    on the agent side (the C#/.NET agent prefers polling msdb over a
    file-lock-check fallback when a job's `sql_instance_id` is set) -- same
    field as the verification-gating rule above, no new column, no
    decoupling.
    """

    __tablename__ = "backup_jobs"
    __table_args__ = (
        Index("ix_backup_jobs_server_id", "server_id"),
        Index("ix_backup_jobs_disk_id", "disk_id"),
        Index("ix_backup_jobs_sql_instance_id", "sql_instance_id"),
        Index("ix_backup_jobs_is_enabled", "is_enabled"),
        Index("ix_backup_jobs_next_run_at", "next_run_at"),
        Index("ix_backup_jobs_trigger_mode", "trigger_mode"),
        CheckConstraint("retention_days > 0", name="retention_days_positive"),
        CheckConstraint("retention_min_copies >= 0", name="retention_min_copies_non_negative"),
        CheckConstraint(
            "copy_window_start_hour IS NULL OR (copy_window_start_hour >= 0 AND copy_window_start_hour <= 23)",
            name="copy_window_start_hour_range",
        ),
        CheckConstraint(
            "copy_window_end_hour IS NULL OR (copy_window_end_hour >= 0 AND copy_window_end_hour <= 23)",
            name="copy_window_end_hour_range",
        ),
        CheckConstraint(
            "(copy_window_start_hour IS NULL) = (copy_window_end_hour IS NULL)",
            name="copy_window_both_or_neither",
        ),
        CheckConstraint(
            "copy_window_start_hour IS NULL OR copy_window_start_hour != copy_window_end_hour",
            name="copy_window_start_end_distinct",
        ),
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
    # Required iff trigger_mode == SCHEDULE (app-layer only, no DB-level
    # cross-column constraint -- see BackupJobCreate/BackupJobUpdate
    # validators and update_backup_job's post-merge re-check).
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    backup_type: Mapped[BackupType] = mapped_column(
        Enum(BackupType, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=BackupType.FULL,
        server_default=BackupType.FULL.value,
    )
    trigger_mode: Mapped[TriggerMode] = mapped_column(
        Enum(TriggerMode, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=TriggerMode.SCHEDULE,
        server_default=TriggerMode.SCHEDULE.value,
    )
    # Required iff trigger_mode == WATCH (app-layer only, same convention
    # as source_path above).
    watch_directory: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Required iff trigger_mode == SCHEDULE (app-layer only, same
    # convention as source_path above).
    schedule_cron: Mapped[str | None] = mapped_column(String(120), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    retention_min_copies: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    verification_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    expected_max_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missed_run_grace_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    # How long a JobRun may sit PENDING with dispatched_at IS NULL (never
    # claimed/dispatched to an agent) before
    # app.workers.alert_worker.check_stuck_pending_dispatch auto-marks it
    # STUCK and raises AlertType.JOB_STUCK_PENDING. Deliberately NOT gated
    # on is_enabled -- a disabled job's stuck manual run must still be
    # caught.
    pending_to_running_grace_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Copy time-window: applies to BOTH trigger modes, deferring the actual
    # FTP transfer (not detection) until the window opens. NULL/NULL/False
    # means unrestricted (no window configured). Both hours must be set or
    # both NULL (copy_window_both_or_neither), and if set they must differ
    # (copy_window_start_end_distinct) -- see CHECK constraints above.
    copy_window_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    copy_window_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    copy_window_weekend_unrestricted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Soft, informational consistency check only -- NOT the primary
    # path-resolution mechanism for RESTORE VERIFYONLY (that resolves the
    # actual disk path via msdb.dbo.backupmediafamily.physical_device_name,
    # see app/workers/backup_verification.py). If set, a mismatch between
    # this pattern and the physical_device_name SQL Server actually reports
    # is only ever noted in VerificationRun.verifyonly_output -- never
    # affects status/severity/alerting.
    local_backup_path_pattern: Mapped[str | None] = mapped_column(String(500), nullable=True)

    server: Mapped["Server"] = relationship("Server", back_populates="backup_jobs")
    disk: Mapped["Disk"] = relationship("Disk", back_populates="backup_jobs")
    sql_instance: Mapped["SqlInstance | None"] = relationship(
        "SqlInstance", back_populates="backup_jobs"
    )

    # Non-secret SqlInstance connection info, flattened onto BackupJobRead so
    # the .NET agent's single jobs-poll response (GET /api/agents/{server_id}/jobs)
    # carries everything it needs for WATCH-mode msdb-priority file-ready
    # detection (Windows Integrated Auth only -- no credentials involved, see
    # CLAUDE.md) without a second round-trip.
    #
    # IMPORTANT: every one of these guards on `"sql_instance" not in
    # inspect(self).unloaded` before touching the relationship, returning
    # None instead of accessing it when it hasn't been eager-loaded. This
    # is deliberate -- most BackupJobRead call sites (list_backup_jobs,
    # get_backup_job, create_backup_job, update_backup_job) do NOT
    # eager-load `sql_instance` and don't need this agent-specific data;
    # touching an unloaded async relationship there would raise
    # MissingGreenlet. Only app.routers.agents.list_agent_jobs
    # eager-loads it (via selectinload) and actually gets non-None values
    # here -- everywhere else these fields are just None, which is correct
    # (not a bug), not "data unexpectedly missing".
    @hybrid_property
    def sql_instance_host(self) -> str | None:
        if "sql_instance" in inspect(self).unloaded:
            return None
        return self.sql_instance.host if self.sql_instance is not None else None

    @hybrid_property
    def sql_instance_port(self) -> int | None:
        if "sql_instance" in inspect(self).unloaded:
            return None
        return self.sql_instance.port if self.sql_instance is not None else None

    @hybrid_property
    def sql_instance_instance_name(self) -> str | None:
        """The SQL Server NAMED INSTANCE (e.g. "SQLEXPRESS"), i.e.
        SqlInstance.instance_name -- not to be confused with
        SqlInstance.name, which is this project's own display label for
        the SqlInstance row and is never exposed here.
        """
        if "sql_instance" in inspect(self).unloaded:
            return None
        return self.sql_instance.instance_name if self.sql_instance is not None else None

    @hybrid_property
    def sql_instance_use_windows_auth(self) -> bool | None:
        if "sql_instance" in inspect(self).unloaded:
            return None
        return self.sql_instance.use_windows_auth if self.sql_instance is not None else None
    runs: Mapped[list["JobRun"]] = relationship(
        "JobRun", back_populates="backup_job", cascade="all, delete-orphan"
    )
    backup_records: Mapped[list["BackupRecord"]] = relationship(
        "BackupRecord", back_populates="backup_job", cascade="all, delete-orphan"
    )
    verification_runs: Mapped[list["VerificationRun"]] = relationship(
        "VerificationRun", back_populates="backup_job", cascade="all, delete-orphan"
    )
