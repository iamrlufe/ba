"""Cross-cutting permission-matrix coverage for the auth module.

This deliberately does NOT re-test business logic already covered per-router
in tests/test_routers_*.py (disk/server mismatch, 404s, CAS guards, etc.) --
only the auth *gate* on each endpoint:

  - Admin-only write endpoints (POST/PATCH/DELETE on servers, sql-instances,
    disks, backup-jobs; PATCH on restore-operations; acknowledge/resolve on
    alerts): 403 for an authenticated OPERATOR, 401 for no auth at all.
  - "Any authenticated" GET endpoints: 200 for both ADMIN and OPERATOR,
    401 for no auth.
  - `require_admin_or_agent_key`-gated endpoints (job-runs POST/PATCH/
    complete, backup-records POST, agents heartbeat): valid X-Agent-Key
    succeeds with no JWT at all; an admin JWT succeeds with no header; an
    operator JWT is rejected (403, NOT satisfied by a non-admin human
    token); a wrong/garbage X-Agent-Key is 401; neither header nor JWT is
    401.

Where an existing test file already exercises one of these cases (e.g.
POST /api/users 403/401, or the restore-operations mode=MISSING carve-out),
it is intentionally not duplicated here.
"""
from __future__ import annotations

from app.core.config import settings
from tests.conftest import (
    build_alert,
    build_backup_job,
    build_backup_record,
    build_disk,
    build_job_run,
    build_restore_operation,
    build_server,
    build_sql_instance,
)

# --------------------------------------------------------------------------
# Shared setup helpers
# --------------------------------------------------------------------------


async def _server(session):
    server = build_server()
    session.add(server)
    await session.commit()
    return server


async def _server_and_disk(session):
    server = await _server(session)
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    return server, disk


async def _enabled_job(session):
    server, disk = await _server_and_disk(session)
    job = build_backup_job(server.id, disk.id, is_enabled=True)
    session.add(job)
    await session.commit()
    return job


async def _pending_run(session):
    job = await _enabled_job(session)
    run = build_job_run(job.id)
    session.add(run)
    await session.commit()
    return run


async def _record_and_instance(session):
    job = await _enabled_job(session)
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    return record, instance


async def _pending_restore(session):
    record, instance = await _record_and_instance(session)
    restore = build_restore_operation(record.id, instance.id)
    session.add(restore)
    await session.commit()
    return restore


async def _active_alert(session):
    alert = build_alert()
    session.add(alert)
    await session.commit()
    return alert


# ==========================================================================
# 1. Admin-only WRITE endpoints -- 403 for operator, 401 for no auth
# ==========================================================================

# --- servers --------------------------------------------------------------


async def test_create_server_forbidden_for_operator(operator_client):
    resp = await operator_client.post(
        "/api/servers", json={"name": "op-srv", "host": "10.0.0.9", "port": 21, "protocol": "FTP"}
    )
    assert resp.status_code == 403


async def test_create_server_requires_auth(client):
    resp = await client.post(
        "/api/servers", json={"name": "noauth-srv", "host": "10.0.0.9", "port": 21, "protocol": "FTP"}
    )
    assert resp.status_code == 401


async def test_update_server_forbidden_for_operator(operator_client, session):
    server = await _server(session)
    resp = await operator_client.patch(f"/api/servers/{server.id}", json={"notes": "x"})
    assert resp.status_code == 403


async def test_update_server_requires_auth(client, session):
    server = await _server(session)
    resp = await client.patch(f"/api/servers/{server.id}", json={"notes": "x"})
    assert resp.status_code == 401


async def test_delete_server_forbidden_for_operator(operator_client, session):
    server = await _server(session)
    resp = await operator_client.delete(f"/api/servers/{server.id}")
    assert resp.status_code == 403


async def test_delete_server_requires_auth(client, session):
    server = await _server(session)
    resp = await client.delete(f"/api/servers/{server.id}")
    assert resp.status_code == 401


# --- sql-instances ----------------------------------------------------------


async def test_create_sql_instance_forbidden_for_operator(operator_client):
    resp = await operator_client.post(
        "/api/sql-instances", json={"name": "op-sql", "host": "10.0.1.9", "use_windows_auth": True}
    )
    assert resp.status_code == 403


async def test_create_sql_instance_requires_auth(client):
    resp = await client.post(
        "/api/sql-instances", json={"name": "noauth-sql", "host": "10.0.1.9", "use_windows_auth": True}
    )
    assert resp.status_code == 401


