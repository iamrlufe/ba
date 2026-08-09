"""Regression test: `alembic upgrade head` must actually work end-to-end.

The rest of the suite creates tables via `Base.metadata.create_all` for
speed/isolation, which does NOT exercise alembic/versions/0001_initial_schema.py
at all. This test runs the real migration, in a subprocess, against a fresh
temp-file SQLite database, and checks that every expected table (plus the
partial unique indexes, which are hand-written raw-SQL in the migration and
easy to typo) actually exists afterwards.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {
    "servers",
    "sql_instances",
    "disks",
    "backup_jobs",
    "job_runs",
    "backup_records",
    "restore_operations",
    "alerts",
    "verification_runs",
    "alembic_version",
}

EXPECTED_PARTIAL_INDEXES = {
    "uq_job_runs_active_per_backup_job",
    "uq_restore_operations_active_per_instance_db",
    "uq_alerts_active_dedupe",
    "uq_verification_runs_active_per_backup_job",
}


def test_alembic_upgrade_head_creates_all_tables(tmp_path):
    db_file = tmp_path / "migration_regression.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    env["FERNET_KEY"] = Fernet.generate_key().decode("utf-8")
    env["SQL_ECHO"] = "false"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert db_file.exists(), "migration ran but did not create the sqlite db file"

    conn = sqlite3.connect(str(db_file))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert EXPECTED_TABLES.issubset(tables), f"missing tables: {EXPECTED_TABLES - tables}"

        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert EXPECTED_PARTIAL_INDEXES.issubset(indexes), (
            f"missing partial unique indexes: {EXPECTED_PARTIAL_INDEXES - indexes}"
        )
    finally:
        conn.close()
