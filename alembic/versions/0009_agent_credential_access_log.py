"""agent_credential_access_logs table

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-11 00:00:00.000000

Adds the `agent_credential_access_logs` table: an insert-only audit trail
of every call to GET /api/agents/{server_id}/connection-config (the
endpoint that returns decrypted FTP/SFTP credentials to the source-server
agent) -- see app/models/agent_credential_access_log.py,
app/core/audit.py, app/core/auth.py::require_connection_config_key, and
app/routers/agents.py::get_agent_connection_config.

`server_id` is deliberately NOT a ForeignKeyConstraint, unlike every other
table in this schema -- see the model's class docstring for why (this
project runs with PRAGMA foreign_keys=ON, and this table must still be
able to log an access attempt against a server_id that does not exist,
outcome="not_found").

No `updated_at` column (unlike almost every other table, which uses
TimestampMixin) -- this table is insert-only, see the model docstring.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_credential_access_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Deliberately no ForeignKeyConstraint -- see module docstring.
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column(
            "accessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("requester_ip", sa.String(length=64), nullable=True),
        sa.Column(
            "auth_method",
            sa.Enum(
                "connection_config_key", "admin_jwt",
                name="agentcredentialaccessauthmethod", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("admin_username", sa.String(length=255), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(
                "success", "denied_disabled", "denied_deleted", "denied_no_credentials",
                "not_found", "unauthorized",
                name="agentcredentialaccessoutcome", native_enum=False,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_credential_access_logs")),
    )
    op.create_index(
        "ix_agent_credential_access_logs_server_id",
        "agent_credential_access_logs",
        ["server_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_credential_access_logs_accessed_at",
        "agent_credential_access_logs",
        ["accessed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("agent_credential_access_logs")
