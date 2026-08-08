"""HTTP-layer tests for /api/alerts (app/routers/alerts.py)."""
from __future__ import annotations

from app.models.enums import AlertSeverity, AlertStatus
from conftest import build_alert


async def test_list_alerts_filters_by_status_and_severity(client, session):
    active_warning = build_alert(status=AlertStatus.ACTIVE, severity=AlertSeverity.WARNING)
    active_critical = build_alert(status=AlertStatus.ACTIVE, severity=AlertSeverity.CRITICAL)
    resolved = build_alert(status=AlertStatus.RESOLVED, severity=AlertSeverity.WARNING)
    session.add_all([active_warning, active_critical, resolved])
    await session.commit()

    resp = await client.get("/api/alerts", params={"status": "ACTIVE"})
    ids = {a["id"] for a in resp.json()["items"]}
    assert active_warning.id in ids
    assert active_critical.id in ids
    assert resolved.id not in ids

    resp2 = await client.get("/api/alerts", params={"severity": "CRITICAL"})
    ids2 = {a["id"] for a in resp2.json()["items"]}
    assert active_critical.id in ids2
    assert active_warning.id not in ids2


async def test_acknowledge_alert_happy_path(client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await client.post(f"/api/alerts/{alert.id}/acknowledge", json={"acknowledged_by": "bob"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ACKNOWLEDGED"
    assert body["acknowledged_by"] == "bob"
    assert body["acknowledged_at"] is not None


async def test_acknowledge_already_resolved_alert_is_409(client, session):
    alert = build_alert(status=AlertStatus.RESOLVED)
    session.add(alert)
    await session.commit()

    resp = await client.post(f"/api/alerts/{alert.id}/acknowledge", json={"acknowledged_by": "bob"})
    assert resp.status_code == 409


async def test_acknowledge_is_idempotent_when_already_acknowledged(client, session):
    alert = build_alert(status=AlertStatus.ACKNOWLEDGED, acknowledged_by="alice")
    session.add(alert)
    await session.commit()

    resp = await client.post(f"/api/alerts/{alert.id}/acknowledge", json={"acknowledged_by": "bob"})
    assert resp.status_code == 200
    assert resp.json()["acknowledged_by"] == "bob"


async def test_acknowledge_alert_missing_body_field_is_422(client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await client.post(f"/api/alerts/{alert.id}/acknowledge", json={})
    assert resp.status_code == 422


async def test_acknowledge_alert_404(client):
    resp = await client.post("/api/alerts/999999/acknowledge", json={"acknowledged_by": "bob"})
    assert resp.status_code == 404


async def test_resolve_alert_happy_path(client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await client.post(f"/api/alerts/{alert.id}/resolve", json={"resolved_note": "fixed it"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "RESOLVED"
    assert body["resolved_note"] == "fixed it"
    assert body["resolved_at"] is not None


async def test_resolve_alert_without_note_is_ok(client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await client.post(f"/api/alerts/{alert.id}/resolve", json={})
    assert resp.status_code == 200
    assert resp.json()["resolved_note"] is None


async def test_resolve_already_resolved_alert_is_409(client, session):
    alert = build_alert(status=AlertStatus.RESOLVED)
    session.add(alert)
    await session.commit()

    resp = await client.post(f"/api/alerts/{alert.id}/resolve", json={})
    assert resp.status_code == 409


async def test_resolve_alert_404(client):
    resp = await client.post("/api/alerts/999999/resolve", json={})
    assert resp.status_code == 404
