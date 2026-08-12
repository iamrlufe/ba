"""job_runs cancel/dispatch lifecycle columns + backup_jobs stuck-pending grace

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-12 00:00:00.000000

Adds the columns backing three connected JobRun-lifecycle features:

- job_runs.dispatched_at (nullable): set exactly once, never cleared, the
  moment a run is actually handed to an agent -- at INSERT time for
  triggered_by in ("scheduler", "watch") (app.routers.job_runs.
  create_job_run), or via POST /api/job-runs/{id}/claim for
  triggered_by="manual". NULL means "still waiting to be picked up" -- see
  app.workers.alert_worker.check_stuck_pending_dispatch, which watches for
  that staying true too long.
- job_runs.cancel_requested_at / cancel_requested_by / cancel_acknowledged_at
  (all nullable): cancel-lifecycle bookkeeping for
  POST /api/job-runs/{id}/cancel (human-initiated, admin-only) and the
  stuck-pending detector's own auto-cancel-as-STUCK path. cancel_acknowledged_at
  is set later/separately (via the update_job_run/complete_job_run
  auto-acknowledgment side effect, or immediately by the stuck-pending
  detector for its own STUCK case) -- never by the cancel endpoint itself.
- backup_jobs.pending_to_running_grace_minutes (NOT NULL, server_default='30'):
  mirrors missed_run_grace_minutes exactly -- how long a JobRun may sit
  PENDING with dispatched_at IS NULL before being auto-marked STUCK.

No CHECK-constraint changes needed for the new JobRunStatus.STUCK /
AlertType.JOB_STUCK_PENDING enum members: both JobRunStatus (job_runs.status)
and AlertType (alerts.alert_type) are stored as plain VARCHAR
(native_enum=False, values_callable=... -- see app/models/enums.py's module
docstring and each model's Enum(...) column definition), with no DB-level
CHECK constraint enumerating allowed values anywhere in this schema.

Plain op.add_column calls throughout, no op.batch_alter_table needed --
every change here is purely additive (new nullable/defaulted columns), same
convention as 0007/0010's plain add_column migrations.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_runs", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "job_runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("job_runs", sa.Column("cancel_requested_by", sa.String(length=255), nullable=True))
    op.add_column(
        "job_runs", sa.Column("cancel_acknowledged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "backup_jobs",
        sa.Column(
            "pending_to_running_grace_minutes",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("backup_jobs", "pending_to_running_grace_minutes")
    op.drop_column("job_runs", "cancel_acknowledged_at")
    op.drop_column("job_runs", "cancel_requested_by")
    op.drop_column("job_runs", "cancel_requested_at")
    op.drop_column("job_runs", "dispatched_at")
