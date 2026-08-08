"""HTTP-layer tests for /api/servers (app/routers/servers.py)."""
from __future__ import annotations

from app.core.security import decrypt_secret
from app.models.enums import ProtocolType
from conftest import build_backup_job, build_disk, build_server


async def test_create_server_happy_path_sftp(client):
    resp = await client.post(
        "/api/servers",
        json={
            "name": "srv-1",
            "host": "10.0.0.5",
            "port": 22,
            "protocol": "SFTP",
            "password": "hunter2",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "srv-1"
    assert body["credentials_set"] is True
    assert body["ssh_key_set"] is False
    assert "password" not in body
    assert "hunter2" not in resp.text


async def test_create_server_sftp_without_credentials_is_422(client):
    resp = await client.post(
        "/api/servers",
        json={"name": "srv-2", "host": "10.0.0.6", "port": 22, "protocol": "SFTP"},
    )
    assert resp.status_code == 422


async def test_create_server_ftp_without_credentials_is_ok(client):
    resp = await client.post(
        "/api/servers",
        json={"name": "srv-3", "host": "10.0.0.7", "port": 21, "protocol": "FTP"},
    )
    assert resp.status_code == 201


async def test_create_server_secret_is_encrypted_at_rest(client, session):
    resp = await client.post(
        "/api/servers",
        json={
            "name": "srv-secret",
            "host": "10.0.0.8",
            "port": 22,
            "protocol": "SFTP",
            "password": "plaintext-pw",
        },
    )
    assert resp.status_code == 201
    server_id = resp.json()["id"]

    from app.models.server import Server

    db_server = await session.get(Server, server_id)
    assert db_server is not None
    assert db_server.password_encrypted is not None
    assert "plaintext-pw" not in db_server.password_encrypted
    assert decrypt_secret(db_server.password_encrypted) == "plaintext-pw"


async def test_create_server_duplicate_name_is_409(client):
    payload = {"name": "dup-name", "host": "10.0.0.9", "port": 21, "protocol": "FTP"}
    r1 = await client.post("/api/servers", json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/api/servers", json=payload)
    assert r2.status_code == 409


async def test_get_server_404(client):
    resp = await client.get("/api/servers/999999")
    assert resp.status_code == 404


async def test_list_servers_excludes_deleted_by_default(client, session):
    server = build_server(is_deleted=True)
    session.add(server)
    await session.commit()

    resp = await client.get("/api/servers")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["items"]]
    assert server.id not in ids

    resp2 = await client.get("/api/servers", params={"include_deleted": True})
    ids2 = [s["id"] for s in resp2.json()["items"]]
    assert server.id in ids2


async def test_update_server_clears_secret_with_empty_string(client):
    create = await client.post(
        "/api/servers",
        json={"name": "srv-upd", "host": "h", "port": 22, "protocol": "SFTP", "password": "pw1"},
    )
    server_id = create.json()["id"]
    assert create.json()["credentials_set"] is True

    resp = await client.patch(f"/api/servers/{server_id}", json={"password": ""})
    assert resp.status_code == 200
    assert resp.json()["credentials_set"] is False


async def test_update_deleted_server_is_409(client, session):
    server = build_server(is_deleted=True)
    session.add(server)
    await session.commit()

    resp = await client.patch(f"/api/servers/{server.id}", json={"notes": "x"})
    assert resp.status_code == 409


async def test_update_server_404(client):
    resp = await client.patch("/api/servers/999999", json={"notes": "x"})
    assert resp.status_code == 404


async def test_delete_server_with_no_enabled_jobs_soft_deletes(client, session):
    server = build_server()
    session.add(server)
    await session.commit()

    resp = await client.delete(f"/api/servers/{server.id}")
    assert resp.status_code == 204

    get_resp = await client.get(f"/api/servers/{server.id}")
    assert get_resp.json()["is_deleted"] is True


async def test_delete_server_with_enabled_backup_job_is_409(client, session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id, is_enabled=True)
    session.add(job)
    await session.commit()

    resp = await client.delete(f"/api/servers/{server.id}")
    assert resp.status_code == 409


async def test_delete_server_404(client):
    resp = await client.delete("/api/servers/999999")
    assert resp.status_code == 404
