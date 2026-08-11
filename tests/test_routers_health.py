"""HTTP-layer test for GET /healthz (app/routers/health.py).

Docker HEALTHCHECK target: deliberately no DB/auth coupling, so this just
confirms it's wired up in app/main.py and returns the expected liveness
payload with no Authorization header.
"""
from __future__ import annotations


async def test_healthz_returns_ok_with_no_auth(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
