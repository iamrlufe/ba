from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ServerMetrics(TimestampMixin, Base):
    """Latest-known snapshot of CPU/memory/top-processes/service-status for
    a Server, as last reported by that server's agent heartbeat. Snapshot
    only -- overwritten in place every heartbeat, no history/time-series.
    One row per Server, created lazily on the first heartbeat that reports
    `metrics` and/or `services` (not eagerly on Server creation) -- see
    app/routers/agents.py::agent_heartbeat.
    """

    __tablename__ = "server_metrics"
    __table_args__ = (
        UniqueConstraint("server_id", name="uq_server_metrics_server_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    cpu_usage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_used_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    memory_total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    top_processes: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    services_status: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    server: Mapped["Server"] = relationship("Server")
