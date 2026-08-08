"""HTTP-layer tests for /api/alerts (app/routers/alerts.py)."""
from __future__ import annotations

from app.models.enums import AlertSeverity, AlertStatus
from tests.conftest import build_alert


async def test_list_alerts_filters_by_status_and_severity(admin_client, session):
    active_warning = build_alert(status=AlertStatus.ACTIVE, severity=AlertSeverity.WARNING)
    active_critical = build_alert(status=AlertStatus.ACTIVE, severity=AlertSeverity.CRITICAL)
    resolved = build_alert(status=AlertStatus.RESOLVED, severity=AlertSeverity.WARNING)
    session.add_all([active_warning, active_critical, resolved])
    await session.commit()

    resp = await admin_client.get("/api/alerts", params={"status": "ACTIVE"})
    ids = {a["id"] for a in resp.json()["items"]}
    assert active_warning.id in ids
    assert active_critical.id in ids
    assert resolved.id not in ids

    resp2 = await admin_client.get("/api/alerts", params={"severity": "CRITICAL"})
    ids2 = {a["id"] for a in resp2.json()["items"]}
    assert active_critical.id in ids2
    assert active_warning.id not in ids2


async def test_acknowledge_alert_happy_path(admin_client, admin_user, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await admin_client.post(f"/api/alerts/{alert.id}/acknowledge", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ACKNOWLEDGED"
    # acknowledged_by is derived from the JWT principal, not client-supplied.
    assert body["acknowledged_by"] == admin_user.username
    assert body["acknowledged_at"] is not None


async def test_acknowledge_alert_rejects_client_supplied_acknowledged_by(admin_client, session):
    """`acknowledged_by` is no longer a request-body field -- supplying it
    must 422 (extra="forbid")."""
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await admin_client.post(
        f"/api/alerts/{alert.id}/acknowledge", json={"acknowledged_by": "someone-else"}
    )
    assert resp.status_code == 422


async def test_acknowledge_alert_forbidden_for_operator(operator_client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await operator_client.post(f"/api/alerts/{alert.id}/acknowledge", json={})
    assert resp.status_code == 403


async def test_acknowledge_already_resolved_alert_is_409(admin_client, session):
    alert = build_alert(status=AlertStatus.RESOLVED)
    session.add(alert)
    await session.commit()

    resp = await admin_client.post(f"/api/alerts/{alert.id}/acknowledge", json={})
    assert resp.status_code == 409


async def test_acknowledge_is_idempotent_when_already_acknowledged(admin_client, admin_user, session):
    alert = build_alert(status=AlertStatus.ACKNOWLEDGED, acknowledged_by="alice")
    session.add(alert)
    await session.commit()

    resp = await admin_client.post(f"/api/alerts/{alert.id}/acknowledge", json={})
    assert resp.status_code == 200
    assert resp.json()["acknowledged_by"] == admin_user.username


async def test_acknowledge_alert_404(admin_client):
    resp = await admin_client.post("/api/alerts/999999/acknowledge", json={})
    assert resp.status_code == 404


async def test_resolve_alert_happy_path(admin_client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await admin_client.post(f"/api/alerts/{alert.id}/resolve", json={"resolved_note": "fixed it"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "RESOLVED"
    assert body["resolved_note"] == "fixed it"
    assert body["resolved_at"] is not None


async def test_resolve_alert_without_note_is_ok(admin_client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await admin_client.post(f"/api/alerts/{alert.id}/resolve", json={})
    assert resp.status_code == 200
    assert resp.json()["resolved_note"] is None


async def test_resolve_already_resolved_alert_is_409(admin_client, session):
    alert = build_alert(status=AlertStatus.RESOLVED)
    session.add(alert)
    await session.commit()

    resp = await admin_client.post(f"/api/alerts/{alert.id}/resolve", json={})
    assert resp.status_code == 409


async def test_resolve_alert_forbidden_for_operator(operator_client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await operator_client.post(f"/api/alerts/{alert.id}/resolve", json={})
    assert resp.status_code == 403


async def test_resolve_alert_404(admin_client):
    resp = await admin_client.post("/api/alerts/999999/resolve", json={})
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# POST /api/alerts/{id}/mark-telegram-delivered
# --------------------------------------------------------------------------


async def test_mark_telegram_delivered_sets_timestamp_on_first_call(admin_client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()
    assert alert.delivered_telegram_at is None

    resp = await admin_client.post(f"/api/alerts/{alert.id}/mark-telegram-delivered", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered_telegram_at"] is not None


async def test_mark_telegram_delivered_is_idempotent_timestamp_unchanged(admin_client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    first = await admin_client.post(f"/api/alerts/{alert.id}/mark-telegram-delivered", json={})
    assert first.status_code == 200
    first_ts = first.json()["delivered_telegram_at"]
    assert first_ts is not None

    second = await admin_client.post(f"/api/alerts/{alert.id}/mark-telegram-delivered", json={})
    assert second.status_code == 200
    second_ts = second.json()["delivered_telegram_at"]
    # No-op on an already-delivered alert -- the timestamp must not move.
    assert second_ts == first_ts


async def test_mark_telegram_delivered_404(admin_client):
    resp = await admin_client.post("/api/alerts/999999/mark-telegram-delivered", json={})
    assert resp.status_code == 404


async def test_mark_telegram_delivered_allows_any_authenticated_role(operator_client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await operator_client.post(f"/api/alerts/{alert.id}/mark-telegram-delivered", json={})
    assert resp.status_code == 200


async def test_mark_telegram_delivered_requires_auth(client, session):
    alert = build_alert()
    session.add(alert)
    await session.commit()

    resp = await client.post(f"/api/alerts/{alert.id}/mark-telegram-delivered", json={})
    assert resp.status_code == 401
