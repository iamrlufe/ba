"""HTTP-layer tests for /api/sql-instances (app/routers/sql_instances.py)."""
from __future__ import annotations

from app.core.security import decrypt_secret
from app.models.enums import JobRunStatus, RequestChannel, RestoreMode, RestoreStatus
from conftest import (
    build_backup_job,
    build_backup_record,
    build_disk,
    build_restore_operation,
    build_server,
    build_sql_instance,
)


async def test_create_sql_instance_happy_path(client):
    resp = await client.post(
        "/api/sql-instances",
        json={
            "name": "sql-1",
            "host": "10.0.1.1",
            "use_windows_auth": False,
            "username": "sa",
            "password": "s3cret",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["credentials_set"] is True
    assert "password" not in body
    assert "s3cret" not in resp.text


async def test_create_sql_instance_requires_credentials_when_not_windows_auth(client):
    resp = await client.post(
        "/api/sql-instances",
        json={"name": "sql-2", "host": "10.0.1.2", "use_windows_auth": False},
    )
    assert resp.status_code == 422


async def test_create_sql_instance_windows_auth_ok_without_credentials(client):
    resp = await client.post(
        "/api/sql-instances",
        json={"name": "sql-3", "host": "10.0.1.3", "use_windows_auth": True},
    )
    assert resp.status_code == 201
    assert resp.json()["credentials_set"] is False


async def test_create_sql_instance_secret_encrypted_at_rest(client, session):
    resp = await client.post(
        "/api/sql-instances",
        json={
            "name": "sql-secret",
            "host": "10.0.1.4",
            "use_windows_auth": False,
            "username": "sa",
            "password": "plain-pw",
        },
    )
    assert resp.status_code == 201
    instance_id = resp.json()["id"]

    from app.models.sql_instance import SqlInstance

    db_instance = await session.get(SqlInstance, instance_id)
    assert db_instance.password_encrypted is not None
    assert "plain-pw" not in db_instance.password_encrypted
    assert decrypt_secret(db_instance.password_encrypted) == "plain-pw"


async def test_create_sql_instance_with_deleted_server_is_409(client, session):
    server = build_server(is_deleted=True)
    session.add(server)
    await session.commit()

    resp = await client.post(
        "/api/sql-instances",
        json={"name": "sql-4", "host": "h", "use_windows_auth": True, "server_id": server.id},
    )
    assert resp.status_code == 409


async def test_create_sql_instance_with_missing_server_is_404(client):
    resp = await client.post(
        "/api/sql-instances",
        json={"name": "sql-5", "host": "h", "use_windows_auth": True, "server_id": 999999},
    )
    assert resp.status_code == 404


async def test_get_sql_instance_404(client):
    resp = await client.get("/api/sql-instances/999999")
    assert resp.status_code == 404


async def test_update_sql_instance_clears_secret(client):
    create = await client.post(
        "/api/sql-instances",
        json={
            "name": "sql-upd",
            "host": "h",
            "use_windows_auth": False,
            "username": "sa",
            "password": "pw1",
        },
    )
    instance_id = create.json()["id"]

    resp = await client.patch(f"/api/sql-instances/{instance_id}", json={"password": "", "username": ""})
    assert resp.status_code == 200
    assert resp.json()["credentials_set"] is False


async def test_update_deleted_sql_instance_is_409(client, session):
    instance = build_sql_instance(is_deleted=True)
    session.add(instance)
    await session.commit()

    resp = await client.patch(f"/api/sql-instances/{instance.id}", json={"notes": "x"})
    assert resp.status_code == 409


async def test_delete_sql_instance_with_active_restore_is_409(client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()

    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()
    restore = build_restore_operation(record.id, instance.id, status=RestoreStatus.RUNNING)
    session.add(restore)
    await session.commit()

    resp = await client.delete(f"/api/sql-instances/{instance.id}")
    assert resp.status_code == 409


async def test_delete_sql_instance_with_enabled_job_is_409(client, session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    job = build_backup_job(
        server.id, disk.id, sql_instance_id=instance.id, verification_method="RESTORE_VERIFY", is_enabled=True
    )
    session.add(job)
    await session.commit()

    resp = await client.delete(f"/api/sql-instances/{instance.id}")
    assert resp.status_code == 409


async def test_delete_sql_instance_soft_deletes(client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()

    resp = await client.delete(f"/api/sql-instances/{instance.id}")
    assert resp.status_code == 204

    get_resp = await client.get(f"/api/sql-instances/{instance.id}")
    assert get_resp.json()["is_deleted"] is True


async def test_delete_sql_instance_404(client):
    resp = await client.delete("/api/sql-instances/999999")
    assert resp.status_code == 404
