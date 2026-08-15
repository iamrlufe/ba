"""backup_jobs remote_directory_override column

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-15 00:00:00.000000

Adds backup_jobs.remote_directory_override (nullable). When NULL (the
common case), the effective remote FTP/SFTP directory for a job is computed
on every read by app.core.remote_paths.resolve_remote_directory from
server.name + backup_jobs.name + backup_jobs.id + backup_jobs.backup_type
(see BackupJob.remote_directory hybrid_property) instead of the old scheme
built purely from internal numeric IDs. Setting this column overrides that
computed value with a literal operator-chosen path.

Plain op.add_column, no op.batch_alter_table needed -- purely additive
nullable column, same convention as 0007/0010/0012's add_column migrations.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "backup_jobs", sa.Column("remote_directory_override", sa.String(length=500), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("backup_jobs", "remote_directory_override")
