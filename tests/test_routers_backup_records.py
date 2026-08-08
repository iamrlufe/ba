"""HTTP-layer tests for /api/backup-records (app/routers/backup_records.py)."""
from __future__ import annotations

from app.models.enums import JobRunStatus
from tests.conftest import build_backup_job, build_disk, build_job_run, build_server


async def _job(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()
    return job


async def test_upsert_backup_record_creates_new(admin_client, session):
    job = await _job(session)
    resp = await admin_client.post(
        "/api/backup-records",
        json={
            "backup_job_id": job.id,
            "file_name": "backup1.bak",
            "remote_path": "/remote/backup1.bak",
            "file_size_bytes": 2048,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_name"] == "backup1.bak"
    assert body["file_size_bytes"] == 2048


async def test_upsert_backup_record_updates_existing_on_conflict(admin_client, session):
    job = await _job(session)
    payload = {
        "backup_job_id": job.id,
        "file_name": "backup2.bak",
        "remote_path": "/remote/backup2.bak",
        "file_size_bytes": 1000,
    }
    first = await admin_client.post("/api/backup-records", json=payload)
    assert first.status_code == 200
    first_id = first.json()["id"]
    first_detected_at = first.json()["detected_at"]

    second = await admin_client.post(
        "/api/backup-records", json={**payload, "file_size_bytes": 2000, "checksum": "abc", "checksum_algorithm": "sha256"}
    )
    assert second.status_code == 200
    body = second.json()
    assert body["id"] == first_id
    assert body["file_size_bytes"] == 2000
    assert body["checksum"] == "abc"
    # detected_at must never be overwritten by later syncs.
    assert body["detected_at"] == first_detected_at


async def test_upsert_backup_record_checksum_without_algorithm_is_422(admin_client, session):
    job = await _job(session)
    resp = await admin_client.post(
        "/api/backup-records",
        json={
            "backup_job_id": job.id,
            "file_name": "backup3.bak",
            "remote_path": "/remote/backup3.bak",
            "file_size_bytes": 10,
            "checksum": "abc123",
        },
    )
    assert resp.status_code == 422


async def test_upsert_backup_record_missing_backup_job_is_404(admin_client):
    resp = await admin_client.post(
        "/api/backup-records",
        json={
            "backup_job_id": 999999,
            "file_name": "backup4.bak",
            "remote_path": "/remote/backup4.bak",
            "file_size_bytes": 10,
        },
    )
    assert resp.status_code == 404


async def test_upsert_backup_record_job_run_mismatch_is_422(admin_client, session):
    job = await _job(session)
    other_job = await _job(session)
    run = build_job_run(other_job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()

    resp = await admin_client.post(
        "/api/backup-records",
        json={
            "backup_job_id": job.id,
            "job_run_id": run.id,
            "file_name": "backup5.bak",
            "remote_path": "/remote/backup5.bak",
            "file_size_bytes": 10,
        },
    )
    assert resp.status_code == 422


async def test_upsert_backup_record_missing_job_run_is_404(admin_client, session):
    job = await _job(session)
    resp = await admin_client.post(
        "/api/backup-records",
        json={
            "backup_job_id": job.id,
            "job_run_id": 999999,
            "file_name": "backup6.bak",
            "remote_path": "/remote/backup6.bak",
            "file_size_bytes": 10,
        },
    )
    assert resp.status_code == 404


async def test_upsert_backup_record_negative_size_is_422(admin_client, session):
    job = await _job(session)
    resp = await admin_client.post(
        "/api/backup-records",
        json={
            "backup_job_id": job.id,
            "file_name": "backup7.bak",
            "remote_path": "/remote/backup7.bak",
            "file_size_bytes": -1,
        },
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# GET /api/backup-records/{id}
# --------------------------------------------------------------------------


async def test_get_backup_record_happy_path(admin_client, session):
    job = await _job(session)
    create_resp = await admin_client.post(
        "/api/backup-records",
        json={
            "backup_job_id": job.id,
            "file_name": "backup8.bak",
            "remote_path": "/remote/backup8.bak",
            "file_size_bytes": 4096,
        },
    )
    assert create_resp.status_code == 200
    record_id = create_resp.json()["id"]

    resp = await admin_client.get(f"/api/backup-records/{record_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == record_id
    assert body["file_name"] == "backup8.bak"
    assert body["backup_job_id"] == job.id


async def test_get_backup_record_404(admin_client):
    resp = await admin_client.get("/api/backup-records/999999")
    assert resp.status_code == 404


async def test_get_backup_record_requires_auth(client, session):
    job = await _job(session)
    resp = await client.get(f"/api/backup-records/{job.id}")
    assert resp.status_code == 401
