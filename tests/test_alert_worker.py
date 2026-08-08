"""Tests for `app.workers.alert_worker` -- the three periodic
background alert-detection checks (`check_missed_runs`,
`check_agent_offline`, `check_job_timeouts`).

Mirrors `tests/test_bootstrap_admin.py`'s established pattern: call the
worker functions directly with the `session_maker` fixture (bound to the
in-memory test engine), never through the ASGI app/lifespan -- per
`app/main.py`'s own docstring, the `client` fixture's `ASGITransport`
never triggers `lifespan`, so `alert_worker_loop` never actually starts
during the test suite.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy import Update, func, select
from sqlalchemy.ext.asyncio import AsyncSession as RealAsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.timeutils import as_naive_utc, compute_next_scheduled_run
from app.main import app as fastapi_app
from app.models.alert import Alert
from app.models.backup_job import BackupJob
from app.models.enums import AlertStatus, AlertType, JobRunStatus, ServerStatus, UserRole
from app.models.job_run import JobRun
from app.models.server import Server
from app.workers.alert_worker import check_agent_offline, check_job_timeouts, check_missed_runs
from conftest import (
    build_backup_job,
    build_disk,
    build_job_run,
    build_server,
    build_user,
    mint_token,
)

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


async def _enabled_job(session, **overrides) -> BackupJob:
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


async def _active_alert_count(
    session_maker, alert_type: AlertType, status: AlertStatus = AlertStatus.ACTIVE
) -> int:
    async with session_maker() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(Alert)
                .where(Alert.alert_type == alert_type, Alert.status == status)
            )
        ).scalar_one()


async def _authed_user_and_token(session):
    user = build_user(role=UserRole.ADMIN)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    token = mint_token(user.id, user.username, user.role)
    return user, token


async def _ws_client(session_maker, token: str) -> AsyncClient:
    """Same shape as tests/test_routers_job_run_ws.py's private helper --
    an httpx.AsyncClient wired to the real app over a WS-capable transport,
    sharing the given session_maker's engine."""
    from httpx_ws.transport import ASGIWebSocketTransport

    async def _override_get_db():
        async with session_maker() as s:
            yield s

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGIWebSocketTransport(app=fastapi_app)
    return AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


# ---------------------------------------------------------------------------
# check_missed_runs
# ---------------------------------------------------------------------------


async def test_check_missed_runs_raises_alert_for_overdue_job(session, session_maker):
    job = await _enabled_job(
        session,
        schedule_cron="0 * * * *",
        timezone="UTC",
        missed_run_grace_minutes=15,
        last_run_at=NOW - timedelta(hours=3),
    )

    count = await check_missed_runs(session_maker, now=NOW)
    assert count == 1

    async with session_maker() as s:
        alert = (
            await s.execute(
                select(Alert).where(
                    Alert.alert_type == AlertType.JOB_MISSED, Alert.backup_job_id == job.id
                )
            )
        ).scalar_one()
    assert alert.status == AlertStatus.ACTIVE


async def test_check_missed_runs_dedup_second_tick_no_duplicate(session, session_maker):
    await _enabled_job(
        session,
        schedule_cron="0 * * * *",
        timezone="UTC",
        missed_run_grace_minutes=15,
        last_run_at=NOW - timedelta(hours=3),
    )

    first_count = await check_missed_runs(session_maker, now=NOW)
    assert first_count == 1

    # Still overdue at a later tick -- must not raise a second, distinct alert.
    second_count = await check_missed_runs(session_maker, now=NOW + timedelta(minutes=5))
    assert second_count == 0

    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 1


async def test_check_missed_runs_resolves_when_caught_up(session, session_maker):
    job = await _enabled_job(
        session,
        schedule_cron="0 * * * *",
        timezone="UTC",
        missed_run_grace_minutes=15,
        last_run_at=NOW - timedelta(hours=3),
    )

    await check_missed_runs(session_maker, now=NOW)
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 1

    async with session_maker() as s:
        db_job = await s.get(BackupJob, job.id)
        db_job.last_run_at = NOW - timedelta(minutes=5)
        await s.commit()

    resolve_tick_count = await check_missed_runs(session_maker, now=NOW)
    assert resolve_tick_count == 0

    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 0
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED, AlertStatus.RESOLVED) == 1


