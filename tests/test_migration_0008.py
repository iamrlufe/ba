"""Seeded-data regression test for alembic/versions/0008_ftp_copy_integrity.py
-- THE single most important test in this feature (explicit user
requirement).

Mirrors tests/test_migration.py's subprocess-invocation style (`alembic
upgrade <rev>` via `python -m alembic`, against a fresh temp-file SQLite
database, DATABASE_URL/FERNET_KEY passed through the subprocess env).

Strategy: run `alembic upgrade 0007` (pre-rebuild schema) via subprocess,
seed the `alerts` table (and one `verification_runs` row) directly via raw
`sqlite3` (bypassing the ORM entirely, matching what a real pre-0008
database would contain), then run `alembic upgrade 0008` via a second
subprocess call, then reconnect via raw `sqlite3` and assert every
pre-existing row's original column values survived byte-for-byte, that
`backup_record_id IS NULL` for all of them (0008's INSERT hardcodes NULL
for that new column), and that `entity_key` was correctly recomputed by
the rebuilt table's Computed() column.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_alembic(*args: str, db_file: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"
    env["FERNET_KEY"] = Fernet.generate_key().decode("utf-8")
    env["SQL_ECHO"] = "false"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


# --------------------------------------------------------------------------
# Seed data: 6 alert rows covering each of the 5 pre-existing FK columns set
# individually, plus one fully-NULL row. Varies id/created_at/updated_at/
# status/title/message across rows so assertions are meaningful (not just
# "the row still exists").
# --------------------------------------------------------------------------

_SEED_ALERTS = [
    # (id, alert_type, severity, entity_type, server_id, disk_id, backup_job_id,
    #  job_run_id, restore_operation_id, title, message, status, channel,
    #  created_at, updated_at)
    (
        101, "SERVER_UNREACHABLE", "CRITICAL", "server", 501, None, None, None, None,
        "server alert", "server unreachable message", "ACTIVE", "BOTH",
        "2026-01-01 00:00:00", "2026-01-01 00:00:00",
    ),
    (
        102, "DISK_SPACE_LOW", "WARNING", "disk", None, 601, None, None, None,
        "disk alert", "disk space low message", "RESOLVED", "WEB",
        "2026-01-02 00:00:00", "2026-01-02 01:00:00",
    ),
    (
        103, "JOB_FAILED", "CRITICAL", "backup_job", None, None, 701, None, None,
        "job alert", "backup job failed message", "ACTIVE", "TELEGRAM",
        "2026-01-03 00:00:00", "2026-01-03 00:00:00",
    ),
    (
        104, "JOB_MISSED", "WARNING", "job_run", None, None, None, 801, None,
        "job run alert", "job run missed message", "ACKNOWLEDGED", "BOTH",
        "2026-01-04 00:00:00", "2026-01-04 02:00:00",
    ),
    (
        105, "RESTORE_FAILED", "CRITICAL", "restore_operation", None, None, None, None, 901,
        "restore alert", "restore operation failed message", "ACTIVE", "BOTH",
        "2026-01-05 00:00:00", "2026-01-05 00:00:00",
    ),
    (
        106, "AGENT_OFFLINE", "WARNING", "server", None, None, None, None, None,
        "orphaned alert", "parent entity already deleted (all FKs NULL)", "RESOLVED", "TELEGRAM",
        "2026-01-06 00:00:00", "2026-01-06 03:00:00",
    ),
]


def _seed_alerts_and_verification_run(db_file: Path) -> None:
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")  # seeding FK targets not required for this test
        for row in _SEED_ALERTS:
            conn.execute(
                "INSERT INTO alerts ("
                "  id, alert_type, severity, entity_type, server_id, disk_id, backup_job_id,"
                "  job_run_id, restore_operation_id, title, message, status, channel,"
                "  created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )

        # One pre-0008 verification_runs row (no verification_type column
        # exists yet at this schema revision -- it must backfill to
        # RESTORE_VERIFYONLY after 0008 runs). Needs a real backup_jobs row
        # to satisfy the FK.
        conn.execute(
            "INSERT INTO servers (id, name, host, port, protocol) VALUES (1, 's', 'h', 21, 'FTP')"
        )
        conn.execute(
            "INSERT INTO disks (id, server_id, label, mount_path) VALUES (1, 1, 'd', '/mnt/d')"
        )
        conn.execute(
            "INSERT INTO backup_jobs (id, server_id, disk_id, name, source_path, schedule_cron) "
            "VALUES (1, 1, 1, 'job', '/src', '0 * * * *')"
        )
        conn.execute(
            "INSERT INTO verification_runs (id, backup_job_id, triggered_by, status) "
            "VALUES (1, 1, 'scheduler', 'OK')"
        )
        conn.commit()
    finally:
        conn.close()


def test_seeded_alert_rows_survive_0008_rebuild_byte_for_byte(tmp_path):
    db_file = tmp_path / "seeded_0008.db"

    _run_alembic("upgrade", "0007", db_file=db_file)
    _seed_alerts_and_verification_run(db_file)
    _run_alembic("upgrade", "0008", db_file=db_file)

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        for expected in _SEED_ALERTS:
            (
                id_, alert_type, severity, entity_type, server_id, disk_id, backup_job_id,
                job_run_id, restore_operation_id, title, message, status, channel,
                created_at, updated_at,
            ) = expected

            row = conn.execute("SELECT * FROM alerts WHERE id = ?", (id_,)).fetchone()
            assert row is not None, f"alert {id_} missing after 0008 upgrade"

            assert row["alert_type"] == alert_type
            assert row["severity"] == severity
            assert row["entity_type"] == entity_type
            assert row["server_id"] == server_id
            assert row["disk_id"] == disk_id
            assert row["backup_job_id"] == backup_job_id
            assert row["job_run_id"] == job_run_id
            assert row["restore_operation_id"] == restore_operation_id
            assert row["title"] == title
            assert row["message"] == message
            assert row["status"] == status
            assert row["channel"] == channel
            assert row["created_at"] == created_at
            assert row["updated_at"] == updated_at

            # New column: always NULL for pre-existing rows.
            assert row["backup_record_id"] is None

            # entity_key recomputed via the rebuilt Computed() column --
            # COALESCE of the (now six) FK columns.
            expected_entity_key = next(
                (v for v in (server_id, disk_id, backup_job_id, job_run_id, restore_operation_id, None)
                 if v is not None),
                None,
            )
            assert row["entity_key"] == expected_entity_key

        # verification_runs backfill.
        vr = conn.execute("SELECT verification_type FROM verification_runs WHERE id = 1").fetchone()
        assert vr["verification_type"] == "RESTORE_VERIFYONLY"
    finally:
        conn.close()


def test_upgrade_downgrade_upgrade_round_trip_no_errors(tmp_path):
    db_file = tmp_path / "roundtrip_0008.db"

    _run_alembic("upgrade", "0007", db_file=db_file)
    _seed_alerts_and_verification_run(db_file)
    _run_alembic("upgrade", "0008", db_file=db_file)
    _run_alembic("downgrade", "0007", db_file=db_file)
    _run_alembic("upgrade", "0008", db_file=db_file)

    conn = sqlite3.connect(str(db_file))
    try:
        count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert count == len(_SEED_ALERTS)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
        assert "backup_record_id" in cols
        vt_cols = {row[1] for row in conn.execute("PRAGMA table_info(verification_runs)").fetchall()}
        assert "verification_type" in vt_cols
    finally:
        conn.close()


def test_downgrade_with_backup_record_entity_type_alert_fails_loudly(tmp_path):
    """Confirms the fail-loud-on-downgrade behavior documented in 0008's
    downgrade() docstring is real, not just documented: an alert row with
    entity_type='backup_record' must make the downgrade's INSERT INTO
    alerts_old ... SELECT fail with a CHECK constraint violation (the
    5-value entity_type_valid CHECK reverts to excluding 'backup_record'),
    rather than silently dropping that row's entity linkage.
    """
    db_file = tmp_path / "downgrade_guard_0008.db"

    _run_alembic("upgrade", "head", db_file=db_file)

    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO alerts ("
            "  id, alert_type, severity, entity_type, backup_record_id, title, message"
            ") VALUES (201, 'FTP_COPY_INTEGRITY_FAILED', 'CRITICAL', 'backup_record', 999, 't', 'm')"
        )
        conn.commit()
    finally:
        conn.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"
    env["FERNET_KEY"] = Fernet.generate_key().decode("utf-8")
    env["SQL_ECHO"] = "false"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0007"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0, "downgrade should fail loudly when a backup_record alert exists"
    assert "CHECK constraint failed" in (result.stdout + result.stderr)


_EXPECTED_ALERTS_CONSTRAINT_NAMES = {
    "pk_alerts",
    "ck_alerts_entity_type_valid",
    "ck_alerts_at_most_one_entity_fk",
    "fk_alerts_server_id_servers",
    "fk_alerts_disk_id_disks",
    "fk_alerts_backup_job_id_backup_jobs",
    "fk_alerts_job_run_id_job_runs",
    "fk_alerts_restore_operation_id_restore_operations",
    "fk_alerts_backup_record_id_backup_records",
}


def _alerts_constraint_names(db_file: Path) -> set[str]:
    """Constraint names as SQLite actually stored them, parsed out of the
    real `CREATE TABLE` SQL via `sqlite_master` -- not what the Python code
    intended, but what's really on disk. Catches the exact class of bug
    0008's `op.f()`-only-on-CheckConstraint fix is meant to prevent: a
    naming-convention reprocessing pass silently doubling/mangling a
    constraint name (e.g. producing `ck_alerts_new_ck_alerts_...` instead of
    `ck_alerts_...`) would surface here as a missing expected name.
    """
    conn = sqlite3.connect(str(db_file))
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'"
        ).fetchone()[0]
        names = set(re.findall(r"CONSTRAINT (\S+)", sql))
        names |= set(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='alerts' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        return names
    finally:
        conn.close()


def test_alerts_table_has_exact_expected_constraint_names_after_fresh_upgrade(tmp_path):
    """Regression guard for the op.f()-on-CheckConstraint-only fix (see the
    module docstring in alembic/versions/0008_ftp_copy_integrity.py):
    SQLAlchemy's naming convention reprocesses CheckConstraint.name even
    when given explicitly (unlike ForeignKeyConstraint/PrimaryKeyConstraint),
    so a plain literal string there would have produced a mangled name like
    `ck_alerts_new_ck_alerts_entity_type_valid`. This asserts the real,
    on-disk constraint names on a fresh `alembic upgrade head` match exactly
    what 0001_initial_schema.py's op.f()-derived names would produce for a
    table actually named `alerts` -- not a mangled shadow-table variant.
    """
    db_file = tmp_path / "constraint_names_fresh.db"
    _run_alembic("upgrade", "head", db_file=db_file)

    actual = _alerts_constraint_names(db_file)
    assert _EXPECTED_ALERTS_CONSTRAINT_NAMES <= actual, (
        f"missing expected constraint names: {_EXPECTED_ALERTS_CONSTRAINT_NAMES - actual}\n"
        f"actual names found: {actual}"
    )


def test_alerts_table_constraint_names_identical_before_and_after_round_trip(tmp_path):
    """The rebuild migration's constraint names must be stable across an
    upgrade -> downgrade -> upgrade cycle, not just present on a fresh
    install -- confirms the op.f() fix survives repeated table rebuilds,
    not just the first one.
    """
    fresh_db = tmp_path / "constraint_names_fresh2.db"
    _run_alembic("upgrade", "head", db_file=fresh_db)
    fresh_names = _alerts_constraint_names(fresh_db)

    roundtrip_db = tmp_path / "constraint_names_roundtrip.db"
    _run_alembic("upgrade", "0007", db_file=roundtrip_db)
    _run_alembic("upgrade", "0008", db_file=roundtrip_db)
    _run_alembic("downgrade", "0007", db_file=roundtrip_db)
    _run_alembic("upgrade", "0008", db_file=roundtrip_db)
    roundtrip_names = _alerts_constraint_names(roundtrip_db)

    assert roundtrip_names == fresh_names
