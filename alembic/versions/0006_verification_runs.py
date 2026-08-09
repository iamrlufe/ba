"""verification_runs table

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-09 00:00:00.000000

Adds the `verification_runs` table backing the SQL Server
backup-verification module (app/workers/backup_verification.py,
app/core/sql_client.py, POST /api/backup-jobs/{id}/verify).

Also documents a new `AlertType.BACKUP_VERIFICATION_FAILED` enum literal --
no DDL required for SQLite, same reasoning as 0004
(alert_worker_enums.py): every Enum column in this schema uses
native_enum=False, which compiles to a plain VARCHAR with no CHECK
constraint, so adding a new Python-side string literal is both necessary
and sufficient. Bundled into this revision rather than its own no-op one
since it's directly part of this same feature.

Partial unique index below MUST stay in sync, by hand, with the matching
`sa.Index(..., sqlite_where=...)` declared in `__table_args__` on
app/models/verification_run.py (_ACTIVE_VERIFICATION_RUN_WHERE).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "verification_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("backup_job_id", sa.Integer(), nullable=False),
        sa.Column("backup_record_id", sa.Integer(), nullable=True),
        sa.Column("sql_instance_id", sa.Integer(), nullable=True),
        sa.Column("triggered_by", sa.String(length=50), server_default="scheduler", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "RUNNING", "OK", "CORRUPT", "MISSING", "ERROR",
                name="verificationrunstatus", native_enum=False,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("msdb_backup_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("msdb_is_damaged", sa.Boolean(), nullable=True),
        sa.Column("verifyonly_output", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(
            ["backup_job_id"],
            ["backup_jobs.id"],
            name=op.f("fk_verification_runs_backup_job_id_backup_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["backup_record_id"],
            ["backup_records.id"],
            name=op.f("fk_verification_runs_backup_record_id_backup_records"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sql_instance_id"],
            ["sql_instances.id"],
            name=op.f("fk_verification_runs_sql_instance_id_sql_instances"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_runs")),
    )
    op.create_index("ix_verification_runs_backup_job_id", "verification_runs", ["backup_job_id"], unique=False)
    op.create_index("ix_verification_runs_backup_record_id", "verification_runs", ["backup_record_id"], unique=False)
    op.create_index("ix_verification_runs_sql_instance_id", "verification_runs", ["sql_instance_id"], unique=False)
    op.create_index("ix_verification_runs_status", "verification_runs", ["status"], unique=False)
    op.create_index(
        "ix_verification_runs_backup_job_id_status",
        "verification_runs",
        ["backup_job_id", "status"],
        unique=False,
    )
    op.create_index("ix_verification_runs_started_at", "verification_runs", ["started_at"], unique=False)
    # At most one PENDING/RUNNING verification run per backup_job_id.
    # Keep in sync with app/models/verification_run.py::_ACTIVE_VERIFICATION_RUN_WHERE.
    op.create_index(
        "uq_verification_runs_active_per_backup_job",
        "verification_runs",
        ["backup_job_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('PENDING','RUNNING')"),
    )


def downgrade() -> None:
    op.drop_table("verification_runs")
