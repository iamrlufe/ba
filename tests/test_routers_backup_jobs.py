"""HTTP-layer tests for /api/backup-jobs (app/routers/backup_jobs.py).

Includes the regression test for the just-fixed disk/server mismatch check
(2.b in the task spec): POST /api/backup-jobs must 422 when the referenced
disk's server_id doesn't match the payload's server_id.
"""
from __future__ import annotations

from app.models.enums import JobRunStatus
from conftest import build_backup_job, build_disk, build_job_run, build_server, build_sql_instance


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
            server.id, disk.id, sql_instance_id=instance.id, verification_method="RESTORE_VERIFY"
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
