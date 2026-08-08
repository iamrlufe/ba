"""HTTP-layer tests for /api/users (app/routers/users.py) -- admin-only
user provisioning."""
from __future__ import annotations

from app.models.enums import UserRole


async def test_create_user_happy_path(admin_client):
    resp = await admin_client.post(
        "/api/users", json={"username": "newop", "password": "supersecret1", "role": "OPERATOR"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "newop"
    assert body["role"] == "OPERATOR"
    assert body["is_active"] is True
    assert "password" not in body
    assert "hashed_password" not in body


async def test_create_user_duplicate_username_is_409(admin_client):
    payload = {"username": "dupuser", "password": "supersecret1", "role": "OPERATOR"}
    r1 = await admin_client.post("/api/users", json=payload)
    assert r1.status_code == 201
    r2 = await admin_client.post("/api/users", json=payload)
    assert r2.status_code == 409


async def test_create_user_password_too_short_is_422(admin_client):
    resp = await admin_client.post(
        "/api/users", json={"username": "shortpw", "password": "short", "role": "OPERATOR"}
    )
    assert resp.status_code == 422


async def test_create_user_username_with_whitespace_is_422(admin_client):
    resp = await admin_client.post(
        "/api/users", json={"username": "has space", "password": "supersecret1", "role": "OPERATOR"}
    )
    assert resp.status_code == 422


async def test_create_user_forbidden_for_operator(operator_client):
    resp = await operator_client.post(
        "/api/users", json={"username": "shouldfail", "password": "supersecret1", "role": "OPERATOR"}
    )
    assert resp.status_code == 403


async def test_create_user_requires_auth(client):
    resp = await client.post(
        "/api/users", json={"username": "noauth", "password": "supersecret1", "role": "OPERATOR"}
    )
    assert resp.status_code == 401


async def test_new_user_can_login(admin_client, client):
    create_resp = await admin_client.post(
        "/api/users", json={"username": "loginable", "password": "supersecret1", "role": "OPERATOR"}
    )
    assert create_resp.status_code == 201

    login_resp = await client.post(
        "/api/auth/login", json={"username": "loginable", "password": "supersecret1"}
    )
    assert login_resp.status_code == 200


async def test_list_users_happy_path(admin_client, admin_user):
    resp = await admin_client.get("/api/users")
    assert resp.status_code == 200
    body = resp.json()
    usernames = {u["username"] for u in body["items"]}
    assert admin_user.username in usernames
    assert body["total"] >= 1


async def test_list_users_forbidden_for_operator(operator_client):
    resp = await operator_client.get("/api/users")
    assert resp.status_code == 403


async def test_list_users_requires_auth(client):
    resp = await client.get("/api/users")
    assert resp.status_code == 401
