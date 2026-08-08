"""job_run progress fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-08 00:00:00.000000

Adds three nullable progress-reporting columns to `job_runs`
(`percent`, `current_file`, `bytes_done`) plus two CHECK constraints, kept
in sync (by hand) with `app/models/job_run.py`.

SQLite does not support `ALTER TABLE ... ADD CONSTRAINT` directly, so this
uses `op.batch_alter_table(...)` (which rebuilds the table under the hood)
for both the ADD COLUMN and CHECK constraint operations.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("job_runs") as batch_op:
        batch_op.add_column(sa.Column("percent", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("current_file", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("bytes_done", sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint(
            op.f("ck_job_runs_percent_range"),
            "percent IS NULL OR (percent >= 0 AND percent <= 100)",
        )
        batch_op.create_check_constraint(
            op.f("ck_job_runs_bytes_done_non_negative"),
            "bytes_done IS NULL OR bytes_done >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("job_runs") as batch_op:
        batch_op.drop_constraint(op.f("ck_job_runs_bytes_done_non_negative"), type_="check")
        batch_op.drop_constraint(op.f("ck_job_runs_percent_range"), type_="check")
        batch_op.drop_column("bytes_done")
        batch_op.drop_column("current_file")
        batch_op.drop_column("percent")
