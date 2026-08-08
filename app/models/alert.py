from datetime import datetime

from sqlalchemy import CheckConstraint, Computed, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import AlertChannel, AlertSeverity, AlertStatus, AlertType

# Kept in sync (by hand) with alembic/versions/0001_initial_schema.py.
_ACTIVE_ALERT_WHERE = "status = 'ACTIVE'"

# `entity_key` collapses the five nullable per-entity FK columns into a
# single non-null(-ish) discriminant for uniqueness purposes. This is
# required because SQL NULL is never equal to NULL for UNIQUE index
# purposes: since any given row only ever has ONE of the five FK columns
# set (the other four are always NULL by design -- see class docstring),
# a naive composite UNIQUE index over all five FK columns would never
# actually detect duplicates (each row differs from every other row in at
# least 4 always-NULL columns). Kept in sync (by hand) with
# alembic/versions/0001_initial_schema.py.
_ENTITY_KEY_SQL = "COALESCE(server_id, disk_id, backup_job_id, job_run_id, restore_operation_id)"

_AT_MOST_ONE_ENTITY_FK_SQL = (
    "(CASE WHEN server_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN disk_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN backup_job_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN job_run_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN restore_operation_id IS NOT NULL THEN 1 ELSE 0 END) <= 1"
)


class Alert(TimestampMixin, Base):
    """A monitoring alert, linked to at most one entity via one of five FKs.

    Multi-FK (not polymorphic entity_type+entity_id) by design: gives real
    FK integrity/cascade behavior per entity type at the cost of a few
    always-null columns. All five FKs use ON DELETE SET NULL (never
    CASCADE) -- an Alert must never disappear just because its parent
    entity was deleted; it should instead end up with all FKs NULL and get
    resolved through the normal alert lifecycle.

    Because of ON DELETE SET NULL, "all five FKs NULL" is a legitimate
    state (parent entity deleted), so the CHECK constraint below enforces
    "at most one" FK set, not "exactly one".

    KNOWN LIMITATION (by design, not a bug): the `at_most_one_entity_fk`
    CHECK constraint only enforces that *at most one* of the five FK
    columns is non-NULL -- it does NOT verify that the one FK column which
    *is* set actually corresponds to `entity_type`. For example, a row
    with `entity_type='disk'` but `server_id` set (and `disk_id` NULL)
    will pass the CHECK constraint even though it's semantically wrong.
    SQLite CHECK constraints cannot express this "if entity_type=X then
    only column_X may be non-NULL" correlation without hardcoding every
    column name into the CHECK expression in a way that's brittle to
    maintain by hand; instead this consistency is guaranteed entirely by
    always going through the creation helper described below, never by
    inserting/updating rows ad hoc.

    TODO(CRUD layer, out of scope here): Alert rows must always be created
    through a single helper function that sets exactly one FK column based
    on `entity_type` (e.g. `create_alert(session, entity_type="disk",
    entity_id=..., ...)`), never by constructing `Alert(...)` ad hoc with
    FK columns set by hand in multiple places. This module intentionally
    does not implement that helper or any alert-detection logic, and the
    database itself cannot fully guarantee entity_type/FK consistency (see
    KNOWN LIMITATION above) -- that guarantee is the helper's
    responsibility.

    De-duplication of ACTIVE alerts is done via the generated column
    `entity_key` (see `_ENTITY_KEY_SQL` above) rather than a composite
    unique index directly over the five FK columns -- a straight
    multi-column UNIQUE index over `(server_id, disk_id, backup_job_id,
    job_run_id, restore_operation_id, ...)` would never fire, because SQL
    NULL is never considered equal to NULL for UNIQUE purposes and at
    least four of those five columns are always NULL on every row.
    `entity_key` collapses them into one non-NULL discriminant via
    COALESCE so the partial unique index `uq_alerts_active_dedupe` on
    `(entity_type, entity_key, alert_type) WHERE status='ACTIVE'` can
    actually detect duplicates.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_status", "status"),
        Index("ix_alerts_severity", "severity"),
        Index("ix_alerts_server_id", "server_id"),
        Index("ix_alerts_disk_id", "disk_id"),
        Index("ix_alerts_backup_job_id", "backup_job_id"),
        Index("ix_alerts_job_run_id", "job_run_id"),
        Index("ix_alerts_restore_operation_id", "restore_operation_id"),
        Index("ix_alerts_status_severity", "status", "severity"),
        CheckConstraint(
            "entity_type IN ('server','disk','backup_job','job_run','restore_operation')",
            name="entity_type_valid",
        ),
        CheckConstraint(_AT_MOST_ONE_ENTITY_FK_SQL, name="at_most_one_entity_fk"),
        # De-duplicate active alerts: at most one ACTIVE alert per
        # (entity_type, entity_key, alert_type). See `_ENTITY_KEY_SQL` /
        # class docstring for why this uses the generated `entity_key`
        # column instead of the five raw FK columns directly.
        Index(
            "uq_alerts_active_dedupe",
            "entity_type",
            "entity_key",
            "alert_type",
            unique=True,
            sqlite_where=text(_ACTIVE_ALERT_WHERE),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[AlertType] = mapped_column(
        Enum(AlertType, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)

    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), nullable=True
    )
    disk_id: Mapped[int | None] = mapped_column(
        ForeignKey("disks.id", ondelete="SET NULL"), nullable=True
    )
    backup_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("backup_jobs.id", ondelete="SET NULL"), nullable=True
    )
    job_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    restore_operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("restore_operations.id", ondelete="SET NULL"), nullable=True
    )

    # Generated (computed) column, not settable by application code -- see
    # `_ENTITY_KEY_SQL` / class docstring. Backs the `uq_alerts_active_dedupe`
    # partial unique index below.
    entity_key: Mapped[int | None] = mapped_column(
        Integer, Computed(_ENTITY_KEY_SQL, persisted=True), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=AlertStatus.ACTIVE,
        server_default=AlertStatus.ACTIVE.value,
    )
    channel: Mapped[AlertChannel] = mapped_column(
        Enum(AlertChannel, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=AlertChannel.BOTH,
        server_default=AlertChannel.BOTH.value,
    )
    delivered_telegram_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_web_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    server: Mapped["Server | None"] = relationship("Server")
    disk: Mapped["Disk | None"] = relationship("Disk")
    backup_job: Mapped["BackupJob | None"] = relationship("BackupJob")
    job_run: Mapped["JobRun | None"] = relationship("JobRun")
    restore_operation: Mapped["RestoreOperation | None"] = relationship("RestoreOperation")
