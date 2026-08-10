"""backup_jobs local_backup_path_pattern column

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-10 00:00:00.000000

Adds the nullable `local_backup_path_pattern` column to `backup_jobs`,
backing the soft (informational-only) consistency check between this
configured path prefix and the `physical_device_name` msdb actually
reports for the latest backup -- see
app/workers/backup_verification.py::execute_verification_run and
app/core/sql_client.py::MsdbBackupInfo.physical_device_name.

This is a soft/informational field only: it never affects
VerificationRun.status, msdb_is_damaged, or alerting -- see the column
comment on app/models/backup_job.py::BackupJob.local_backup_path_pattern.

No index needed (not used in any WHERE/JOIN/ORDER BY), no CHECK
constraint, no server_default -- matches app/models/backup_job.py exactly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "backup_jobs",
        sa.Column("local_backup_path_pattern", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backup_jobs", "local_backup_path_pattern")
