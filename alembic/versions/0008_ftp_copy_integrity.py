"""FTP copy-integrity: verification_runs.verification_type + alerts.backup_record_id

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-10 00:00:00.000000

Two independent changes:

1. verification_runs.verification_type -- a plain nullable-with-default ADD
   COLUMN (no batch mode required: SQLite allows ALTER TABLE ADD COLUMN
   NOT NULL as long as a constant DEFAULT is given, and we are not adding
   any CHECK constraint here). All pre-existing rows backfill to
   'RESTORE_VERIFYONLY' via server_default, which is correct since every
   row created before this migration came from the RESTORE VERIFYONLY
   flow (app/workers/backup_verification.py) -- see
   app/models/verification_run.py.

2. alerts.backup_record_id -- a full table rebuild (rename -> create ->
   copy -> drop), NOT op.batch_alter_table(recreate="always"). SQLite
   cannot ALTER an existing CHECK constraint, and Alembic's SQLite batch
   mode has no supported way to change an existing Computed() column's
   expression (needed here: entity_key's COALESCE must grow from 5 to 6
   FK columns) -- relying on batch mode's automatic table-reflection+
   recreate for this specific combination (CHECK + Computed, both
   changing) is not safe. Instead this uses op.create_table(...) against a
   shadow table name ("alerts_new"), the same sa.Column/sa.CheckConstraint
   /sa.Computed/sa.ForeignKeyConstraint building blocks 0001 used for the
   original alerts table, then op.execute(...) to copy rows (explicit
   column list; entity_key is NOT copied -- it recomputes from the
   COALESCE expression), op.drop_table + op.rename_table to swap it in,
   then op.create_index(...) to recreate every index (DROP TABLE drops
   all indexes tied to the old table; they must be explicitly recreated
   against the renamed table).

   IMPORTANT (op.f() usage is NOT uniform across constraint types here --
   verified empirically against the real 0001-created "alerts" table
   before finalizing this migration, see below):

   For ForeignKeyConstraint / PrimaryKeyConstraint, passing an explicit
   plain-string `name=` bypasses the naming convention entirely (the
   convention only ever fires when `name` is left as None) -- so those are
   spelled out below as plain literal strings copied verbatim from
   0001_initial_schema.py (plus one new FK name), deliberately NOT wrapped
   in op.f(), since op.f()-derived names computed from a template would
   bake in "alerts_new" (the shadow table's name at creation time) into
   the constraint's stored name, e.g. "fk_alerts_new_...", and SQLite's
   ALTER TABLE ... RENAME TO does NOT rewrite constraint names embedded in
   the table's stored CREATE TABLE SQL, only table-name references.

   For CheckConstraint specifically, SQLAlchemy's naming convention
   documented behavior is different and easy to get wrong: even when an
   explicit `name=` is given, it is NOT used as the final name outright --
   it is instead fed into the convention template as the `constraint_name`
   token (e.g. "ck_%(table_name)s_%(constraint_name)s"), so a plain string
   like name="ck_alerts_entity_type_valid" against a table literally named
   "alerts_new" at creation time renders as
   "ck_alerts_new_ck_alerts_entity_type_valid" -- confirmed empirically
   while writing this migration (a plain string was NOT sufficient here,
   unlike the FK/PK case above). The fix is to wrap the two CheckConstraint
   names in op.f(...), which marks the given string as already fully
   resolved (bypasses the convention/template substitution entirely, using
   the string exactly as given regardless of the table's name at creation
   time) -- so op.f("ck_alerts_entity_type_valid") reliably renders as
   exactly "ck_alerts_entity_type_valid" whether the table is named
   "alerts_new" or "alerts".

   Also documents a new AlertType.FTP_COPY_INTEGRITY_FAILED enum literal
   -- no separate DDL required (native_enum=False means Enum columns
   compile to plain VARCHAR with no CHECK, so a new Python-side string
   literal is sufficient).

   RESIDUAL RISK (SQLite foreign_keys pragma): grep over app/models
   confirms NO other table has a foreign key pointing at alerts.id, so
   DROP TABLE alerts cannot violate any child row's FK regardless of
   pragma state; the INSERT INTO alerts_new ... SELECT FROM alerts step
   only ever copies already-existing, already-valid FK values verbatim --
   it introduces no new or changed FK value. Verified empirically via the
   seeded-data migration test (tests/test_migration_0008.py), which
   asserts every pre-existing row's FK values survive byte-for-byte, not
   just that the table exists.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------- verification_runs ---
    op.add_column(
        "verification_runs",
        sa.Column(
            "verification_type",
            sa.Enum(
                "RESTORE_VERIFYONLY", "FTP_COPY_INTEGRITY",
                name="verificationtype", native_enum=False,
            ),
            server_default="RESTORE_VERIFYONLY",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_verification_runs_verification_type",
        "verification_runs",
        ["verification_type"],
        unique=False,
    )

    # ------------------------------------------------------- alerts -----
    op.create_table(
        "alerts_new",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "alert_type",
            sa.Enum(
                "JOB_FAILED", "JOB_MISSED", "JOB_TIMEOUT", "VERIFICATION_FAILED",
                "BACKUP_VERIFICATION_FAILED", "FTP_COPY_INTEGRITY_FAILED",
                "DISK_SPACE_LOW", "DISK_SPACE_CRITICAL", "SERVER_UNREACHABLE",
                "AGENT_OFFLINE", "RESTORE_FAILED",
                name="alerttype", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("INFO", "WARNING", "CRITICAL", name="alertseverity", native_enum=False),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("disk_id", sa.Integer(), nullable=True),
        sa.Column("backup_job_id", sa.Integer(), nullable=True),
        sa.Column("job_run_id", sa.Integer(), nullable=True),
        sa.Column("restore_operation_id", sa.Integer(), nullable=True),
        sa.Column("backup_record_id", sa.Integer(), nullable=True),  # NEW
        sa.Column(
            "entity_key",
            sa.Integer(),
            sa.Computed(
                "COALESCE(server_id, disk_id, backup_job_id, job_run_id, "
                "restore_operation_id, backup_record_id)",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "ACKNOWLEDGED", "RESOLVED", name="alertstatus", native_enum=False),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("TELEGRAM", "WEB", "BOTH", name="alertchannel", native_enum=False),
            server_default="BOTH",
            nullable=False,
        ),
        sa.Column("delivered_telegram_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_web_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('server','disk','backup_job','job_run','restore_operation','backup_record')",
            name=op.f("ck_alerts_entity_type_valid"),  # op.f() REQUIRED for CheckConstraint -- see module docstring
        ),
        sa.CheckConstraint(
            "(CASE WHEN server_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN disk_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN backup_job_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN job_run_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN restore_operation_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN backup_record_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
            name=op.f("ck_alerts_at_most_one_entity_fk"),  # op.f() REQUIRED for CheckConstraint
        ),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], name="fk_alerts_server_id_servers", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["disk_id"], ["disks.id"], name="fk_alerts_disk_id_disks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["backup_job_id"], ["backup_jobs.id"], name="fk_alerts_backup_job_id_backup_jobs", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_run_id"], ["job_runs.id"], name="fk_alerts_job_run_id_job_runs", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["restore_operation_id"], ["restore_operations.id"], name="fk_alerts_restore_operation_id_restore_operations", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["backup_record_id"], ["backup_records.id"], name="fk_alerts_backup_record_id_backup_records", ondelete="SET NULL"),  # NEW
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),  # literal, NOT op.f()
    )

    op.execute(
        "INSERT INTO alerts_new ("
        "  id, alert_type, severity, entity_type,"
        "  server_id, disk_id, backup_job_id, job_run_id, restore_operation_id, backup_record_id,"
        "  title, message, status, channel,"
        "  delivered_telegram_at, delivered_web_at,"
        "  acknowledged_by, acknowledged_at, resolved_at, resolved_note,"
        "  created_at, updated_at"
        ") "
        "SELECT"
        "  id, alert_type, severity, entity_type,"
        "  server_id, disk_id, backup_job_id, job_run_id, restore_operation_id, NULL,"
        "  title, message, status, channel,"
        "  delivered_telegram_at, delivered_web_at,"
        "  acknowledged_by, acknowledged_at, resolved_at, resolved_note,"
        "  created_at, updated_at "
        "FROM alerts"
    )

    op.drop_table("alerts")
    op.rename_table("alerts_new", "alerts")

    op.create_index("ix_alerts_status", "alerts", ["status"], unique=False)
    op.create_index("ix_alerts_severity", "alerts", ["severity"], unique=False)
    op.create_index("ix_alerts_server_id", "alerts", ["server_id"], unique=False)
    op.create_index("ix_alerts_disk_id", "alerts", ["disk_id"], unique=False)
    op.create_index("ix_alerts_backup_job_id", "alerts", ["backup_job_id"], unique=False)
    op.create_index("ix_alerts_job_run_id", "alerts", ["job_run_id"], unique=False)
    op.create_index("ix_alerts_restore_operation_id", "alerts", ["restore_operation_id"], unique=False)
    op.create_index("ix_alerts_backup_record_id", "alerts", ["backup_record_id"], unique=False)  # NEW
    op.create_index("ix_alerts_status_severity", "alerts", ["status", "severity"], unique=False)
    op.create_index(
        "uq_alerts_active_dedupe",
        "alerts",
        ["entity_type", "entity_key", "alert_type"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    # ------------------------------------------------------- alerts -----
    # NOTE: this INSERT will raise IntegrityError (CHECK constraint
    # violation on entity_type_valid, which reverts to the 5-value list
    # without 'backup_record') if ANY alert row has entity_type=
    # 'backup_record' at downgrade time. This is intentional: fail loudly
    # rather than silently discarding those rows' entity linkage. An
    # operator downgrading past this revision must first resolve/delete
    # any FTP_COPY_INTEGRITY_FAILED alerts (entity_type='backup_record').
    op.create_table(
        "alerts_old",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "alert_type",
            sa.Enum(
                "JOB_FAILED", "JOB_MISSED", "JOB_TIMEOUT", "VERIFICATION_FAILED",
                "BACKUP_VERIFICATION_FAILED", "DISK_SPACE_LOW", "DISK_SPACE_CRITICAL",
                "SERVER_UNREACHABLE", "AGENT_OFFLINE", "RESTORE_FAILED",
                name="alerttype", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("INFO", "WARNING", "CRITICAL", name="alertseverity", native_enum=False),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("disk_id", sa.Integer(), nullable=True),
        sa.Column("backup_job_id", sa.Integer(), nullable=True),
        sa.Column("job_run_id", sa.Integer(), nullable=True),
        sa.Column("restore_operation_id", sa.Integer(), nullable=True),
        sa.Column(
            "entity_key", sa.Integer(),
            sa.Computed(
                "COALESCE(server_id, disk_id, backup_job_id, job_run_id, restore_operation_id)",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum("ACTIVE", "ACKNOWLEDGED", "RESOLVED", name="alertstatus", native_enum=False), server_default="ACTIVE", nullable=False),
        sa.Column("channel", sa.Enum("TELEGRAM", "WEB", "BOTH", name="alertchannel", native_enum=False), server_default="BOTH", nullable=False),
        sa.Column("delivered_telegram_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_web_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('server','disk','backup_job','job_run','restore_operation')",
            name=op.f("ck_alerts_entity_type_valid"),  # op.f() REQUIRED for CheckConstraint -- see module docstring
        ),
        sa.CheckConstraint(
            "(CASE WHEN server_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN disk_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN backup_job_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN job_run_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN restore_operation_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
            name=op.f("ck_alerts_at_most_one_entity_fk"),  # op.f() REQUIRED for CheckConstraint
        ),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], name="fk_alerts_server_id_servers", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["disk_id"], ["disks.id"], name="fk_alerts_disk_id_disks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["backup_job_id"], ["backup_jobs.id"], name="fk_alerts_backup_job_id_backup_jobs", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_run_id"], ["job_runs.id"], name="fk_alerts_job_run_id_job_runs", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["restore_operation_id"], ["restore_operations.id"], name="fk_alerts_restore_operation_id_restore_operations", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
    )

    op.execute(
        "INSERT INTO alerts_old ("
        "  id, alert_type, severity, entity_type,"
        "  server_id, disk_id, backup_job_id, job_run_id, restore_operation_id,"
        "  title, message, status, channel,"
        "  delivered_telegram_at, delivered_web_at,"
        "  acknowledged_by, acknowledged_at, resolved_at, resolved_note,"
        "  created_at, updated_at"
        ") "
        "SELECT"
        "  id, alert_type, severity, entity_type,"
        "  server_id, disk_id, backup_job_id, job_run_id, restore_operation_id,"
        "  title, message, status, channel,"
        "  delivered_telegram_at, delivered_web_at,"
        "  acknowledged_by, acknowledged_at, resolved_at, resolved_note,"
        "  created_at, updated_at "
        "FROM alerts"
    )

    op.drop_table("alerts")
    op.rename_table("alerts_old", "alerts")

    op.create_index("ix_alerts_status", "alerts", ["status"], unique=False)
    op.create_index("ix_alerts_severity", "alerts", ["severity"], unique=False)
    op.create_index("ix_alerts_server_id", "alerts", ["server_id"], unique=False)
    op.create_index("ix_alerts_disk_id", "alerts", ["disk_id"], unique=False)
    op.create_index("ix_alerts_backup_job_id", "alerts", ["backup_job_id"], unique=False)
    op.create_index("ix_alerts_job_run_id", "alerts", ["job_run_id"], unique=False)
    op.create_index("ix_alerts_restore_operation_id", "alerts", ["restore_operation_id"], unique=False)
    op.create_index("ix_alerts_status_severity", "alerts", ["status", "severity"], unique=False)
    op.create_index(
        "uq_alerts_active_dedupe", "alerts", ["entity_type", "entity_key", "alert_type"],
        unique=True, sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    # ---------------------------------------------- verification_runs ---
    with op.batch_alter_table("verification_runs") as batch_op:
        batch_op.drop_index("ix_verification_runs_verification_type")
        batch_op.drop_column("verification_type")
