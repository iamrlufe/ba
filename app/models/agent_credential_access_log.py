from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import AgentCredentialAccessAuthMethod, AgentCredentialAccessOutcome


class AgentCredentialAccessLog(Base):
    """Insert-only audit trail of every call to
    `GET /api/agents/{server_id}/connection-config` (app/routers/agents.py),
    the endpoint that returns decrypted FTP/SFTP credentials to the
    source-server agent. One row is written per call, for every outcome
    (success AND every denial path) -- see that endpoint's docstring.

    `server_id` is deliberately NOT a real (DB-enforced) ForeignKey to
    `servers.id`, unlike every other *_id column in this codebase (see
    e.g. app/models/alert.py). This project runs with
    `PRAGMA foreign_keys=ON` (app/core/db.py), so a real FK constraint
    would make it impossible to log an access attempt against a
    server_id that does not exist (outcome="not_found") -- and logging
    exactly that attempt is the whole point of this table. `server_id` is
    still NOT NULL (every access attempt is against some path-parameter
    server_id, even a bogus one) -- only the DB-level FK *constraint* is
    intentionally omitted, not the column/semantics.

    No `updated_at` (unlike the TimestampMixin used by almost every other
    model in this codebase) -- this table is INSERT-only, there is no
    update/delete path, so a mutable `updated_at` column would be
    misleading. `accessed_at` is the single, immutable timestamp.

    Never stores decrypted credential values, the raw X-Connection-Config-Key
    header value, or any other secret -- only access metadata.
    """

    __tablename__ = "agent_credential_access_logs"
    __table_args__ = (
        Index("ix_agent_credential_access_logs_server_id", "server_id"),
        Index("ix_agent_credential_access_logs_accessed_at", "accessed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # See class docstring: intentionally not a ForeignKey(...) column.
    server_id: Mapped[int] = mapped_column(Integer, nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    requester_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auth_method: Mapped[AgentCredentialAccessAuthMethod] = mapped_column(
        Enum(
            AgentCredentialAccessAuthMethod,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )
    # Populated only when auth_method == ADMIN_JWT (regardless of outcome --
    # including the "unauthorized" outcome for a non-admin JWT, so the audit
    # trail records who attempted access even when access was denied).
    admin_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[AgentCredentialAccessOutcome] = mapped_column(
        Enum(
            AgentCredentialAccessOutcome,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )
