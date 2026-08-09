"""HTTP-layer tests for /api/backup-jobs (app/routers/backup_jobs.py).

Includes the regression test for the just-fixed disk/server mismatch check
(2.b in the task spec): POST /api/backup-jobs must 422 when the referenced
disk's server_id doesn't match the payload's server_id.
"""
from __future__ import annotations

from app.models.enums import JobRunStatus, VerificationRunStatus
from app.routers import backup_jobs as backup_jobs_module
from app.workers.backup_verification import create_pending_verification_run
from tests.conftest import (
    build_backup_job,
    build_disk,
    build_job_run,
    build_server,
    build_sql_instance,
    build_verification_run,
)


def _job_payload(server_id: int, disk_id: int, **overrides) -> dict:
    payload = {
        "name": "job-1",
        "source_path": "/data/src",
        "schedule_cron": "0 * * * *",
        "server_id": server_id,
        "disk_id": disk_id,
    }
    payload.update(overrides)
    return payload


async def _server_and_disk(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    return server, disk


async def test_create_backup_job_happy_path(admin_client, session):
    server, disk = await _server_and_disk(session)
    resp = await admin_client.post("/api/backup-jobs", json=_job_payload(server.id, disk.id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["server_id"] == server.id
    assert body["disk_id"] == disk.id


async def test_create_backup_job_disk_server_mismatch_is_422(admin_client, session):
    """Regression test: disk belonging to server A must be rejected when
    server_id in the payload points at server B."""
    server_a, disk_a = await _server_and_disk(session)
    server_b, _disk_b = await _server_and_disk(session)

    resp = await admin_client.post(
        "/api/backup-jobs", json=_job_payload(server_b.id, disk_a.id)
    )
    assert resp.status_code == 422


async def test_create_backup_job_disk_matching_server_still_succeeds(admin_client, session):
    """Sanity check accompanying the mismatch regression test above: the
    matching (disk actually belongs to server_id) case must still 201."""
    server, disk = await _server_and_disk(session)
    resp = await admin_client.post("/api/backup-jobs", json=_job_payload(server.id, disk.id))
    assert resp.status_code == 201


async def test_create_backup_job_missing_server_is_404(admin_client, session):
    _server, disk = await _server_and_disk(session)
    resp = await admin_client.post("/api/backup-jobs", json=_job_payload(999999, disk.id))
    assert resp.status_code == 404


async def test_create_backup_job_missing_disk_is_404(admin_client, session):
    server, _disk = await _server_and_disk(session)
    resp = await admin_client.post("/api/backup-jobs", json=_job_payload(server.id, 999999))
    assert resp.status_code == 404


async def test_create_backup_job_on_deleted_server_is_409(admin_client, session):
    server = build_server(is_deleted=True)
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()

    resp = await admin_client.post("/api/backup-jobs", json=_job_payload(server.id, disk.id))
    assert resp.status_code == 409


async def test_create_backup_job_with_deleted_sql_instance_is_409(admin_client, session):
    server, disk = await _server_and_disk(session)
    instance = build_sql_instance(is_deleted=True)
    session.add(instance)
    await session.commit()

    resp = await admin_client.post(
        "/api/backup-jobs",
        json=_job_payload(
            server.id,
            disk.id,
            sql_instance_id=instance.id,
            verification_method="RESTORE_VERIFY",
            database_name="orders",
        ),
    )
    assert resp.status_code == 409


async def test_create_backup_job_sql_instance_without_verification_method_is_422(admin_client, session):
    server, disk = await _server_and_disk(session)
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()

    resp = await admin_client.post(
        "/api/backup-jobs",
        json=_job_payload(server.id, disk.id, sql_instance_id=instance.id),
    )
    assert resp.status_code == 422


async def test_get_backup_job_404(admin_client):
    resp = await admin_client.get("/api/backup-jobs/999999")
    assert resp.status_code == 404


async def test_update_backup_job_happy_path(admin_client, session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()

    resp = await admin_client.patch(f"/api/backup-jobs/{job.id}", json={"name": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"


async def test_update_backup_job_sql_instance_without_verification_method_is_409(admin_client, session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()

    resp = await admin_client.patch(f"/api/backup-jobs/{job.id}", json={"sql_instance_id": 1})
    assert resp.status_code == 409


async def test_update_backup_job_404(admin_client):
    resp = await admin_client.patch("/api/backup-jobs/999999", json={"name": "x"})
    assert resp.status_code == 404


async def test_delete_backup_job_with_active_run_is_409(admin_client, session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()

    resp = await admin_client.delete(f"/api/backup-jobs/{job.id}")
    assert resp.status_code == 409


async def test_delete_backup_job_happy_path(admin_client, session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()

    resp = await admin_client.delete(f"/api/backup-jobs/{job.id}")
    assert resp.status_code == 204

    get_resp = await admin_client.get(f"/api/backup-jobs/{job.id}")
    assert get_resp.status_code == 404


async def test_delete_backup_job_404(admin_client):
    resp = await admin_client.delete("/api/backup-jobs/999999")
    assert resp.status_code == 404


async def test_list_backup_jobs_filters(admin_client, session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(server.id, disk.id, is_enabled=False)
    session.add(job)
    await session.commit()

    resp = await admin_client.get("/api/backup-jobs", params={"is_enabled": False})
    ids = [j["id"] for j in resp.json()["items"]]
    assert job.id in ids

    resp2 = await admin_client.get("/api/backup-jobs", params={"is_enabled": True})
    ids2 = [j["id"] for j in resp2.json()["items"]]
    assert job.id not in ids2


# ---------------------------------------------------------------------------
# POST /{id}/verify, GET /{id}/verification-runs, GET .../verification-runs/{run_id}
# ---------------------------------------------------------------------------


async def _eligible_job(session, **overrides) -> tuple:
    server, disk = await _server_and_disk(session)
    instance = build_sql_instance(use_windows_auth=True)
    session.add(instance)
    await session.commit()
    await session.refresh(instance)

    overrides.setdefault("sql_instance_id", instance.id)
    overrides.setdefault("verification_method", "RESTORE_VERIFY")
    overrides.setdefault("database_name", "orders")
    job = build_backup_job(server.id, disk.id, **overrides)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job, instance


async def test_verify_backup_job_admin_returns_202(admin_client, session, session_maker, monkeypatch):
    # Isolate the fire-and-forget background task from the real
    # DATABASE_URL-backed engine (app.core.db.async_session_maker) so it
    # operates on the same in-memory test DB the request itself used --
    # never touches a real file/dev DB, never opens a real network socket
    # (there is no BackupRecord, so execute_verification_run classifies
    # MISSING without ever calling a SqlClientFactory).
    monkeypatch.setattr(backup_jobs_module, "async_session_maker", session_maker)
    job, _instance = await _eligible_job(session)

    resp = await admin_client.post(f"/api/backup-jobs/{job.id}/verify")
    assert resp.status_code == 202
    body = resp.json()
    assert body["backup_job_id"] == job.id
    assert body["status"] == "PENDING"
    assert "error_message" not in body or body["error_message"] is None


async def test_verify_backup_job_operator_forbidden(operator_client, session):
    job, _instance = await _eligible_job(session)
    resp = await operator_client.post(f"/api/backup-jobs/{job.id}/verify")
    assert resp.status_code == 403


async def test_verify_backup_job_404(admin_client):
    resp = await admin_client.post("/api/backup-jobs/999999/verify")
    assert resp.status_code == 404


async def test_verify_backup_job_without_sql_instance_is_409(admin_client, session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()

    resp = await admin_client.post(f"/api/backup-jobs/{job.id}/verify")
    assert resp.status_code == 409


async def test_verify_backup_job_second_call_while_active_is_409(admin_client, session, session_maker):
    """Deterministic version of the "two concurrent POST /verify calls"
    check: pre-seed an already-active (PENDING) VerificationRun directly
    via the DB session (least flaky -- avoids racing the fire-and-forget
    background task of a first real POST), then confirm the endpoint's
    IntegrityError -> 409 conversion kicks in for the second POST."""
    job, _instance = await _eligible_job(session)

    async with session_maker() as s:
        await create_pending_verification_run(s, job.id, triggered_by="pretest")
        await s.commit()

    resp = await admin_client.post(f"/api/backup-jobs/{job.id}/verify")
    assert resp.status_code == 409


async def test_list_verification_runs_operator_forbidden(operator_client, session):
    job, _instance = await _eligible_job(session)
    resp = await operator_client.get(f"/api/backup-jobs/{job.id}/verification-runs")
    assert resp.status_code == 403


async def test_list_verification_runs_admin_pagination_and_filtering(admin_client, session):
    job, _instance = await _eligible_job(session)
    ok_run = build_verification_run(job.id, status=VerificationRunStatus.OK)
    corrupt_run = build_verification_run(job.id, status=VerificationRunStatus.CORRUPT)
    session.add_all([ok_run, corrupt_run])
    await session.commit()

    resp = await admin_client.get(f"/api/backup-jobs/{job.id}/verification-runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    ids = {item["id"] for item in body["items"]}
    assert {ok_run.id, corrupt_run.id} <= ids

    filtered = await admin_client.get(
        f"/api/backup-jobs/{job.id}/verification-runs", params={"status": "CORRUPT"}
    )
    assert filtered.status_code == 200
    filtered_body = filtered.json()
    assert filtered_body["total"] == 1
    assert filtered_body["items"][0]["id"] == corrupt_run.id


async def test_list_verification_runs_404_for_missing_job(admin_client):
    resp = await admin_client.get("/api/backup-jobs/999999/verification-runs")
    assert resp.status_code == 404


async def test_get_verification_run_operator_forbidden(operator_client, session):
    job, _instance = await _eligible_job(session)
    run = build_verification_run(job.id, status=VerificationRunStatus.OK)
    session.add(run)
    await session.commit()

    resp = await operator_client.get(f"/api/backup-jobs/{job.id}/verification-runs/{run.id}")
    assert resp.status_code == 403


async def test_get_verification_run_admin_happy_path(admin_client, session):
    job, _instance = await _eligible_job(session)
    run = build_verification_run(job.id, status=VerificationRunStatus.OK)
    session.add(run)
    await session.commit()

    resp = await admin_client.get(f"/api/backup-jobs/{job.id}/verification-runs/{run.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run.id


async def test_get_verification_run_404_when_not_found(admin_client, session):
    job, _instance = await _eligible_job(session)
    resp = await admin_client.get(f"/api/backup-jobs/{job.id}/verification-runs/999999")
    assert resp.status_code == 404


async def test_get_verification_run_404_cross_job_leak(admin_client, session):
    """A VerificationRun that belongs to a DIFFERENT backup_job_id must 404,
    not leak across jobs."""
    job_a, _instance_a = await _eligible_job(session)
    job_b, _instance_b = await _eligible_job(session)
    run_for_b = build_verification_run(job_b.id, status=VerificationRunStatus.OK)
    session.add(run_for_b)
    await session.commit()

    resp = await admin_client.get(f"/api/backup-jobs/{job_a.id}/verification-runs/{run_for_b.id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# delete_backup_job -- extended guard: 409 if an active VerificationRun exists
# ---------------------------------------------------------------------------


async def test_delete_backup_job_with_active_verification_run_is_409(admin_client, session):
    job, _instance = await _eligible_job(session)
    run = build_verification_run(job.id, status=VerificationRunStatus.PENDING)
    session.add(run)
    await session.commit()

    resp = await admin_client.delete(f"/api/backup-jobs/{job.id}")
    assert resp.status_code == 409


async def test_delete_backup_job_with_terminal_verification_run_succeeds(admin_client, session):
    job, _instance = await _eligible_job(session)
    run = build_verification_run(job.id, status=VerificationRunStatus.OK)
    session.add(run)
    await session.commit()

    resp = await admin_client.delete(f"/api/backup-jobs/{job.id}")
    assert resp.status_code == 204
