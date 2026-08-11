"""HTTP-layer tests for /api/agents (app/routers/agents.py).

Note: there is no ORM entity `Agent` -- `server_id` here is literally
`Server.id`, see the router module docstring.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.config import settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models.agent_credential_access_log import AgentCredentialAccessLog
from app.models.enums import (
    AgentCredentialAccessAuthMethod,
    AgentCredentialAccessOutcome,
    AlertType,
    ServerStatus,
)
from tests.conftest import build_backup_job, build_disk, build_server


async def test_heartbeat_missing_server_is_404(admin_client):
    resp = await admin_client.post("/api/agents/999999/heartbeat", json={"reachable": True})
    assert resp.status_code == 404


async def test_heartbeat_reachable_sets_active_and_updates_last_seen(admin_client, session):
    server = build_server(status=ServerStatus.UNREACHABLE)
    session.add(server)
    await session.commit()

    resp = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["server"]["status"] == "ACTIVE"
    assert body["server"]["last_seen_at"] is not None
    assert body["alerts_resolved"] == []
    assert body["alerts_raised"] == []


async def test_heartbeat_unreachable_sets_status_and_raises_alert(admin_client, session):
    server = build_server(status=ServerStatus.ACTIVE)
    session.add(server)
    await session.commit()

    resp = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["server"]["status"] == "UNREACHABLE"
    assert len(body["alerts_raised"]) == 1
    assert body["alerts_raised"][0]["alert_type"] == AlertType.SERVER_UNREACHABLE.value


async def test_heartbeat_unreachable_twice_does_not_duplicate_alert(admin_client, session):
    server = build_server(status=ServerStatus.ACTIVE)
    session.add(server)
    await session.commit()

    r1 = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": False})
    assert len(r1.json()["alerts_raised"]) == 1
    first_alert_id = r1.json()["alerts_raised"][0]["id"]

    # Second heartbeat re-reports the still-active alert (idempotent), but
    # must not create a second, distinct Alert row -- same id both times,
    # and only one ACTIVE SERVER_UNREACHABLE alert exists in the DB.
    r2 = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": False})
    assert len(r2.json()["alerts_raised"]) == 1
    assert r2.json()["alerts_raised"][0]["id"] == first_alert_id

    from sqlalchemy import func, select

    from app.models.alert import Alert

    count = (
        await session.execute(
            select(func.count()).select_from(Alert).where(Alert.alert_type == AlertType.SERVER_UNREACHABLE)
        )
    ).scalar_one()
    assert count == 1


async def test_heartbeat_recovers_and_resolves_alert(admin_client, session):
    server = build_server(status=ServerStatus.ACTIVE)
    session.add(server)
    await session.commit()

    down = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": False})
    assert len(down.json()["alerts_raised"]) == 1

    up = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert len(up.json()["alerts_resolved"]) == 1
    assert up.json()["alerts_resolved"][0]["alert_type"] == AlertType.SERVER_UNREACHABLE.value


async def test_heartbeat_disabled_server_never_reactivated(admin_client, session):
    server = build_server(status=ServerStatus.DISABLED)
    session.add(server)
    await session.commit()

    resp = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert resp.status_code == 200
    assert resp.json()["server"]["status"] == "DISABLED"


async def test_heartbeat_new_disk_created_inactive(admin_client, session):
    server = build_server()
    session.add(server)
    await session.commit()

    resp = await admin_client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={
            "reachable": True,
            "disks": [
                {"mount_path": "/mnt/new", "label": "New Disk", "total_bytes": 1000, "free_bytes": 500}
            ],
        },
    )
    assert resp.status_code == 200
    disks = resp.json()["disks"]
    assert len(disks) == 1
    assert disks[0]["is_active"] is False
    # No threshold alert for a brand-new, not-yet-active disk.
    assert resp.json()["alerts_raised"] == []


async def test_heartbeat_existing_active_disk_critical_threshold_raises_alert(admin_client, session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id, mount_path="/mnt/data", is_active=True, critical_threshold_pct=90)
    session.add(disk)
    await session.commit()

    resp = await admin_client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={
            "reachable": True,
            "disks": [{"mount_path": "/mnt/data", "total_bytes": 1000, "free_bytes": 50}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["disks"][0]["free_bytes"] == 50
    assert any(a["alert_type"] == AlertType.DISK_SPACE_CRITICAL.value for a in body["alerts_raised"])


async def test_heartbeat_disk_usage_drops_below_threshold_resolves_alert(admin_client, session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(
        server.id, mount_path="/mnt/data", is_active=True, warning_threshold_pct=80, critical_threshold_pct=90
    )
    session.add(disk)
    await session.commit()

    critical = await admin_client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={
            "reachable": True,
            "disks": [{"mount_path": "/mnt/data", "total_bytes": 1000, "free_bytes": 50}],
        },
    )
    assert any(a["alert_type"] == AlertType.DISK_SPACE_CRITICAL.value for a in critical.json()["alerts_raised"])

    recovered = await admin_client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={
            "reachable": True,
            "disks": [{"mount_path": "/mnt/data", "total_bytes": 1000, "free_bytes": 900}],
        },
    )
    resolved_types = [a["alert_type"] for a in recovered.json()["alerts_resolved"]]
    assert AlertType.DISK_SPACE_CRITICAL.value in resolved_types


async def test_heartbeat_validation_error_on_bad_disk_usage(admin_client, session):
    server = build_server()
    session.add(server)
    await session.commit()

    resp = await admin_client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={
            "reachable": True,
            "disks": [{"mount_path": "/mnt/x", "total_bytes": 100, "free_bytes": 500}],
        },
    )
    assert resp.status_code == 422


# ==========================================================================
# Extended monitoring: metrics/services on heartbeat + GET .../metrics
# ==========================================================================

_METRICS_PAYLOAD = {
    "cpu_usage_pct": 45.5,
    "memory_used_bytes": 4_000_000_000,
    "memory_total_bytes": 8_000_000_000,
    "top_processes": [
        {"process_name": "sqlservr.exe", "pid": 1234, "cpu_pct": 12.3, "memory_bytes": 500_000_000}
    ],
}


async def test_heartbeat_metrics_populated_round_trips_via_get_metrics(admin_client, session):
    server = build_server()
    session.add(server)
    await session.commit()

    hb = await admin_client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={"reachable": True, "metrics": _METRICS_PAYLOAD},
    )
    assert hb.status_code == 200

    resp = await admin_client.get(f"/api/servers/{server.id}/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_id"] == server.id
    metrics = body["metrics"]
    assert metrics is not None
    assert metrics["cpu_usage_pct"] == 45.5
    assert metrics["memory_used_bytes"] == 4_000_000_000
    assert metrics["memory_total_bytes"] == 8_000_000_000
    assert metrics["memory_used_pct"] == 50.0
    assert metrics["top_processes"] == _METRICS_PAYLOAD["top_processes"]
    assert metrics["checked_at"] is not None


async def test_heartbeat_metrics_and_services_both_null_creates_no_metrics_row(admin_client, session):
    server = build_server()
    session.add(server)
    await session.commit()

    hb = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert hb.status_code == 200

    resp = await admin_client.get(f"/api/servers/{server.id}/metrics")
    assert resp.status_code == 200
    assert resp.json()["metrics"] is None

    from sqlalchemy import func, select

    from app.models.server_metrics import ServerMetrics

    count = (
        await session.execute(
            select(func.count()).select_from(ServerMetrics).where(ServerMetrics.server_id == server.id)
        )
    ).scalar_one()
    assert count == 0


async def test_heartbeat_services_null_vs_empty_list_semantics(admin_client, session):
    """The crux of this feature: `services: null`/omitted must leave the
    stored `services_status` untouched, while an explicit `services: []`
    must wipe it to empty -- these are NOT equivalent.
    """
    server = build_server()
    session.add(server)
    await session.commit()

    real_services = [{"service_name": "MSSQLSERVER", "status": "Running"}]

    # 1. First heartbeat sets services_status to a real value.
    r1 = await admin_client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={"reachable": True, "services": real_services},
    )
    assert r1.status_code == 200

    get1 = await admin_client.get(f"/api/servers/{server.id}/metrics")
    assert get1.json()["metrics"]["services_status"] == real_services

    # 2. Follow-up heartbeat with services omitted (null) must NOT wipe it.
    r2 = await admin_client.post(f"/api/agents/{server.id}/heartbeat", json={"reachable": True})
    assert r2.status_code == 200

    get2 = await admin_client.get(f"/api/servers/{server.id}/metrics")
    assert get2.json()["metrics"]["services_status"] == real_services

    # 3. A heartbeat with an EXPLICIT empty list DOES wipe it to [].
    r3 = await admin_client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={"reachable": True, "services": []},
    )
    assert r3.status_code == 200

    get3 = await admin_client.get(f"/api/servers/{server.id}/metrics")
    assert get3.json()["metrics"]["services_status"] == []


# ==========================================================================
# GET /api/agents/{server_id}/monitoring-config
# ==========================================================================


async def test_monitoring_config_none_override_resolves_to_global_default(admin_client, session, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_MONITORED_SERVICE_NAMES", "MSSQLSERVER,SQLSERVERAGENT")
    server = build_server(monitored_service_names=None)
    session.add(server)
    await session.commit()

    resp = await admin_client.get(f"/api/agents/{server.id}/monitoring-config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_id"] == server.id
    assert body["service_names"] == ["MSSQLSERVER", "SQLSERVERAGENT"]
    assert body["service_names"] == settings.default_monitored_service_names


async def test_monitoring_config_explicit_empty_override_is_not_collapsed_into_default(
    admin_client, session, monkeypatch
):
    """Regression guard for the `is not None` vs truthiness distinction: an
    explicit `[]` override must be returned as-is, NOT silently fall
    through to the (non-empty) global default.
    """
    monkeypatch.setattr(settings, "DEFAULT_MONITORED_SERVICE_NAMES", "MSSQLSERVER,SQLSERVERAGENT")
    server = build_server(monitored_service_names=[])
    session.add(server)
    await session.commit()

    resp = await admin_client.get(f"/api/agents/{server.id}/monitoring-config")
    assert resp.status_code == 200
    assert resp.json()["service_names"] == []


async def test_monitoring_config_populated_override_returned_exactly(admin_client, session, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_MONITORED_SERVICE_NAMES", "SOME_OTHER_SERVICE")
    server = build_server(monitored_service_names=["CustomService1", "CustomService2"])
    session.add(server)
    await session.commit()

    resp = await admin_client.get(f"/api/agents/{server.id}/monitoring-config")
    assert resp.status_code == 200
    assert resp.json()["service_names"] == ["CustomService1", "CustomService2"]


async def test_monitoring_config_404_for_missing_server(admin_client):
    resp = await admin_client.get("/api/agents/999999/monitoring-config")
    assert resp.status_code == 404


# ==========================================================================
# GET /api/agents/{server_id}/jobs
# ==========================================================================


async def _server_with_disk(session, **server_overrides):
    server = build_server(**server_overrides)
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    return server, disk


async def test_list_agent_jobs_missing_server_is_404(admin_client):
    resp = await admin_client.get("/api/agents/999999/jobs")
    assert resp.status_code == 404


async def test_list_agent_jobs_empty_list_for_server_with_no_jobs(admin_client, session):
    server, _disk = await _server_with_disk(session)

    resp = await admin_client.get(f"/api/agents/{server.id}/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_agent_jobs_returns_only_enabled_jobs_for_this_server(admin_client, session):
    server1, disk1 = await _server_with_disk(session)
    server2, disk2 = await _server_with_disk(session)

    enabled_1 = build_backup_job(server1.id, disk1.id, is_enabled=True)
    enabled_2 = build_backup_job(server1.id, disk1.id, is_enabled=True)
    disabled = build_backup_job(server1.id, disk1.id, is_enabled=False)
    other_server_job = build_backup_job(server2.id, disk2.id, is_enabled=True)
    session.add_all([enabled_1, enabled_2, disabled, other_server_job])
    await session.commit()

    resp = await admin_client.get(f"/api/agents/{server1.id}/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    returned_ids = {item["id"] for item in body["items"]}
    assert returned_ids == {enabled_1.id, enabled_2.id}
    assert disabled.id not in returned_ids
    assert other_server_job.id not in returned_ids
    for item in body["items"]:
        assert item["server_id"] == server1.id
        assert item["is_enabled"] is True


async def test_list_agent_jobs_pagination(admin_client, session):
    server, disk = await _server_with_disk(session)
    jobs = [build_backup_job(server.id, disk.id, is_enabled=True) for _ in range(5)]
    session.add_all(jobs)
    await session.commit()
    all_ids = {j.id for j in jobs}

    page1 = await admin_client.get(f"/api/agents/{server.id}/jobs", params={"limit": 2, "offset": 0})
    page2 = await admin_client.get(f"/api/agents/{server.id}/jobs", params={"limit": 2, "offset": 2})
    page3 = await admin_client.get(f"/api/agents/{server.id}/jobs", params={"limit": 2, "offset": 4})

    assert page1.json()["total"] == 5
    assert len(page1.json()["items"]) == 2
    assert len(page2.json()["items"]) == 2
    assert len(page3.json()["items"]) == 1

    seen_ids = set()
    for page in (page1, page2, page3):
        for item in page.json()["items"]:
            assert item["id"] not in seen_ids
            seen_ids.add(item["id"])
    assert seen_ids == all_ids


# ==========================================================================
# GET /api/agents/{server_id}/connection-config
# ==========================================================================


def _connection_config_headers() -> dict:
    return {"X-Connection-Config-Key": settings.CONNECTION_CONFIG_API_KEY}


async def _server_with_credentials(session, **overrides):
    defaults = dict(
        username_encrypted=encrypt_secret("agent-user"),
        password_encrypted=encrypt_secret("agent-pw-s3cr3t"),
        ssh_private_key_encrypted=encrypt_secret("-----BEGIN KEY-----fakekeydata-----END KEY-----"),
    )
    defaults.update(overrides)
    server = build_server(**defaults)
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return server


async def _last_access_log_row(session, server_id: int) -> AgentCredentialAccessLog:
    stmt = (
        select(AgentCredentialAccessLog)
        .where(AgentCredentialAccessLog.server_id == server_id)
        .order_by(AgentCredentialAccessLog.id.desc())
    )
    row = (await session.execute(stmt)).scalars().first()
    assert row is not None, f"no AgentCredentialAccessLog row found for server_id={server_id}"
    return row


async def test_connection_config_happy_path_round_trip(client, session):
    server = await _server_with_credentials(session)

    resp = await client.get(
        f"/api/agents/{server.id}/connection-config", headers=_connection_config_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_id"] == server.id
    assert body["host"] == server.host
    assert body["port"] == server.port
    assert body["protocol"] == server.protocol.value
    assert body["username"] == "agent-user"
    assert body["password"] == "agent-pw-s3cr3t"
    assert body["ssh_private_key"] == "-----BEGIN KEY-----fakekeydata-----END KEY-----"

    # The ciphertext form must never leak into the response.
    assert server.password_encrypted not in resp.text
    assert server.username_encrypted not in resp.text
    assert server.ssh_private_key_encrypted not in resp.text

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.SUCCESS
    assert row.auth_method == AgentCredentialAccessAuthMethod.CONNECTION_CONFIG_KEY
    assert row.server_id == server.id


async def test_connection_config_404_for_missing_server(client, session):
    resp = await client.get(
        "/api/agents/999999/connection-config", headers=_connection_config_headers()
    )
    assert resp.status_code == 404

    row = await _last_access_log_row(session, 999999)
    assert row.outcome == AgentCredentialAccessOutcome.NOT_FOUND
    assert row.auth_method == AgentCredentialAccessAuthMethod.CONNECTION_CONFIG_KEY


async def test_connection_config_409_for_soft_deleted_server(client, session):
    server = await _server_with_credentials(session, is_deleted=True)

    resp = await client.get(
        f"/api/agents/{server.id}/connection-config", headers=_connection_config_headers()
    )
    assert resp.status_code == 409

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.DENIED_DELETED


async def test_connection_config_403_for_disabled_server_but_heartbeat_still_works(client, session):
    """Asymmetric-by-design: heartbeat still accepts a DISABLED server
    (see test_heartbeat_disabled_server_never_reactivated above), but
    connection-config must reject it outright. Regression guard for that
    asymmetry -- see app/routers/agents.py::get_agent_connection_config
    docstring and app/core/auth.py::require_connection_config_key.
    """
    server = await _server_with_credentials(session, status=ServerStatus.DISABLED)

    config_resp = await client.get(
        f"/api/agents/{server.id}/connection-config", headers=_connection_config_headers()
    )
    assert config_resp.status_code == 403

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.DENIED_DISABLED

    heartbeat_resp = await client.post(
        f"/api/agents/{server.id}/heartbeat",
        json={"reachable": True},
        headers={"X-Agent-Key": settings.AGENT_API_KEY},
    )
    assert heartbeat_resp.status_code == 200
    assert heartbeat_resp.json()["server"]["status"] == "DISABLED"


async def test_connection_config_409_for_no_credentials_configured(client, session):
    server = build_server()
    session.add(server)
    await session.commit()

    resp = await client.get(
        f"/api/agents/{server.id}/connection-config", headers=_connection_config_headers()
    )
    assert resp.status_code == 409

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.DENIED_NO_CREDENTIALS


async def test_connection_config_missing_key_no_jwt_is_401(client, session):
    server = await _server_with_credentials(session)

    resp = await client.get(f"/api/agents/{server.id}/connection-config")
    assert resp.status_code == 401

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.UNAUTHORIZED
    assert row.auth_method == AgentCredentialAccessAuthMethod.ADMIN_JWT
    assert row.admin_username is None


async def test_connection_config_bad_key_no_jwt_is_401(client, session):
    server = await _server_with_credentials(session)

    resp = await client.get(
        f"/api/agents/{server.id}/connection-config",
        headers={"X-Connection-Config-Key": "not-the-real-key"},
    )
    assert resp.status_code == 401

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.UNAUTHORIZED
    assert row.auth_method == AgentCredentialAccessAuthMethod.CONNECTION_CONFIG_KEY


async def test_connection_config_valid_agent_key_alone_does_not_authorize(client, session):
    """The whole point of the separate secret: a valid X-Agent-Key (the
    widely-distributed general agent key) must NOT satisfy
    require_connection_config_key on its own.
    """
    server = await _server_with_credentials(session)

    resp = await client.get(
        f"/api/agents/{server.id}/connection-config",
        headers={"X-Agent-Key": settings.AGENT_API_KEY},
    )
    assert resp.status_code == 401

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.UNAUTHORIZED


async def test_connection_config_operator_jwt_is_403(operator_client, session):
    server = await _server_with_credentials(session)

    resp = await operator_client.get(f"/api/agents/{server.id}/connection-config")
    assert resp.status_code == 403

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.UNAUTHORIZED
    assert row.auth_method == AgentCredentialAccessAuthMethod.ADMIN_JWT
    assert row.admin_username == "operator-tester"


async def test_connection_config_admin_jwt_bypasses_key_header(admin_client, session):
    server = await _server_with_credentials(session)

    resp = await admin_client.get(f"/api/agents/{server.id}/connection-config")
    assert resp.status_code == 200
    assert resp.json()["password"] == "agent-pw-s3cr3t"

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.SUCCESS
    assert row.auth_method == AgentCredentialAccessAuthMethod.ADMIN_JWT
    assert row.admin_username == "admin-tester"


async def test_connection_config_decryption_failed_is_500_and_audited(client, session):
    server = await _server_with_credentials(session)
    # Corrupt the stored ciphertext directly (simulates a bad FERNET_KEY
    # rotation or bit-rot) -- decrypt_secret must raise InvalidToken.
    server.password_encrypted = "not-a-valid-fernet-token"
    await session.commit()

    resp = await client.get(
        f"/api/agents/{server.id}/connection-config", headers=_connection_config_headers()
    )
    assert resp.status_code == 500

    row = await _last_access_log_row(session, server.id)
    assert row.outcome == AgentCredentialAccessOutcome.DECRYPTION_FAILED
    assert row.auth_method == AgentCredentialAccessAuthMethod.CONNECTION_CONFIG_KEY


# ==========================================================================
# GET /api/agents/credential-access-log
# ==========================================================================


async def test_credential_access_log_requires_admin_403_for_operator(operator_client):
    resp = await operator_client.get("/api/agents/credential-access-log")
    assert resp.status_code == 403


async def test_credential_access_log_401_for_no_auth(client):
    resp = await client.get("/api/agents/credential-access-log")
    assert resp.status_code == 401


async def test_credential_access_log_pagination(admin_client, session):
    server = await _server_with_credentials(session)
    # Generate 5 distinct access-log rows by calling the endpoint 5 times
    # with alternating valid/invalid keys.
    for i in range(5):
        headers = (
            _connection_config_headers() if i % 2 == 0 else {"X-Connection-Config-Key": "bad"}
        )
        await admin_client.get(f"/api/agents/{server.id}/connection-config", headers=headers)

    page1 = await admin_client.get(
        "/api/agents/credential-access-log", params={"server_id": server.id, "limit": 2, "offset": 0}
    )
    page2 = await admin_client.get(
        "/api/agents/credential-access-log", params={"server_id": server.id, "limit": 2, "offset": 2}
    )
    assert page1.status_code == 200
    assert page1.json()["total"] == 5
    assert len(page1.json()["items"]) == 2
    assert len(page2.json()["items"]) == 2
    page1_ids = {i["id"] for i in page1.json()["items"]}
    page2_ids = {i["id"] for i in page2.json()["items"]}
    assert page1_ids.isdisjoint(page2_ids)


async def test_credential_access_log_server_id_filter(admin_client, session):
    server1 = await _server_with_credentials(session)
    server2 = await _server_with_credentials(session)

    await admin_client.get(
        f"/api/agents/{server1.id}/connection-config", headers=_connection_config_headers()
    )
    await admin_client.get(
        f"/api/agents/{server2.id}/connection-config", headers=_connection_config_headers()
    )

    resp = await admin_client.get(
        "/api/agents/credential-access-log", params={"server_id": server1.id}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["server_id"] == server1.id for item in body["items"])


async def test_credential_access_log_never_leaks_secret_or_raw_key(admin_client, session):
    server = await _server_with_credentials(session)

    resp = await admin_client.get(
        f"/api/agents/{server.id}/connection-config", headers=_connection_config_headers()
    )
    assert resp.status_code == 200

    log_resp = await admin_client.get("/api/agents/credential-access-log")
    assert log_resp.status_code == 200
    assert "agent-pw-s3cr3t" not in log_resp.text
    assert "agent-user" not in log_resp.text
    assert "fakekeydata" not in log_resp.text
    assert settings.CONNECTION_CONFIG_API_KEY not in log_resp.text
    assert server.password_encrypted not in log_resp.text
