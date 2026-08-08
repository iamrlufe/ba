"""alert worker enum literals (no-op migration)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-08 00:00:00.000000

Documents four new enum literals added to app/models/enums.py for the
background alert-detection worker:
  - ServerStatus.OFFLINE
  - JobRunStatus.TIMEOUT
  - AlertType.AGENT_OFFLINE
  - AlertType.JOB_TIMEOUT

No DDL is required for SQLite: every Enum column in this schema uses
native_enum=False, which SQLAlchemy compiles to a plain VARCHAR with no
CHECK constraint (create_constraint defaults to False) -- confirmed by
inspection of 0001/0002/0003, none of which emit a CHECK constraint for
any enum-typed column. SQLite also does not enforce declared VARCHAR(n)
length at runtime. Adding new string literals to the Python-side enum is
therefore both necessary and sufficient; this revision exists purely to
keep the Alembic history in sync with that model change, per this
project's convention of giving every model-relevant change its own
revision.
"""
from typing import Sequence, Union

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No DDL required -- see module docstring.
    pass


def downgrade() -> None:
    # No DDL required -- see module docstring.
    pass
