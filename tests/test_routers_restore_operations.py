"""HTTP-layer tests for /api/restore-operations (app/routers/restore_operations.py).

Includes the regression test for the just-fixed atomic conditional-UPDATE
CAS guard on `PATCH /api/restore-operations/{id}` (2.a in the task spec):
a PATCH transitioning a restore operation into a terminal status (DONE/
FAILED), followed by a second PATCH attempt, must 409 without altering the
already-terminal row.
"""
from __future__ import annotations

from app.models.enums import RestoreMode, RestoreStatus
from conftest import build_backup_job, build_backup_record, build_disk, build_server, build_sql_instance


async def _record_and_instance(session):
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
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    return record, instance


def _create_payload(record_id: int, instance_id: int, db_name: str = "orders", **overrides) -> dict:
    payload = {
        "backup_record_id": record_id,
        "sql_instance_id": instance_id,
        "database_name": db_name,
        "confirmation_database_name": db_name,
        "mode": RestoreMode.ALL.value,
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# Baseline CRUD / validation / 404
# --------------------------------------------------------------------------


async def test_create_restore_operation_happy_path(client, session):
    record, instance = await _record_and_instance(session)
    resp = await client.post(
        "/api/restore-operations",
        json=_create_payload(record.id, instance.id),
        headers={"X-Requested-By": "alice"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["requested_by"] == "alice"
    assert body["server_id"] == instance.server_id


async def test_create_restore_operation_confirmation_mismatch_is_422(client, session):
    record, instance = await _record_and_instance(session)
    resp = await client.post(
        "/api/restore-operations",
        json=_create_payload(record.id, instance.id, confirmation_database_name="typo"),
    )
    assert resp.status_code == 422


async def test_create_restore_operation_missing_backup_record_is_404(client, session):
    _record, instance = await _record_and_instance(session)
    resp = await client.post("/api/restore-operations", json=_create_payload(999999, instance.id))
    assert resp.status_code == 404


async def test_create_restore_operation_missing_sql_instance_is_404(client, session):
    record, _instance = await _record_and_instance(session)
    resp = await client.post("/api/restore-operations", json=_create_payload(record.id, 999999))
    assert resp.status_code == 404


async def test_create_restore_operation_deleted_sql_instance_is_409(client, session):
    record, instance = await _record_and_instance(session)
    instance.is_deleted = True
    await session.commit()

    resp = await client.post("/api/restore-operations", json=_create_payload(record.id, instance.id))
    assert resp.status_code == 409


async def test_create_restore_operation_duplicate_active_is_409(client, session):
    record, instance = await _record_and_instance(session)
    payload = _create_payload(record.id, instance.id)
    r1 = await client.post("/api/restore-operations", json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/api/restore-operations", json=payload)
    assert r2.status_code == 409


async def test_get_restore_operation_404(client):
    resp = await client.get("/api/restore-operations/999999")
    assert resp.status_code == 404


async def test_get_restore_operation_log(client, session):
    record, instance = await _record_and_instance(session)
    from app.models.enums import RequestChannel
    from conftest import build_restore_operation

    restore = build_restore_operation(record.id, instance.id, log="line1\nline2")
    session.add(restore)
    await session.commit()

    resp = await client.get(f"/api/restore-operations/{restore.id}/log")
    assert resp.status_code == 200
    assert resp.json()["log"] == "line1\nline2"


async def test_patch_restore_operation_running_to_cancelled_is_409(client, session):
    from conftest import build_restore_operation

    record, instance = await _record_and_instance(session)
    restore = build_restore_operation(record.id, instance.id, status=RestoreStatus.RUNNING)
    session.add(restore)
    await session.commit()

    resp = await client.patch(f"/api/restore-operations/{restore.id}", json={"status": "CANCELLED"})
    assert resp.status_code == 409


async def test_patch_restore_operation_pending_to_cancelled_is_ok(client, session):
    from conftest import build_restore_operation

    record, instance = await _record_and_instance(session)
    restore = build_restore_operation(record.id, instance.id, status=RestoreStatus.PENDING)
    session.add(restore)
    await session.commit()

    resp = await client.patch(f"/api/restore-operations/{restore.id}", json={"status": "CANCELLED"})
    assert resp.status_code == 200
    # Note: unlike DONE/FAILED, the router does not auto-stamp completed_at
    # for CANCELLED (see update_restore_operation) -- only status changes.
    assert resp.json()["status"] == "CANCELLED"


async def test_patch_restore_operation_404(client):
    resp = await client.patch("/api/restore-operations/999999", json={"status": "RUNNING"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# 2.a REGRESSION: atomic conditional UPDATE on PATCH -> terminal status
# survives a retried/duplicate request racing the first one.
# --------------------------------------------------------------------------


async def test_patch_restore_operation_double_terminal_transition_is_409_and_state_unchanged(client, session):
    """Sequential regression test: PATCH a restore operation into a
    terminal status (DONE), then PATCH it again -- the second attempt must
    409 and must NOT alter the already-terminal row's status/completed_at
    from what the first (successful) PATCH set."""
    from conftest import build_restore_operation

    record, instance = await _record_and_instance(session)
    restore = build_restore_operation(record.id, instance.id, status=RestoreStatus.RUNNING)
    session.add(restore)
    await session.commit()

    first = await client.patch(f"/api/restore-operations/{restore.id}", json={"status": "DONE"})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["status"] == "DONE"
    first_completed_at = first_body["completed_at"]
    assert first_completed_at is not None

    second = await client.patch(
        f"/api/restore-operations/{restore.id}", json={"status": "FAILED", "error_message": "retry"}
    )
    assert second.status_code == 409

    get_resp = await client.get(f"/api/restore-operations/{restore.id}")
    persisted = get_resp.json()
    assert persisted["status"] == "DONE"
    assert persisted["completed_at"] == first_completed_at
    assert persisted["error_message"] is None
