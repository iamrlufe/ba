"""server_metrics table + servers.monitored_service_names column

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-11 00:00:00.000000

Adds the server_metrics table (one row per Server, upserted from
AgentHeartbeatRequest.metrics/services on each heartbeat -- see
app/models/server_metrics.py, app/routers/agents.py::agent_heartbeat) and
the servers.monitored_service_names nullable JSON override column (see
app/models/server.py -- NULL means "use Settings.DEFAULT_MONITORED_SERVICE_NAMES",
non-NULL, including [], replaces the global default wholesale, no merge).
No backfill required for either change: server_metrics starts empty (rows
created lazily on first qualifying heartbeat), and monitored_service_names
defaults to NULL for all existing servers (== "use global default", the
correct behavior for pre-existing rows with no explicit configuration).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "server_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("cpu_usage_pct", sa.Float(), nullable=True),
        sa.Column("memory_used_bytes", sa.BigInteger(), nullable=True),
        sa.Column("memory_total_bytes", sa.BigInteger(), nullable=True),
        sa.Column("top_processes", sa.JSON(), nullable=True),
        sa.Column("services_status", sa.JSON(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_server_metrics_server_id_servers"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_metrics")),
        sa.UniqueConstraint("server_id", name="uq_server_metrics_server_id"),
    )
    op.add_column("servers", sa.Column("monitored_service_names", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("servers", "monitored_service_names")
    op.drop_table("server_metrics")
