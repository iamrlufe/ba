"""backup_jobs WATCH trigger mode + copy time-window columns

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-11 00:00:00.000000

Adds BackupJob.trigger_mode (SCHEDULE | WATCH, see app/models/enums.py::
TriggerMode) plus the five columns/relaxations it depends on:

- backup_jobs.schedule_cron / backup_jobs.source_path relax from NOT NULL
  to nullable -- required iff trigger_mode == SCHEDULE (app-layer only, see
  app/schemas/backup_job.py validators and
  app/routers/backup_jobs.py::update_backup_job's post-merge re-check).
  Uses op.batch_alter_table(recreate="always"): SQLite has no native ALTER
  COLUMN, and Alembic's SQLite batch mode is required for the nullability
  relaxation (unlike 0007/0010's plain add_column, this migration also
  changes existing column definitions, not just adds new ones).
- backup_jobs.trigger_mode (NOT NULL, server_default='SCHEDULE') -- every
  pre-existing row backfills to SCHEDULE, which is correct since WATCH did
  not exist before this migration and every row was created under the
  cron-scheduled flow.
- backup_jobs.watch_directory (nullable) -- required iff trigger_mode ==
  WATCH; NULL for all pre-existing (SCHEDULE) rows.
- backup_jobs.copy_window_start_hour / copy_window_end_hour (nullable) and
  copy_window_weekend_unrestricted (NOT NULL, server_default='0') -- the
  copy time-window applies to BOTH trigger modes but is optional; NULL/
  NULL/False backfills to "unrestricted" (no window configured) for every
  pre-existing row, which is the correct behavior-preserving default.

This migration is non-destructive for existing rows: no existing
schedule_cron/source_path value is touched, only their nullability
constraint changes, and every backfilled column (trigger_mode,
copy_window_weekend_unrestricted) uses a default that reproduces the
pre-migration behavior exactly (SCHEDULE mode, unrestricted copy window).

downgrade() WILL fail loudly (IntegrityError, via the plain
`alter_column(nullable=False)` on schedule_cron/source_path) if any row has
trigger_mode=WATCH with schedule_cron IS NULL or source_path IS NULL at
downgrade time -- this is intentional, matching this project's existing
migration philosophy (see 0008_ftp_copy_integrity.py's downgrade()
docstring for the same "fail loudly rather than silently discard/corrupt
data" convention). An operator downgrading past this revision must first
convert any WATCH-mode job back to SCHEDULE (setting schedule_cron/
source_path, clearing watch_directory) or delete it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("backup_jobs", recreate="always") as batch_op:
        batch_op.alter_column("schedule_cron", existing_type=sa.String(length=120), nullable=True)
        batch_op.alter_column("source_path", existing_type=sa.String(length=500), nullable=True)

        batch_op.add_column(
            sa.Column(
                "trigger_mode",
                sa.Enum("SCHEDULE", "WATCH", name="triggermode", native_enum=False),
                server_default="SCHEDULE",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("watch_directory", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("copy_window_start_hour", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("copy_window_end_hour", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "copy_window_weekend_unrestricted",
                sa.Boolean(),
                server_default="0",
                nullable=False,
            )
        )

        batch_op.create_check_constraint(
            "copy_window_start_hour_range",
            "copy_window_start_hour IS NULL OR (copy_window_start_hour >= 0 AND copy_window_start_hour <= 23)",
        )
        batch_op.create_check_constraint(
            "copy_window_end_hour_range",
            "copy_window_end_hour IS NULL OR (copy_window_end_hour >= 0 AND copy_window_end_hour <= 23)",
        )
        batch_op.create_check_constraint(
            "copy_window_both_or_neither",
            "(copy_window_start_hour IS NULL) = (copy_window_end_hour IS NULL)",
        )
        batch_op.create_check_constraint(
            "copy_window_start_end_distinct",
            "copy_window_start_hour IS NULL OR copy_window_start_hour != copy_window_end_hour",
        )

        batch_op.create_index("ix_backup_jobs_trigger_mode", ["trigger_mode"])


def downgrade() -> None:
    with op.batch_alter_table("backup_jobs", recreate="always") as batch_op:
        batch_op.drop_index("ix_backup_jobs_trigger_mode")

        batch_op.drop_constraint("copy_window_start_end_distinct", type_="check")
        batch_op.drop_constraint("copy_window_both_or_neither", type_="check")
        batch_op.drop_constraint("copy_window_end_hour_range", type_="check")
        batch_op.drop_constraint("copy_window_start_hour_range", type_="check")

        batch_op.drop_column("copy_window_weekend_unrestricted")
        batch_op.drop_column("copy_window_end_hour")
        batch_op.drop_column("copy_window_start_hour")
        batch_op.drop_column("watch_directory")
        batch_op.drop_column("trigger_mode")

        # Fails loudly (IntegrityError) if any row has NULL schedule_cron
        # or source_path at downgrade time -- see module docstring.
        batch_op.alter_column("source_path", existing_type=sa.String(length=500), nullable=False)
        batch_op.alter_column("schedule_cron", existing_type=sa.String(length=120), nullable=False)
