"""HTTP-layer tests for /api/disks (app/routers/disks.py)."""
from __future__ import annotations

from conftest import build_backup_job, build_disk, build_server


async def _server(session):
    server = build_server()
    session.add(server)
    await session.commit()
    return server


async def test_create_disk_happy_path(client, session):
    server = await _server(session)
    resp = await client.post(
        "/api/disks",
        json={"server_id": server.id, "label": "d1", "mount_path": "/mnt/d1"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["server_id"] == server.id
    assert body["used_pct"] is None


async def test_create_disk_bad_thresholds_is_422(client, session):
    server = await _server(session)
    resp = await client.post(
        "/api/disks",
        json={
            "server_id": server.id,
            "label": "d1",
            "mount_path": "/mnt/d1",
            "warning_threshold_pct": 90,
            "critical_threshold_pct": 80,
        },
    )
    assert resp.status_code == 422


async def test_create_disk_missing_server_is_404(client):
    resp = await client.post(
        "/api/disks", json={"server_id": 999999, "label": "d1", "mount_path": "/mnt/d1"}
    )
    assert resp.status_code == 404


async def test_create_disk_on_deleted_server_is_409(client, session):
    server = build_server(is_deleted=True)
    session.add(server)
    await session.commit()

    resp = await client.post(
        "/api/disks", json={"server_id": server.id, "label": "d1", "mount_path": "/mnt/d1"}
    )
    assert resp.status_code == 409


async def test_create_disk_duplicate_mount_path_same_server_is_409(client, session):
    server = await _server(session)
    payload = {"server_id": server.id, "label": "d1", "mount_path": "/mnt/dup"}
    r1 = await client.post("/api/disks", json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/api/disks", json={**payload, "label": "d2"})
    assert r2.status_code == 409


async def test_get_disk_404(client):
    resp = await client.get("/api/disks/999999")
    assert resp.status_code == 404


async def test_used_pct_computed_field(client, session):
    server = await _server(session)
    disk = build_disk(server.id, total_bytes=1000, free_bytes=250)
    session.add(disk)
    await session.commit()

    resp = await client.get(f"/api/disks/{disk.id}")
    assert resp.status_code == 200
    assert resp.json()["used_pct"] == 75.0


async def test_update_disk_happy_path(client, session):
    server = await _server(session)
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()

    resp = await client.patch(f"/api/disks/{disk.id}", json={"label": "renamed", "is_active": True})
    assert resp.status_code == 200
    assert resp.json()["label"] == "renamed"


async def test_update_disk_404(client):
    resp = await client.patch("/api/disks/999999", json={"label": "x"})
    assert resp.status_code == 404


async def test_delete_disk_happy_path(client, session):
    server = await _server(session)
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()

    resp = await client.delete(f"/api/disks/{disk.id}")
    assert resp.status_code == 204

    get_resp = await client.get(f"/api/disks/{disk.id}")
    assert get_resp.status_code == 404


async def test_delete_disk_referenced_by_backup_job_is_409(client, session):
    server = await _server(session)
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()

    resp = await client.delete(f"/api/disks/{disk.id}")
    assert resp.status_code == 409


async def test_delete_disk_404(client):
    resp = await client.delete("/api/disks/999999")
    assert resp.status_code == 404


async def test_list_disks_filters_by_server(client, session):
    server1 = await _server(session)
    server2 = await _server(session)
    d1 = build_disk(server1.id)
    d2 = build_disk(server2.id)
    session.add_all([d1, d2])
    await session.commit()

    resp = await client.get("/api/disks", params={"server_id": server1.id})
    ids = [d["id"] for d in resp.json()["items"]]
    assert d1.id in ids
    assert d2.id not in ids
