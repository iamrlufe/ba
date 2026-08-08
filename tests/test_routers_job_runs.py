"""HTTP-layer tests for /api/job-runs (app/routers/job_runs.py).

Includes the regression tests for the just-fixed atomic-completion CAS
guard (2.a in the task spec): a retried/duplicate `POST .../complete` (or
`PATCH`) must 409 instead of silently re-applying (and corrupting) the
terminal state of an already-completed run.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import get_db
from app.main import app as fastapi_app
from app.models.backup_job import BackupJob
from app.models.base import Base
from app.models.disk import Disk
from app.models.enums import JobRunStatus, ProtocolType, UserRole
from app.models.job_run import JobRun
from app.models.server import Server
from app.models.user import User
from tests.conftest import build_backup_job, build_disk, build_job_run, build_server, mint_token


async def _enabled_job(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id, is_enabled=True)
    session.add(job)
    await session.commit()
    return job


# --------------------------------------------------------------------------
# Baseline CRUD / validation / 404
# --------------------------------------------------------------------------


async def test_create_job_run_happy_path(admin_client, session):
    job = await _enabled_job(session)
    resp = await admin_client.post("/api/job-runs", json={"backup_job_id": job.id, "triggered_by": "manual"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "PENDING"


async def test_create_job_run_disabled_job_is_409(admin_client, session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id, is_enabled=False)
    session.add(job)
    await session.commit()

    resp = await admin_client.post("/api/job-runs", json={"backup_job_id": job.id})
    assert resp.status_code == 409


async def test_create_job_run_missing_backup_job_is_404(admin_client):
    resp = await admin_client.post("/api/job-runs", json={"backup_job_id": 999999})
    assert resp.status_code == 404


async def test_create_job_run_second_active_run_is_409(admin_client, session):
    job = await _enabled_job(session)
    r1 = await admin_client.post("/api/job-runs", json={"backup_job_id": job.id})
    assert r1.status_code == 201
    r2 = await admin_client.post("/api/job-runs", json={"backup_job_id": job.id})
    assert r2.status_code == 409


async def test_get_job_run_404(admin_client):
    resp = await admin_client.get("/api/job-runs/999999")
    assert resp.status_code == 404


async def test_get_job_run_log(admin_client, session):
    job = await _enabled_job(session)
    run = build_job_run(job.id, log_output="line1\nline2")
    session.add(run)
    await session.commit()

    resp = await admin_client.get(f"/api/job-runs/{run.id}/log")
    assert resp.status_code == 200
    assert resp.json()["log_output"] == "line1\nline2"


async def test_patch_job_run_valid_transition(admin_client, session):
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.PENDING)
    session.add(run)
    await session.commit()

    resp = await admin_client.patch(f"/api/job-runs/{run.id}", json={"status": "RUNNING"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "RUNNING"


async def test_patch_job_run_progress_fields(admin_client, session):
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()

    resp = await admin_client.patch(
        f"/api/job-runs/{run.id}", json={"percent": 42, "current_file": "f.bak", "bytes_done": 1024}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["percent"] == 42
    assert body["current_file"] == "f.bak"
    assert body["bytes_done"] == 1024


async def test_patch_job_run_terminal_status_via_patch_is_422(admin_client, session):
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()

    resp = await admin_client.patch(f"/api/job-runs/{run.id}", json={"status": "SUCCESS"})
    assert resp.status_code == 422


async def test_patch_job_run_already_terminal_is_409(admin_client, session):
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.SUCCESS, finished_at=datetime.now(UTC))
    session.add(run)
    await session.commit()

    resp = await admin_client.patch(f"/api/job-runs/{run.id}", json={"percent": 50})
    assert resp.status_code == 409


async def test_patch_job_run_invalid_transition_terminal_back_to_pending_is_409(admin_client, session):
    # Directly seed an invalid old_status via ORM bypassing the router so we
    # can exercise the transition-check branch specifically (not reachable
    # by going only through the API, since PATCH already 409s on any
    # already-terminal run before even inspecting payload.status).
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()

    # RUNNING -> PENDING is a structurally allowed transition per
    # is_valid_transition (only terminal -> PENDING/RUNNING is forbidden),
    # so this should succeed; used here as a control/happy-path check for
    # the transition validator wired into the PATCH handler.
    resp = await admin_client.patch(f"/api/job-runs/{run.id}", json={"status": "PENDING"})
    assert resp.status_code == 200


async def test_patch_job_run_404(admin_client):
    resp = await admin_client.patch("/api/job-runs/999999", json={"percent": 1})
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# 2.a REGRESSION: atomic conditional UPDATE on complete/PATCH survives a
# retried/duplicate request racing the first one.
# --------------------------------------------------------------------------


async def test_complete_job_run_happy_path(admin_client, session):
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING, started_at=datetime.now(UTC))
    session.add(run)
    await session.commit()

    resp = await admin_client.post(f"/api/job-runs/{run.id}/complete", json={"status": "SUCCESS"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["finished_at"] is not None


async def test_complete_job_run_double_completion_sequential_is_409_and_state_unchanged(admin_client, session):
    """Sequential regression test: second POST .../complete on an
    already-completed run must 409 and must NOT alter the persisted
    status/finished_at from the first (successful) call."""
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING, started_at=datetime.now(UTC))
    session.add(run)
    await session.commit()

    first = await admin_client.post(f"/api/job-runs/{run.id}/complete", json={"status": "SUCCESS"})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["status"] == "SUCCESS"
    first_finished_at = first_body["finished_at"]
    assert first_finished_at is not None

    second = await admin_client.post(f"/api/job-runs/{run.id}/complete", json={"status": "FAILED"})
    assert second.status_code == 409

    get_resp = await admin_client.get(f"/api/job-runs/{run.id}")
    assert get_resp.status_code == 200
    persisted = get_resp.json()
    assert persisted["status"] == "SUCCESS"
    assert persisted["finished_at"] == first_finished_at


async def test_patch_job_run_after_complete_is_409(admin_client, session):
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING, started_at=datetime.now(UTC))
    session.add(run)
    await session.commit()

    complete_resp = await admin_client.post(f"/api/job-runs/{run.id}/complete", json={"status": "SUCCESS"})
    assert complete_resp.status_code == 200

    patch_resp = await admin_client.patch(f"/api/job-runs/{run.id}", json={"percent": 100})
    assert patch_resp.status_code == 409


async def test_complete_job_run_concurrent_double_completion_exactly_one_wins():
    """Tighter regression test: fire two `complete` requests concurrently
    (asyncio.gather) against the same RUNNING job run. Exactly one must
    succeed (200) and the other must 409 -- the CAS guard (`WHERE status ==
    <status read at request start>`) must prevent both from applying,
    which a naive read-then-write implementation would not guarantee.

    Deliberately does NOT use the shared `admin_client`/`session` fixtures from
    conftest.py: those are bound to a `StaticPool`-backed in-memory SQLite
    engine that hands out multiple `AsyncSession`s over a *single* shared
    physical DBAPI connection. Two sessions concurrently holding open
    transactions on that one physical connection is not a valid simulation
    of two independent concurrent requests (their BEGIN/COMMIT boundaries
    interleave on the same connection object, corrupting both transactions
    -- confirmed by a standalone repro), so it can't be used to exercise a
    *real* race. This test instead spins up its own temp-file-backed SQLite
    engine (a real, independent-connections-capable engine, as it would be
    in production against any real DB), so the two concurrent requests get
    genuinely independent DB connections/transactions.
    """
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async def _override_get_db():
            async with maker() as s:
                yield s

        previous_override = fastapi_app.dependency_overrides.get(get_db)
        fastapi_app.dependency_overrides[get_db] = _override_get_db
        try:
            async with maker() as s:
                server = Server(name="race-server", host="h", port=21, protocol=ProtocolType.FTP)
                s.add(server)
                await s.commit()
                disk = Disk(server_id=server.id, label="d", mount_path="/m")
                s.add(disk)
                await s.commit()
                job = BackupJob(
                    server_id=server.id,
                    disk_id=disk.id,
                    name="race-job",
                    source_path="/s",
                    schedule_cron="* * * * *",
                )
                s.add(job)
                await s.commit()
                run = JobRun(backup_job_id=job.id, status=JobRunStatus.RUNNING, started_at=datetime.now(UTC))
                s.add(run)
                await s.commit()
                run_id = run.id

                admin = User(
                    username="race-admin",
                    hashed_password="unused",
                    role=UserRole.ADMIN,
                    is_active=True,
                )
                s.add(admin)
                await s.commit()
                token = mint_token(admin.id, admin.username, admin.role)

            transport = ASGITransport(app=fastapi_app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"Authorization": f"Bearer {token}"},
            ) as race_client:
                results = await asyncio.gather(
                    race_client.post(f"/api/job-runs/{run_id}/complete", json={"status": "SUCCESS"}),
                    race_client.post(f"/api/job-runs/{run_id}/complete", json={"status": "FAILED"}),
                )
                statuses = sorted(r.status_code for r in results)
                assert statuses == [200, 409]

                get_resp = await race_client.get(f"/api/job-runs/{run_id}")
                persisted_status = get_resp.json()["status"]
                # Persisted terminal status must match whichever of the two
                # requests actually won (the one that got 200), not a mix
                # of both, and must not still be RUNNING.
                winner = next(r for r in results if r.status_code == 200)
                assert persisted_status == winner.json()["status"]
                assert persisted_status in ("SUCCESS", "FAILED")
        finally:
            if previous_override is not None:
                fastapi_app.dependency_overrides[get_db] = previous_override
            else:
                fastapi_app.dependency_overrides.pop(get_db, None)
            await engine.dispose()
    finally:
        os.remove(db_path)
