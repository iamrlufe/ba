"""Tests for `app.workers.daily_summary.build_daily_summary`.

Calls the function directly against the `session` fixture (bound to the
in-memory test engine) -- mirrors `tests/test_alert_worker.py`'s
established pattern for worker-layer (non-HTTP) code.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import JobRunStatus
from app.workers.daily_summary import build_daily_summary
from tests.conftest import build_backup_job, build_disk, build_job_run, build_server

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


async def _enabled_job(session, **overrides):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    overrides.setdefault("is_enabled", True)
    job = build_backup_job(server.id, disk.id, **overrides)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def _status_for_latest_run(session, run_status: JobRunStatus) -> str:
    job = await _enabled_job(session)
    run = build_job_run(
        job.id,
        status=run_status,
        finished_at=NOW,
    )
    session.add(run)
    await session.commit()

    summary = await build_daily_summary(session, now=NOW)
    matches = [j for j in summary.jobs if j.backup_job_id == job.id]
    assert len(matches) == 1
    return matches[0].status


# --------------------------------------------------------------------------
# Reviewer fix 3: a job whose latest run is STUCK must classify as FAIL in
# the daily digest, same as FAILED/TIMEOUT -- a run that never even got
# dispatched to an agent is at least as much of an operational failure.
# --------------------------------------------------------------------------


async def test_daily_summary_stuck_latest_run_is_fail(session):
    assert await _status_for_latest_run(session, JobRunStatus.STUCK) == "FAIL"


async def test_daily_summary_failed_latest_run_is_fail(session):
    assert await _status_for_latest_run(session, JobRunStatus.FAILED) == "FAIL"


async def test_daily_summary_timeout_latest_run_is_fail(session):
    assert await _status_for_latest_run(session, JobRunStatus.TIMEOUT) == "FAIL"


async def test_daily_summary_success_latest_run_is_ok(session):
    assert await _status_for_latest_run(session, JobRunStatus.SUCCESS) == "OK"


async def test_daily_summary_counts_reflect_stuck_as_fail(session):
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.STUCK, finished_at=NOW)
    session.add(run)
    await session.commit()

    summary = await build_daily_summary(session, now=NOW)
    assert summary.counts.jobs_fail >= 1
    assert summary.counts.jobs_ok == 0