async def test_update_sql_instance_forbidden_for_operator(operator_client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    resp = await operator_client.patch(f"/api/sql-instances/{instance.id}", json={"notes": "x"})
    assert resp.status_code == 403


async def test_update_sql_instance_requires_auth(client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    resp = await client.patch(f"/api/sql-instances/{instance.id}", json={"notes": "x"})
    assert resp.status_code == 401


async def test_delete_sql_instance_forbidden_for_operator(operator_client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    resp = await operator_client.delete(f"/api/sql-instances/{instance.id}")
    assert resp.status_code == 403


async def test_delete_sql_instance_requires_auth(client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    resp = await client.delete(f"/api/sql-instances/{instance.id}")
    assert resp.status_code == 401


# --- disks -----------------------------------------------------------------


async def test_create_disk_forbidden_for_operator(operator_client, session):
    server = await _server(session)
    resp = await operator_client.post(
        "/api/disks", json={"server_id": server.id, "label": "op-disk", "mount_path": "/mnt/op"}
    )
    assert resp.status_code == 403


async def test_create_disk_requires_auth(client, session):
    server = await _server(session)
    resp = await client.post(
        "/api/disks", json={"server_id": server.id, "label": "noauth-disk", "mount_path": "/mnt/noauth"}
    )
    assert resp.status_code == 401


async def test_update_disk_forbidden_for_operator(operator_client, session):
    _server_row, disk = await _server_and_disk(session)
    resp = await operator_client.patch(f"/api/disks/{disk.id}", json={"label": "renamed"})
    assert resp.status_code == 403


async def test_update_disk_requires_auth(client, session):
    _server_row, disk = await _server_and_disk(session)
    resp = await client.patch(f"/api/disks/{disk.id}", json={"label": "renamed"})
    assert resp.status_code == 401


async def test_delete_disk_forbidden_for_operator(operator_client, session):
    _server_row, disk = await _server_and_disk(session)
    resp = await operator_client.delete(f"/api/disks/{disk.id}")
    assert resp.status_code == 403


async def test_delete_disk_requires_auth(client, session):
    _server_row, disk = await _server_and_disk(session)
    resp = await client.delete(f"/api/disks/{disk.id}")
    assert resp.status_code == 401


# --- backup-jobs -------------------------------------------------------------


async def test_create_backup_job_forbidden_for_operator(operator_client, session):
    server, disk = await _server_and_disk(session)
    resp = await operator_client.post(
        "/api/backup-jobs",
        json={
            "name": "op-job",
            "source_path": "/data/src",
            "schedule_cron": "0 * * * *",
            "server_id": server.id,
            "disk_id": disk.id,
        },
    )
    assert resp.status_code == 403


async def test_create_backup_job_requires_auth(client, session):
    server, disk = await _server_and_disk(session)
    resp = await client.post(
        "/api/backup-jobs",
        json={
            "name": "noauth-job",
            "source_path": "/data/src",
            "schedule_cron": "0 * * * *",
            "server_id": server.id,
            "disk_id": disk.id,
        },
    )
    assert resp.status_code == 401


async def test_update_backup_job_forbidden_for_operator(operator_client, session):
    job = await _enabled_job(session)
    resp = await operator_client.patch(f"/api/backup-jobs/{job.id}", json={"name": "renamed"})
    assert resp.status_code == 403


async def test_update_backup_job_requires_auth(client, session):
    job = await _enabled_job(session)
    resp = await client.patch(f"/api/backup-jobs/{job.id}", json={"name": "renamed"})
    assert resp.status_code == 401


async def test_delete_backup_job_forbidden_for_operator(operator_client, session):
    job = await _enabled_job(session)
    resp = await operator_client.delete(f"/api/backup-jobs/{job.id}")
    assert resp.status_code == 403


async def test_delete_backup_job_requires_auth(client, session):
    job = await _enabled_job(session)
    resp = await client.delete(f"/api/backup-jobs/{job.id}")
    assert resp.status_code == 401


# --- restore-operations PATCH (operator-403 already covered elsewhere) ------


async def test_patch_restore_operation_requires_auth(client, session):
    restore = await _pending_restore(session)
    resp = await client.patch(f"/api/restore-operations/{restore.id}", json={"status": "CANCELLED"})
    assert resp.status_code == 401


# --- alerts acknowledge/resolve (operator-403 already covered elsewhere) ----


async def test_acknowledge_alert_requires_auth(client, session):
    alert = await _active_alert(session)
    resp = await client.post(f"/api/alerts/{alert.id}/acknowledge", json={})
    assert resp.status_code == 401


async def test_resolve_alert_requires_auth(client, session):
    alert = await _active_alert(session)
    resp = await client.post(f"/api/alerts/{alert.id}/resolve", json={})
    assert resp.status_code == 401


# ==========================================================================
# 2. "Any authenticated" GET endpoints -- 200 for admin AND operator, 401 bare
#
# NOTE: `admin_client`/`operator_client`/`client` all build on the SAME
# underlying `httpx.AsyncClient` instance (see tests/conftest.py -- each
# fixture just mutates the shared object's `Authorization` header). Because
# of that, a single test must never request more than one of
# {client, admin_client, operator_client} at once: whichever fixture's
# setup runs last "wins" the header, silently making the earlier-requested
# fixture alias the same (wrong) identity. Each role is therefore its own
# test function here, deliberately not combined/looped.
# ==========================================================================


async def test_list_servers_ok_for_admin(admin_client, session):
    server = await _server(session)
    assert (await admin_client.get("/api/servers")).status_code == 200
    assert (await admin_client.get(f"/api/servers/{server.id}")).status_code == 200


async def test_list_servers_ok_for_operator(operator_client, session):
    server = await _server(session)
    assert (await operator_client.get("/api/servers")).status_code == 200
    assert (await operator_client.get(f"/api/servers/{server.id}")).status_code == 200


async def test_list_servers_requires_auth(client, session):
    server = await _server(session)
    assert (await client.get("/api/servers")).status_code == 401
    assert (await client.get(f"/api/servers/{server.id}")).status_code == 401


async def test_list_sql_instances_ok_for_admin(admin_client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    assert (await admin_client.get("/api/sql-instances")).status_code == 200
    assert (await admin_client.get(f"/api/sql-instances/{instance.id}")).status_code == 200


async def test_list_sql_instances_ok_for_operator(operator_client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    assert (await operator_client.get("/api/sql-instances")).status_code == 200
    assert (await operator_client.get(f"/api/sql-instances/{instance.id}")).status_code == 200


async def test_list_sql_instances_requires_auth(client, session):
    instance = build_sql_instance()
    session.add(instance)
    await session.commit()
    assert (await client.get("/api/sql-instances")).status_code == 401
    assert (await client.get(f"/api/sql-instances/{instance.id}")).status_code == 401


async def test_list_disks_ok_for_admin(admin_client, session):
    _server_row, disk = await _server_and_disk(session)
    assert (await admin_client.get("/api/disks")).status_code == 200
    assert (await admin_client.get(f"/api/disks/{disk.id}")).status_code == 200


async def test_list_disks_ok_for_operator(operator_client, session):
    _server_row, disk = await _server_and_disk(session)
    assert (await operator_client.get("/api/disks")).status_code == 200
    assert (await operator_client.get(f"/api/disks/{disk.id}")).status_code == 200


async def test_list_disks_requires_auth(client, session):
    _server_row, disk = await _server_and_disk(session)
    assert (await client.get("/api/disks")).status_code == 401
    assert (await client.get(f"/api/disks/{disk.id}")).status_code == 401


async def test_list_backup_jobs_ok_for_admin(admin_client, session):
    job = await _enabled_job(session)
    assert (await admin_client.get("/api/backup-jobs")).status_code == 200
    assert (await admin_client.get(f"/api/backup-jobs/{job.id}")).status_code == 200


async def test_list_backup_jobs_ok_for_operator(operator_client, session):
    job = await _enabled_job(session)
    assert (await operator_client.get("/api/backup-jobs")).status_code == 200
    assert (await operator_client.get(f"/api/backup-jobs/{job.id}")).status_code == 200


async def test_list_backup_jobs_requires_auth(client, session):
    job = await _enabled_job(session)
    assert (await client.get("/api/backup-jobs")).status_code == 401
    assert (await client.get(f"/api/backup-jobs/{job.id}")).status_code == 401


async def test_list_job_runs_ok_for_admin(admin_client, session):
    run = await _pending_run(session)
    assert (await admin_client.get("/api/job-runs")).status_code == 200
    assert (await admin_client.get(f"/api/job-runs/{run.id}")).status_code == 200
    assert (await admin_client.get(f"/api/job-runs/{run.id}/log")).status_code == 200


async def test_list_job_runs_ok_for_operator(operator_client, session):
    run = await _pending_run(session)
    assert (await operator_client.get("/api/job-runs")).status_code == 200
    assert (await operator_client.get(f"/api/job-runs/{run.id}")).status_code == 200
    assert (await operator_client.get(f"/api/job-runs/{run.id}/log")).status_code == 200


async def test_list_job_runs_requires_auth(client, session):
    run = await _pending_run(session)
    assert (await client.get("/api/job-runs")).status_code == 401
    assert (await client.get(f"/api/job-runs/{run.id}")).status_code == 401
    assert (await client.get(f"/api/job-runs/{run.id}/log")).status_code == 401


async def test_list_restore_operations_ok_for_admin(admin_client, session):
    restore = await _pending_restore(session)
    assert (await admin_client.get("/api/restore-operations")).status_code == 200
    assert (await admin_client.get(f"/api/restore-operations/{restore.id}")).status_code == 200
    assert (await admin_client.get(f"/api/restore-operations/{restore.id}/log")).status_code == 200


async def test_list_restore_operations_ok_for_operator(operator_client, session):
    restore = await _pending_restore(session)
    assert (await operator_client.get("/api/restore-operations")).status_code == 200
    assert (await operator_client.get(f"/api/restore-operations/{restore.id}")).status_code == 200
    assert (await operator_client.get(f"/api/restore-operations/{restore.id}/log")).status_code == 200


async def test_list_restore_operations_requires_auth(client, session):
    restore = await _pending_restore(session)
    assert (await client.get("/api/restore-operations")).status_code == 401
    assert (await client.get(f"/api/restore-operations/{restore.id}")).status_code == 401
    assert (await client.get(f"/api/restore-operations/{restore.id}/log")).status_code == 401


async def test_list_alerts_ok_for_admin(admin_client, session):
    await _active_alert(session)
    assert (await admin_client.get("/api/alerts")).status_code == 200


async def test_list_alerts_ok_for_operator(operator_client, session):
    await _active_alert(session)
    assert (await operator_client.get("/api/alerts")).status_code == 200


async def test_list_alerts_requires_auth(client, session):
    await _active_alert(session)
    assert (await client.get("/api/alerts")).status_code == 401


# ==========================================================================
# 3. require_admin_or_agent_key matrix -- job-runs write endpoints,
#    backup-records upsert, agents heartbeat.
# ==========================================================================

# --- POST /api/job-runs -----------------------------------------------------


async def test_create_job_run_valid_agent_key_succeeds_without_jwt(client, session):
    job = await _enabled_job(session)
    client.headers["X-Agent-Key"] = settings.AGENT_API_KEY
    resp = await client.post("/api/job-runs", json={"backup_job_id": job.id})
    assert resp.status_code == 201


async def test_create_job_run_admin_jwt_succeeds_without_agent_key(admin_client, session):
    job = await _enabled_job(session)
    resp = await admin_client.post("/api/job-runs", json={"backup_job_id": job.id})
    assert resp.status_code == 201


async def test_create_job_run_operator_jwt_is_403(operator_client, session):
    job = await _enabled_job(session)
    resp = await operator_client.post("/api/job-runs", json={"backup_job_id": job.id})
    assert resp.status_code == 403


async def test_create_job_run_wrong_agent_key_is_401(client, session):
    job = await _enabled_job(session)
    client.headers["X-Agent-Key"] = "not-the-real-key"
    resp = await client.post("/api/job-runs", json={"backup_job_id": job.id})
    assert resp.status_code == 401


async def test_create_job_run_no_auth_at_all_is_401(client, session):
    job = await _enabled_job(session)
    resp = await client.post("/api/job-runs", json={"backup_job_id": job.id})
    assert resp.status_code == 401


# --- PATCH /api/job-runs/{id} ------------------------------------------------


async def test_patch_job_run_valid_agent_key_succeeds_without_jwt(client, session):
    run = await _pending_run(session)
    client.headers["X-Agent-Key"] = settings.AGENT_API_KEY
    resp = await client.patch(f"/api/job-runs/{run.id}", json={"percent": 10})
    assert resp.status_code == 200


async def test_patch_job_run_admin_jwt_succeeds_without_agent_key(admin_client, session):
    run = await _pending_run(session)
    resp = await admin_client.patch(f"/api/job-runs/{run.id}", json={"percent": 10})
    assert resp.status_code == 200


async def test_patch_job_run_operator_jwt_is_403(operator_client, session):
    run = await _pending_run(session)
    resp = await operator_client.patch(f"/api/job-runs/{run.id}", json={"percent": 10})
    assert resp.status_code == 403


async def test_patch_job_run_wrong_agent_key_is_401(client, session):
    run = await _pending_run(session)
    client.headers["X-Agent-Key"] = "not-the-real-key"
    resp = await client.patch(f"/api/job-runs/{run.id}", json={"percent": 10})
    assert resp.status_code == 401


async def test_patch_job_run_no_auth_at_all_is_401(client, session):
    run = await _pending_run(session)
    resp = await client.patch(f"/api/job-runs/{run.id}", json={"percent": 10})
    assert resp.status_code == 401


# --- POST /api/job-runs/{id}/complete ----------------------------------------


async def test_complete_job_run_valid_agent_key_succeeds_without_jwt(client, session):
    run = await _pending_run(session)
    client.headers["X-Agent-Key"] = settings.AGENT_API_KEY
    resp = await client.post(f"/api/job-runs/{run.id}/complete", json={"status": "SUCCESS"})
    assert resp.status_code == 200


async def test_complete_job_run_admin_jwt_succeeds_without_agent_key(admin_client, session):
    run = await _pending_run(session)
    resp = await admin_client.post(f"/api/job-runs/{run.id}/complete", json={"status": "SUCCESS"})
    assert resp.status_code == 200


async def test_complete_job_run_operator_jwt_is_403(operator_client, session):
    run = await _pending_run(session)
    resp = await operator_client.post(f"/api/job-runs/{run.id}/complete", json={"status": "SUCCESS"})
    assert resp.status_code == 403


async def test_complete_job_run_wrong_agent_key_is_401(client, session):
    run = await _pending_run(session)
    client.headers["X-Agent-Key"] = "not-the-real-key"
    resp = await client.post(f"/api/job-runs/{run.id}/complete", json={"status": "SUCCESS"})
    assert resp.status_code == 401


async def test_complete_job_run_no_auth_at_all_is_401(client, session):
    run = await _pending_run(session)
    resp = await client.post(f"/api/job-runs/{run.id}/complete", json={"status": "SUCCESS"})
    assert resp.status_code == 401


# --- POST /api/backup-records (upsert) --------------------------------------


def _backup_record_payload(job_id: int) -> dict:
    return {
        "backup_job_id": job_id,
        "file_name": "matrix.bak",
        "remote_path": "/remote/matrix.bak",
        "file_size_bytes": 100,
    }


async def test_upsert_backup_record_valid_agent_key_succeeds_without_jwt(client, session):
    job = await _enabled_job(session)
    client.headers["X-Agent-Key"] = settings.AGENT_API_KEY
    resp = await client.post("/api/backup-records", json=_backup_record_payload(job.id))
    assert resp.status_code == 200


async def test_upsert_backup_record_admin_jwt_succeeds_without_agent_key(admin_client, session):
    job = await _enabled_job(session)
    resp = await admin_client.post("/api/backup-records", json=_backup_record_payload(job.id))
    assert resp.status_code == 200


async def test_upsert_backup_record_operator_jwt_is_403(operator_client, session):
    job = await _enabled_job(session)
    resp = await operator_client.post("/api/backup-records", json=_backup_record_payload(job.id))
    assert resp.status_code == 403


async def test_upsert_backup_record_wrong_agent_key_is_401(client, session):
    job = await _enabled_job(session)
    client.headers["X-Agent-Key"] = "not-the-real-key"
    resp = await client.post("/api/backup-records", json=_backup_record_payload(job.id))
    assert resp.status_code == 401


async def test_upsert_backup_record_no_auth_at_all_is_401(client, session):
    job = await _enabled_job(session)
    resp = await client.post("/api/backup-records", json=_backup_record_payload(job.id))
    assert resp.status_code == 401


# --- POST /api/agents/{server_id}/heartbeat ----------------------------------


async def test_heartbeat_valid_agent_key_succeeds_without_jwt(client, session):
    server = await _server(session)
    client.headers["X-Agent-Key"] = settings.AGENT_API_KEY
    resp = await client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert resp.status_code == 200


async def test_heartbeat_admin_jwt_succeeds_without_agent_key(admin_client, session):
    server = await _server(session)
    resp = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert resp.status_code == 200


async def test_heartbeat_operator_jwt_is_403(operator_client, session):
    server = await _server(session)
    resp = await operator_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert resp.status_code == 403


async def test_heartbeat_wrong_agent_key_is_401(client, session):
    server = await _server(session)
    client.headers["X-Agent-Key"] = "not-the-real-key"
    resp = await client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert resp.status_code == 401


async def test_heartbeat_no_auth_at_all_is_401(client, session):
    server = await _server(session)
    resp = await client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert resp.status_code == 401