async def test_check_missed_runs_disabled_job_never_evaluated(session, session_maker):
    await _enabled_job(
        session,
        schedule_cron="0 * * * *",
        timezone="UTC",
        missed_run_grace_minutes=15,
        last_run_at=NOW - timedelta(days=5),
        is_enabled=False,
    )

    count = await check_missed_runs(session_maker, now=NOW)
    assert count == 0
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 0


async def test_check_missed_runs_malformed_cron_is_skipped_without_raising(session, session_maker):
    await _enabled_job(
        session,
        schedule_cron="not a valid cron",
        timezone="UTC",
        missed_run_grace_minutes=15,
        last_run_at=NOW - timedelta(hours=3),
    )

    # Must not raise -- the invalid cron is caught and the job is skipped.
    count = await check_missed_runs(session_maker, now=NOW)
    assert count == 0
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 0


async def test_check_missed_runs_honors_job_timezone_not_utc(session, session_maker):
    """`anchor`/`now` are chosen so that evaluating the cron in
    America/New_York (UTC-5 in January) vs. silently treating the same
    values as UTC produce DIFFERENT overdue verdicts:

      - NY-aware next fire:  2026-01-15 05:00 UTC (overdue by 1h at `now`)
      - UTC-naive next fire: 2026-01-16 00:00 UTC (not yet due at `now`)

    If the worker silently treated `timezone` as UTC, no alert would be
    raised here.
    """
    anchor = datetime(2026, 1, 15, 4, 30, 0, tzinfo=UTC)
    now = datetime(2026, 1, 15, 6, 0, 0, tzinfo=UTC)

    # Sanity-check the premise directly against compute_next_scheduled_run.
    ny_expected = compute_next_scheduled_run("0 0 * * *", "America/New_York", anchor)
    utc_expected = compute_next_scheduled_run("0 0 * * *", "UTC", anchor)
    assert ny_expected == datetime(2026, 1, 15, 5, 0, 0)
    assert utc_expected == datetime(2026, 1, 16, 0, 0, 0)
    assert as_naive_utc(now) > ny_expected
    assert as_naive_utc(now) < utc_expected

    await _enabled_job(
        session,
        schedule_cron="0 0 * * *",
        timezone="America/New_York",
        missed_run_grace_minutes=5,
        last_run_at=anchor,
    )

    count = await check_missed_runs(session_maker, now=now)
    assert count == 1
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 1


async def test_check_missed_runs_dst_spring_forward_transition_is_sane(session, session_maker):
    """Spring-forward DST boundary (America/New_York, 2026-03-08 2am local
    clocks skip forward to 3am): compute_next_scheduled_run must produce a
    sane, non-crashing result for a cron fire time landing inside the
    nonexistent local hour, and check_missed_runs must run end-to-end
    without raising."""
    anchor = datetime(2026, 3, 8, 6, 30, 0, tzinfo=UTC)  # 1:30am EST, just before the jump
    result = compute_next_scheduled_run("30 2 * * *", "America/New_York", anchor)
    assert result is not None
    # Sane result: within a few hours of the DST boundary, not wildly wrong.
    assert timedelta(0) < result - as_naive_utc(anchor) < timedelta(hours=6)

    await _enabled_job(
        session,
        schedule_cron="30 2 * * *",
        timezone="America/New_York",
        missed_run_grace_minutes=15,
        last_run_at=anchor,
    )
    now = datetime(2026, 3, 8, 12, 0, 0, tzinfo=UTC)
    count = await check_missed_runs(session_maker, now=now)
    assert count == 1  # well past due by noon UTC


# ---------------------------------------------------------------------------
# check_agent_offline
# ---------------------------------------------------------------------------


