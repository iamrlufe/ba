"""HTTP-layer tests for /api/agents (app/routers/agents.py).

Note: there is no ORM entity `Agent` -- `server_id` here is literally
`Server.id`, see the router module docstring.
"""
from __future__ import annotations

from app.models.enums import AlertType, ServerStatus
from conftest import build_disk, build_server


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
