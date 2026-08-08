"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-08 00:00:00.000000

Table creation order is fixed by FK dependencies (and mirrors
app/models/__init__.py):
servers -> sql_instances -> disks -> backup_jobs -> job_runs ->
backup_records -> restore_operations -> alerts.

Partial unique indexes (SQLite `sqlite_where`) below MUST stay in sync,
by hand, with the matching `sa.Index(..., sqlite_where=...)` declared in
`__table_args__` on the corresponding model:
  - job_runs:            app/models/job_run.py            (_ACTIVE_RUN_WHERE)
  - restore_operations:  app/models/restore_operation.py   (_ACTIVE_RESTORE_WHERE)
  - alerts:               app/models/alert.py               (_ACTIVE_ALERT_WHERE)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------- servers
    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column(
            "protocol",
            sa.Enum("FTP", "SFTP", name="protocoltype", native_enum=False),
            nullable=False,
        ),
        sa.Column("username_encrypted", sa.Text(), nullable=True),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "DISABLED", "UNREACHABLE", name="serverstatus", native_enum=False),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_servers")),
        sa.UniqueConstraint("name", name=op.f("uq_servers_name")),
    )
    op.create_index("ix_servers_status", "servers", ["status"], unique=False)
    op.create_index("ix_servers_is_deleted", "servers", ["is_deleted"], unique=False)

    # ---------------------------------------------------------- sql_instances
    op.create_table(
        "sql_instances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("instance_name", sa.String(length=128), nullable=True),
        sa.Column("username_encrypted", sa.Text(), nullable=True),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("use_windows_auth", sa.Boolean(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "DISABLED", "UNREACHABLE", name="serverstatus", native_enum=False),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("last_verified_connection_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_sql_instances_server_id_servers"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sql_instances")),
        sa.UniqueConstraint("name", name=op.f("uq_sql_instances_name")),
    )
    op.create_index("ix_sql_instances_server_id", "sql_instances", ["server_id"], unique=False)
    op.create_index("ix_sql_instances_status", "sql_instances", ["status"], unique=False)

    # ---------------------------------------------------------------- disks
    op.create_table(
        "disks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("mount_path", sa.String(length=500), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=True),
        sa.Column("free_bytes", sa.BigInteger(), nullable=True),
        sa.Column("usage_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warning_threshold_pct", sa.Integer(), server_default="80", nullable=False),
        sa.Column("critical_threshold_pct", sa.Integer(), server_default="90", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "warning_threshold_pct > 0 AND warning_threshold_pct < 100",
            name=op.f("ck_disks_warning_threshold_range"),
        ),
        sa.CheckConstraint(
            "critical_threshold_pct > warning_threshold_pct AND critical_threshold_pct <= 100",
            name=op.f("ck_disks_critical_threshold_range"),
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_disks_server_id_servers"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disks")),
        sa.UniqueConstraint("server_id", "mount_path", name="uq_disks_server_id_mount_path"),
    )
    op.create_index("ix_disks_server_id", "disks", ["server_id"], unique=False)

    # ----------------------------------------------------------- backup_jobs
    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("disk_id", sa.Integer(), nullable=False),
        sa.Column("sql_instance_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("database_name", sa.String(length=255), nullable=True),
        sa.Column("source_path", sa.String(length=500), nullable=False),
        sa.Column(
            "backup_type",
            sa.Enum("FULL", "DIFFERENTIAL", "TRANSACTION_LOG", "CUSTOM", name="backuptype", native_enum=False),
            server_default="FULL",
            nullable=False,
        ),
        sa.Column("schedule_cron", sa.String(length=120), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("retention_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("retention_min_copies", sa.Integer(), server_default="1", nullable=False),
        sa.Column("verification_method", sa.String(length=50), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("expected_max_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("missed_run_grace_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("retention_days > 0", name=op.f("ck_backup_jobs_retention_days_positive")),
        sa.CheckConstraint("retention_min_copies >= 0", name=op.f("ck_backup_jobs_retention_min_copies_non_negative")),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_backup_jobs_server_id_servers"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["disk_id"], ["disks.id"], name=op.f("fk_backup_jobs_disk_id_disks"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["sql_instance_id"],
            ["sql_instances.id"],
            name=op.f("fk_backup_jobs_sql_instance_id_sql_instances"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backup_jobs")),
    )
    op.create_index("ix_backup_jobs_server_id", "backup_jobs", ["server_id"], unique=False)
    op.create_index("ix_backup_jobs_disk_id", "backup_jobs", ["disk_id"], unique=False)
    op.create_index("ix_backup_jobs_sql_instance_id", "backup_jobs", ["sql_instance_id"], unique=False)
    op.create_index("ix_backup_jobs_is_enabled", "backup_jobs", ["is_enabled"], unique=False)
    op.create_index("ix_backup_jobs_next_run_at", "backup_jobs", ["next_run_at"], unique=False)

    # -------------------------------------------------------------- job_runs
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("backup_job_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "RUNNING", "SUCCESS", "WARNING", "FAILED", "CANCELLED",
                name="jobrunstatus", native_enum=False,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("triggered_by", sa.String(length=50), server_default="scheduler", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum(
                "NOT_REQUESTED", "PENDING", "PASSED", "FAILED",
                name="verificationstatus", native_enum=False,
            ),
            server_default="NOT_REQUESTED",
            nullable=False,
        ),
        sa.Column("verification_details", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("log_output", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(
            ["backup_job_id"], ["backup_jobs.id"], name=op.f("fk_job_runs_backup_job_id_backup_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
    )
    op.create_index("ix_job_runs_backup_job_id", "job_runs", ["backup_job_id"], unique=False)
    op.create_index("ix_job_runs_status", "job_runs", ["status"], unique=False)
    op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"], unique=False)
    op.create_index(
        "ix_job_runs_backup_job_id_status", "job_runs", ["backup_job_id", "status"], unique=False
    )
    # At most one PENDING/RUNNING run per backup_job_id.
    # Keep in sync with app/models/job_run.py::_ACTIVE_RUN_WHERE.
    op.create_index(
        "uq_job_runs_active_per_backup_job",
        "job_runs",
        ["backup_job_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING','RUNNING')"),
    )

    # --------------------------------------------------------- backup_records
    op.create_table(
        "backup_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("backup_job_id", sa.Integer(), nullable=False),
        sa.Column("job_run_id", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("remote_path", sa.String(length=500), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("checksum_algorithm", sa.String(length=20), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("file_size_bytes >= 0", name=op.f("ck_backup_records_file_size_bytes_non_negative")),
        sa.ForeignKeyConstraint(
            ["backup_job_id"], ["backup_jobs.id"], name=op.f("fk_backup_records_backup_job_id_backup_jobs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_run_id"], ["job_runs.id"], name=op.f("fk_backup_records_job_run_id_job_runs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backup_records")),
        sa.UniqueConstraint(
            "backup_job_id", "remote_path", "file_name",
            name="uq_backup_records_backup_job_id_remote_path_file_name",
        ),
    )
    op.create_index("ix_backup_records_backup_job_id", "backup_records", ["backup_job_id"], unique=False)
    op.create_index("ix_backup_records_job_run_id", "backup_records", ["job_run_id"], unique=False)
    op.create_index("ix_backup_records_detected_at", "backup_records", ["detected_at"], unique=False)

    # ------------------------------------------------------ restore_operations
    op.create_table(
        "restore_operations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("backup_record_id", sa.Integer(), nullable=False),
        sa.Column("sql_instance_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("database_name", sa.String(length=255), nullable=False),
        sa.Column(
            "mode", sa.Enum("ALL", "EXISTING", "MISSING", name="restoremode", native_enum=False), nullable=False
        ),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column(
            "requested_by_channel",
            sa.Enum("WEB", "TELEGRAM", name="requestchannel", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED", name="restorestatus", native_enum=False),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(
            ["backup_record_id"],
            ["backup_records.id"],
            name=op.f("fk_restore_operations_backup_record_id_backup_records"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sql_instance_id"],
            ["sql_instances.id"],
            name=op.f("fk_restore_operations_sql_instance_id_sql_instances"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_restore_operations_server_id_servers"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restore_operations")),
    )
    op.create_index("ix_restore_operations_status", "restore_operations", ["status"], unique=False)
    op.create_index(
        "ix_restore_operations_sql_instance_id", "restore_operations", ["sql_instance_id"], unique=False
    )
    op.create_index(
        "ix_restore_operations_backup_record_id", "restore_operations", ["backup_record_id"], unique=False
    )
    op.create_index(
        "ix_restore_operations_requested_by", "restore_operations", ["requested_by"], unique=False
    )
    # At most one active (PENDING/RUNNING) restore per (sql_instance_id, database_name).
    # Keep in sync with app/models/restore_operation.py::_ACTIVE_RESTORE_WHERE.
    op.create_index(
        "uq_restore_operations_active_per_instance_db",
        "restore_operations",
        ["sql_instance_id", "database_name"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING','RUNNING')"),
    )

    # ---------------------------------------------------------------- alerts
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "alert_type",
            sa.Enum(
                "JOB_FAILED", "JOB_MISSED", "VERIFICATION_FAILED", "DISK_SPACE_LOW",
                "DISK_SPACE_CRITICAL", "SERVER_UNREACHABLE", "RESTORE_FAILED",
                name="alerttype", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("INFO", "WARNING", "CRITICAL", name="alertseverity", native_enum=False),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("disk_id", sa.Integer(), nullable=True),
        sa.Column("backup_job_id", sa.Integer(), nullable=True),
        sa.Column("job_run_id", sa.Integer(), nullable=True),
        sa.Column("restore_operation_id", sa.Integer(), nullable=True),
        # Generated (computed) column collapsing the five nullable per-entity
        # FK columns above into a single non-null(-ish) discriminant, since
        # SQL NULL is never equal to NULL for UNIQUE-index purposes and at
        # least four of the five FK columns are always NULL on every row.
        # Keep in sync with app/models/alert.py::_ENTITY_KEY_SQL.
        sa.Column(
            "entity_key",
            sa.Integer(),
            sa.Computed(
                "COALESCE(server_id, disk_id, backup_job_id, job_run_id, restore_operation_id)",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "ACKNOWLEDGED", "RESOLVED", name="alertstatus", native_enum=False),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("TELEGRAM", "WEB", "BOTH", name="alertchannel", native_enum=False),
            server_default="BOTH",
            nullable=False,
        ),
        sa.Column("delivered_telegram_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_web_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('server','disk','backup_job','job_run','restore_operation')",
            name=op.f("ck_alerts_entity_type_valid"),
        ),
        sa.CheckConstraint(
            "(CASE WHEN server_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN disk_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN backup_job_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN job_run_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN restore_operation_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
            name=op.f("ck_alerts_at_most_one_entity_fk"),
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_alerts_server_id_servers"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["disk_id"], ["disks.id"], name=op.f("fk_alerts_disk_id_disks"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["backup_job_id"], ["backup_jobs.id"], name=op.f("fk_alerts_backup_job_id_backup_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["job_run_id"], ["job_runs.id"], name=op.f("fk_alerts_job_run_id_job_runs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["restore_operation_id"],
            ["restore_operations.id"],
            name=op.f("fk_alerts_restore_operation_id_restore_operations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    op.create_index("ix_alerts_status", "alerts", ["status"], unique=False)
    op.create_index("ix_alerts_severity", "alerts", ["severity"], unique=False)
    op.create_index("ix_alerts_server_id", "alerts", ["server_id"], unique=False)
    op.create_index("ix_alerts_disk_id", "alerts", ["disk_id"], unique=False)
    op.create_index("ix_alerts_backup_job_id", "alerts", ["backup_job_id"], unique=False)
    op.create_index("ix_alerts_job_run_id", "alerts", ["job_run_id"], unique=False)
    op.create_index("ix_alerts_restore_operation_id", "alerts", ["restore_operation_id"], unique=False)
    op.create_index("ix_alerts_status_severity", "alerts", ["status", "severity"], unique=False)
    # De-duplicate active alerts: at most one ACTIVE alert per
    # (entity_type, entity_key, alert_type). Uses the generated `entity_key`
    # column instead of the five raw FK columns directly -- see the comment
    # on that column above and app/models/alert.py class docstring.
    # Keep in sync with app/models/alert.py::_ACTIVE_ALERT_WHERE.
    op.create_index(
        "uq_alerts_active_dedupe",
        "alerts",
        ["entity_type", "entity_key", "alert_type"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("restore_operations")
    op.drop_table("backup_records")
    op.drop_table("job_runs")
    op.drop_table("backup_jobs")
    op.drop_table("disks")
    op.drop_table("sql_instances")
    op.drop_table("servers")