async def test_check_agent_offline_marks_stale_server_and_raises_alert(session, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_OFFLINE_THRESHOLD_MINUTES", 10)
    server = build_server(status=ServerStatus.ACTIVE, last_seen_at=NOW - timedelta(minutes=20))
    session.add(server)
    await session.commit()
    server_id = server.id

    count = await check_agent_offline(session_maker, now=NOW)
    assert count == 1

    async with session_maker() as s:
        refreshed = await s.get(Server, server_id)
        assert refreshed.status == ServerStatus.OFFLINE

    assert await _active_alert_count(session_maker, AlertType.AGENT_OFFLINE) == 1


async def test_check_agent_offline_repeat_tick_no_duplicate_or_error(session, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_OFFLINE_THRESHOLD_MINUTES", 10)
    server = build_server(status=ServerStatus.ACTIVE, last_seen_at=NOW - timedelta(minutes=20))
    session.add(server)
    await session.commit()

    first = await check_agent_offline(session_maker, now=NOW)
    assert first == 1

    # Second tick: the already-OFFLINE server is excluded from the initial
    # SELECT (reviewer fix) -- must be a complete no-op, not just "dedup".
    second = await check_agent_offline(session_maker, now=NOW + timedelta(minutes=5))
    assert second == 0
    assert await _active_alert_count(session_maker, AlertType.AGENT_OFFLINE) == 1


async def test_check_agent_offline_disabled_server_never_touched(session, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_OFFLINE_THRESHOLD_MINUTES", 10)
    server = build_server(status=ServerStatus.DISABLED, last_seen_at=NOW - timedelta(days=10))
    session.add(server)
    await session.commit()
    server_id = server.id

    count = await check_agent_offline(session_maker, now=NOW)
    assert count == 0

    async with session_maker() as s:
        refreshed = await s.get(Server, server_id)
        assert refreshed.status == ServerStatus.DISABLED

    assert await _active_alert_count(session_maker, AlertType.AGENT_OFFLINE) == 0


async def test_check_agent_offline_never_heartbeated_server_never_touched(session, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_OFFLINE_THRESHOLD_MINUTES", 10)
    server = build_server(status=ServerStatus.ACTIVE, last_seen_at=None)
    session.add(server)
    await session.commit()
    server_id = server.id

    count = await check_agent_offline(session_maker, now=NOW)
    assert count == 0

    async with session_maker() as s:
        refreshed = await s.get(Server, server_id)
        assert refreshed.status == ServerStatus.ACTIVE

    assert await _active_alert_count(session_maker, AlertType.AGENT_OFFLINE) == 0


async def test_check_agent_offline_race_concurrent_heartbeat_not_clobbered(
    session, session_maker, monkeypatch
):
    """THE most important test in this task per the user's explicit ask:
    a heartbeat lands between the worker's SELECT and its CAS UPDATE.
    The worker's `seen_at_snapshot`-guarded UPDATE must match zero rows,
    leaving the server ACTIVE with the fresh heartbeat timestamp -- NOT
    clobbered back to OFFLINE -- and no AGENT_OFFLINE alert must be
    raised.
    """
    monkeypatch.setattr(settings, "AGENT_OFFLINE_THRESHOLD_MINUTES", 10)
    stale_seen_at = NOW - timedelta(minutes=20)
    server = build_server(status=ServerStatus.ACTIVE, last_seen_at=stale_seen_at)
    session.add(server)
    await session.commit()
    server_id = server.id

    fresh_seen_at = NOW - timedelta(seconds=1)

    real_execute = RealAsyncSession.execute
    triggered = {"done": False}

    async def _execute_with_race(self, statement, *args, **kwargs):
        if (
            not triggered["done"]
            and isinstance(statement, Update)
            and statement.table.name == "servers"
        ):
            triggered["done"] = True
            # Simulate a concurrent heartbeat landing right here, between
            # the worker's SELECT (already completed) and its CAS UPDATE
            # (about to execute below via real_execute).
            async with session_maker() as other:
                other_server = await other.get(Server, server_id)
                other_server.last_seen_at = fresh_seen_at
                other_server.status = ServerStatus.ACTIVE
                await other.commit()
        return await real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(RealAsyncSession, "execute", _execute_with_race)

    count = await check_agent_offline(session_maker, now=NOW)

    assert triggered["done"] is True
    assert count == 0

    async with session_maker() as s:
        refreshed = await s.get(Server, server_id)
        assert refreshed.status == ServerStatus.ACTIVE
        assert as_naive_utc(refreshed.last_seen_at) == as_naive_utc(fresh_seen_at)

    assert await _active_alert_count(session_maker, AlertType.AGENT_OFFLINE) == 0


# ---------------------------------------------------------------------------
# app/routers/agents.py -- heartbeat resolves AGENT_OFFLINE unconditionally
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reachable", [True, False])
async def test_heartbeat_resolves_agent_offline_alert_regardless_of_reachable(
    admin_client, session, session_maker, monkeypatch, reachable
):
    monkeypatch.setattr(settings, "AGENT_OFFLINE_THRESHOLD_MINUTES", 10)
    server = build_server(status=ServerStatus.ACTIVE, last_seen_at=NOW - timedelta(minutes=20))
    session.add(server)
    await session.commit()
    server_id = server.id

    raised = await check_agent_offline(session_maker, now=NOW)
    assert raised == 1
    assert await _active_alert_count(session_maker, AlertType.AGENT_OFFLINE) == 1

    resp = await admin_client.post(f"/api/agents/{server_id}/heartbeat", json={"reachable": reachable})
    assert resp.status_code == 200
    resolved_types = [a["alert_type"] for a in resp.json()["alerts_resolved"]]
    assert AlertType.AGENT_OFFLINE.value in resolved_types

    assert await _active_alert_count(session_maker, AlertType.AGENT_OFFLINE) == 0


# ---------------------------------------------------------------------------
# check_job_timeouts
# ---------------------------------------------------------------------------


async def test_check_job_timeouts_transitions_running_run_past_duration(session, session_maker):
    job = await _enabled_job(session, expected_max_duration_minutes=30)
    started_at = NOW - timedelta(hours=1)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING, started_at=started_at)
    session.add(run)
    await session.commit()
    run_id = run.id

    count = await check_job_timeouts(session_maker, now=NOW)
    assert count == 1

    async with session_maker() as s:
        refreshed = await s.get(JobRun, run_id)
        assert refreshed.status == JobRunStatus.TIMEOUT
        assert refreshed.finished_at is not None
        assert refreshed.duration_seconds is not None
        assert refreshed.duration_seconds > 0
        assert refreshed.error_message is not None

        db_job = await s.get(BackupJob, job.id)
        assert db_job.last_run_at is not None

    assert await _active_alert_count(session_maker, AlertType.JOB_TIMEOUT) == 1

    # Dedup on repeat -- no second alert for the same backup job.
    second = await check_job_timeouts(session_maker, now=NOW + timedelta(minutes=5))
    assert second == 0
    assert await _active_alert_count(session_maker, AlertType.JOB_TIMEOUT) == 1


async def test_check_job_timeouts_null_expected_duration_never_touched(session, session_maker):
    job = await _enabled_job(session, expected_max_duration_minutes=None)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING, started_at=NOW - timedelta(days=5))
    session.add(run)
    await session.commit()
    run_id = run.id

    count = await check_job_timeouts(session_maker, now=NOW)
    assert count == 0

    async with session_maker() as s:
        refreshed = await s.get(JobRun, run_id)
        assert refreshed.status == JobRunStatus.RUNNING


async def test_check_job_timeouts_pending_run_never_touched(session, session_maker):
    job = await _enabled_job(session, expected_max_duration_minutes=30)
    run = build_job_run(job.id, status=JobRunStatus.PENDING)
    session.add(run)
    await session.commit()
    run_id = run.id

    count = await check_job_timeouts(session_maker, now=NOW)
    assert count == 0

    async with session_maker() as s:
        refreshed = await s.get(JobRun, run_id)
        assert refreshed.status == JobRunStatus.PENDING


async def test_check_job_timeouts_race_concurrently_completed_run_not_overwritten(
    session, session_maker, monkeypatch
):
    """A run that gets concurrently completed (SUCCESS) between being
    selected as timeout-eligible and the worker's CAS update must keep its
    real terminal status -- not be overwritten to TIMEOUT -- and no
    JOB_TIMEOUT alert may be raised for it."""
    job = await _enabled_job(session, expected_max_duration_minutes=30)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING, started_at=NOW - timedelta(hours=1))
    session.add(run)
    await session.commit()
    run_id = run.id

    real_execute = RealAsyncSession.execute
    triggered = {"done": False}

    async def _execute_with_race(self, statement, *args, **kwargs):
        if (
            not triggered["done"]
            and isinstance(statement, Update)
            and statement.table.name == "job_runs"
        ):
            triggered["done"] = True
            async with session_maker() as other:
                other_run = await other.get(JobRun, run_id)
                other_run.status = JobRunStatus.SUCCESS
                other_run.finished_at = NOW
                await other.commit()
        return await real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(RealAsyncSession, "execute", _execute_with_race)

    count = await check_job_timeouts(session_maker, now=NOW)

    assert triggered["done"] is True
    assert count == 0

    async with session_maker() as s:
        refreshed = await s.get(JobRun, run_id)
        assert refreshed.status == JobRunStatus.SUCCESS

    assert await _active_alert_count(session_maker, AlertType.JOB_TIMEOUT) == 0


async def test_check_job_timeouts_notifies_ws_client_then_closes(session, session_maker):
    """End-to-end: a WS client watching a RUNNING run must receive the
    final TIMEOUT broadcast, then have its connection closed with code
    1000, when check_job_timeouts times out that run."""
    job = await _enabled_job(session, expected_max_duration_minutes=30)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING, started_at=NOW - timedelta(hours=1))
    session.add(run)
    await session.commit()
    run_id = run.id

    _user, token = await _authed_user_and_token(session)
    ws_client = await _ws_client(session_maker, token)
    try:
        async with ws_client:
            async with aconnect_ws(f"/ws/job-runs/{run_id}?token={token}", ws_client) as ws:
                initial = await ws.receive_json()
                assert initial["status"] == "RUNNING"

                count = await check_job_timeouts(session_maker, now=NOW)
                assert count == 1

                final = await ws.receive_json()
                assert final["status"] == "TIMEOUT"
                assert final["finished_at"] is not None

                got_disconnect = None
                try:
                    await ws.receive_json()
                    raise AssertionError("expected the connection to be closed by the server")
                except* WebSocketDisconnect as eg:
                    got_disconnect = eg.exceptions[0]
                assert got_disconnect is not None
                assert got_disconnect.code == 1000
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


