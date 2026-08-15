"""Round-trip regression test for
alembic/versions/0012_job_run_cancel_dispatch_stuck_pending.py.

Mirrors tests/test_migration_0008.py's subprocess-invocation style
(`alembic upgrade/downgrade <rev>` via `python -m alembic`, against a fresh
temp-file SQLite database, DATABASE_URL/FERNET_KEY passed through the
subprocess env) -- 0012 is a purely-additive migration (plain
op.add_column calls, no table rebuild), so this test just confirms
`upgrade head` -> `downgrade -1` -> `upgrade head` round-trips cleanly and
that every new column is present/absent at the right points, per item 7 in
the task spec.

NOTE: `head` is now "0013" (alembic/versions/0013_backup_job_remote_directory_override.py,
purely additive on top of 0012 -- adds backup_jobs.remote_directory_override).
The two `head`-revision assertions below were updated from "0012" to "0013"
accordingly; this is expected collateral of a later migration landing on
top of this one, not a defect in 0012 or 0013.  `_run_alembic("downgrade", "-1", ...)`
from head now lands on 0012 (not 0011), so the round-trip test's
intermediate assertions were adjusted to check the 0013-specific column
disappears/reappears at that same step, while still exercising 0012's own
columns as before.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parent.parent

_NEW_JOB_RUNS_COLUMNS = {
    "dispatched_at",
    "cancel_requested_at",
    "cancel_requested_by",
    "cancel_acknowledged_at",
}
_NEW_BACKUP_JOBS_COLUMNS = {"pending_to_running_grace_minutes"}
# Added by 0013 (alembic/versions/0013_backup_job_remote_directory_override.py),
# one revision past 0012 -- checked separately since it only exists from
# 0013 onward (not yet present right after 0012 alone).
_NEW_BACKUP_JOBS_COLUMNS_0013 = {"remote_directory_override"}


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


def _columns(db_file: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_file))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_upgrade_head_adds_expected_columns(tmp_path):
    db_file = tmp_path / "upgrade_head_0012.db"
    _run_alembic("upgrade", "head", db_file=db_file)

    job_runs_cols = _columns(db_file, "job_runs")
    backup_jobs_cols = _columns(db_file, "backup_jobs")

    assert _NEW_JOB_RUNS_COLUMNS <= job_runs_cols
    assert _NEW_BACKUP_JOBS_COLUMNS <= backup_jobs_cols
    # head is now 0013, one revision past 0012 -- its own column must also
    # be present at head.
    assert _NEW_BACKUP_JOBS_COLUMNS_0013 <= backup_jobs_cols

    conn = sqlite3.connect(str(db_file))
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()
    # head moved from "0012" to "0013" when
    # alembic/versions/0013_backup_job_remote_directory_override.py landed
    # on top of 0012 -- expected collateral of that later migration, not a
    # defect in 0012 itself (see module docstring).
    assert version == "0013"


def test_upgrade_head_downgrade_minus_one_upgrade_head_round_trips_cleanly(tmp_path):
    db_file = tmp_path / "roundtrip_0012.db"

    _run_alembic("upgrade", "head", db_file=db_file)
    assert _NEW_JOB_RUNS_COLUMNS <= _columns(db_file, "job_runs")
    assert _NEW_BACKUP_JOBS_COLUMNS <= _columns(db_file, "backup_jobs")
    assert _NEW_BACKUP_JOBS_COLUMNS_0013 <= _columns(db_file, "backup_jobs")

    # "downgrade -1" from head (0013) now lands on 0012, not 0011 -- 0013's
    # own column must disappear, while 0012's (and earlier's) columns must
    # still be present since only the single most-recent revision was
    # reverted.
    _run_alembic("downgrade", "-1", db_file=db_file)
    job_runs_cols_after_downgrade = _columns(db_file, "job_runs")
    backup_jobs_cols_after_downgrade = _columns(db_file, "backup_jobs")
    assert _NEW_JOB_RUNS_COLUMNS <= job_runs_cols_after_downgrade
    assert _NEW_BACKUP_JOBS_COLUMNS <= backup_jobs_cols_after_downgrade
    assert _NEW_BACKUP_JOBS_COLUMNS_0013.isdisjoint(backup_jobs_cols_after_downgrade)

    conn = sqlite3.connect(str(db_file))
    try:
        version_after_downgrade = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()
    assert version_after_downgrade == "0012"

    _run_alembic("downgrade", "-1", db_file=db_file)
    job_runs_cols_after_second_downgrade = _columns(db_file, "job_runs")
    backup_jobs_cols_after_second_downgrade = _columns(db_file, "backup_jobs")
    assert _NEW_JOB_RUNS_COLUMNS.isdisjoint(job_runs_cols_after_second_downgrade)
    assert _NEW_BACKUP_JOBS_COLUMNS.isdisjoint(backup_jobs_cols_after_second_downgrade)

    conn = sqlite3.connect(str(db_file))
    try:
        version_after_second_downgrade = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
    finally:
        conn.close()
    assert version_after_second_downgrade == "0011"

    _run_alembic("upgrade", "head", db_file=db_file)
    assert _NEW_JOB_RUNS_COLUMNS <= _columns(db_file, "job_runs")
    assert _NEW_BACKUP_JOBS_COLUMNS <= _columns(db_file, "backup_jobs")
    assert _NEW_BACKUP_JOBS_COLUMNS_0013 <= _columns(db_file, "backup_jobs")

    conn = sqlite3.connect(str(db_file))
    try:
        version_after_reupgrade = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()
    assert version_after_reupgrade == "0013"


def test_downgrade_preserves_pre_existing_job_run_and_backup_job_rows(tmp_path):
    """Purely-additive migration: existing rows (and their other column
    values) must survive an upgrade -> downgrade -> upgrade cycle
    byte-for-byte, since 0012 never rebuilds either table."""
    db_file = tmp_path / "roundtrip_data_0012.db"

    _run_alembic("upgrade", "0011", db_file=db_file)
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO servers (id, name, host, port, protocol) VALUES (1, 's', 'h', 21, 'FTP')"
        )
        conn.execute(
            "INSERT INTO disks (id, server_id, label, mount_path) VALUES (1, 1, 'd', '/mnt/d')"
        )
        conn.execute(
            "INSERT INTO backup_jobs (id, server_id, disk_id, name, source_path, schedule_cron) "
            "VALUES (1, 1, 1, 'job-one', '/src', '0 * * * *')"
        )
        conn.execute(
            "INSERT INTO job_runs (id, backup_job_id, status, triggered_by) "
            "VALUES (1, 1, 'PENDING', 'manual')"
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic("upgrade", "head", db_file=db_file)
    _run_alembic("downgrade", "-1", db_file=db_file)
    _run_alembic("upgrade", "head", db_file=db_file)

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        job = conn.execute("SELECT * FROM backup_jobs WHERE id = 1").fetchone()
        assert job["name"] == "job-one"
        assert job["pending_to_running_grace_minutes"] == 30

        run = conn.execute("SELECT * FROM job_runs WHERE id = 1").fetchone()
        assert run["status"] == "PENDING"
        assert run["triggered_by"] == "manual"
        assert run["dispatched_at"] is None
        assert run["cancel_requested_at"] is None
        assert run["cancel_requested_by"] is None
        assert run["cancel_acknowledged_at"] is None
    finally:
        conn.close()