async def test_timeout_transition_lets_next_missed_runs_tick_resolve_job_missed(session, session_maker):
    """Cross-check called out in spec Section 5.3 point 5: a TIMEOUT
    transition updates BackupJob.last_run_at, so the *next* tick of
    check_missed_runs (not check_job_timeouts itself) resolves any active
    JOB_MISSED alert for that same job."""
    job = await _enabled_job(
        session,
        schedule_cron="0 * * * *",
        timezone="UTC",
        missed_run_grace_minutes=15,
        expected_max_duration_minutes=30,
        last_run_at=NOW - timedelta(hours=3),
    )

    missed_count = await check_missed_runs(session_maker, now=NOW)
    assert missed_count == 1
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 1

    run = build_job_run(job.id, status=JobRunStatus.RUNNING, started_at=NOW - timedelta(hours=1))
    session.add(run)
    await session.commit()

    timeout_count = await check_job_timeouts(session_maker, now=NOW)
    assert timeout_count == 1
    # check_job_timeouts itself must not resolve JOB_MISSED.
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 1

    resolved_tick_count = await check_missed_runs(session_maker, now=NOW)
    assert resolved_tick_count == 0
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED) == 0
    assert await _active_alert_count(session_maker, AlertType.JOB_MISSED, AlertStatus.RESOLVED) == 1
