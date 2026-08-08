"""HTTP-layer tests for /api/auth (app/routers/auth.py)."""
from __future__ import annotations

import app.routers.auth as auth_router
from app.core.auth import hash_password
from app.models.enums import UserRole
from tests.conftest import build_user


async def test_login_happy_path(client, session):
    user = build_user(username="alice", hashed_password=hash_password("correct-horse-battery"))
    session.add(user)
    await session.commit()

    resp = await client.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-battery"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]
    assert body["expires_in"] > 0


async def test_login_unknown_username_is_401_generic(client):
    resp = await client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


async def test_login_wrong_password_is_401_generic(client, session):
    user = build_user(username="bob", hashed_password=hash_password("realpassword"))
    session.add(user)
    await session.commit()

    resp = await client.post("/api/auth/login", json={"username": "bob", "password": "wrongpassword"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


async def test_login_inactive_user_is_401_generic(client, session):
    user = build_user(
        username="carol", hashed_password=hash_password("realpassword"), is_active=False
    )
    session.add(user)
    await session.commit()

    resp = await client.post("/api/auth/login", json={"username": "carol", "password": "realpassword"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


async def test_login_response_never_contains_password_fields(client, session):
    user = build_user(username="dave", hashed_password=hash_password("realpassword"))
    session.add(user)
    await session.commit()

    resp = await client.post("/api/auth/login", json={"username": "dave", "password": "realpassword"})
    assert resp.status_code == 200
    body = resp.json()
    assert "password" not in body
    assert "hashed_password" not in body


async def test_me_requires_auth(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_me_returns_current_user(admin_client, admin_user):
    resp = await admin_client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == admin_user.username
    assert body["role"] == UserRole.ADMIN.value
    assert "hashed_password" not in body
    assert "password" not in body


async def test_me_rejects_invalid_token(client):
    client.headers["Authorization"] = "Bearer not-a-real-jwt"
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_me_rejects_inactive_user_token(client, session):
    from tests.conftest import mint_token

    user = build_user(username="eve", role=UserRole.OPERATOR, is_active=False)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    token = mint_token(user.id, user.username, user.role)

    client.headers["Authorization"] = f"Bearer {token}"
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# Regression coverage for the username-enumeration timing side-channel fix:
# `login` must call `verify_password` unconditionally, against either the
# real user's hash or `_DUMMY_HASH`, so a not-found username can never
# short-circuit before paying the same bcrypt cost as a wrong-password
# attempt. This is a functional (not timing-based) regression test -- it
# spies on `app.routers.auth.verify_password` and asserts it is actually
# invoked, and with which hash, rather than measuring wall-clock time
# (which would be flaky in CI).
# --------------------------------------------------------------------------


async def test_login_unknown_username_still_calls_verify_password_with_dummy_hash(client, monkeypatch):
    calls = []
    original_verify_password = auth_router.verify_password

    def _spy(plain_password, hashed_password):
        calls.append((plain_password, hashed_password))
        return original_verify_password(plain_password, hashed_password)

    monkeypatch.setattr(auth_router, "verify_password", _spy)

    resp = await client.post(
        "/api/auth/login", json={"username": "definitely-not-a-real-user", "password": "whatever"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"

    # verify_password must have been called exactly once, against the
    # precomputed dummy hash (not skipped via short-circuiting `or`, which
    # was the original bug -- that would leave `calls` empty here).
    assert len(calls) == 1
    called_password, called_hash = calls[0]
    assert called_password == "whatever"
    assert called_hash == auth_router._DUMMY_HASH


async def test_login_known_username_calls_verify_password_with_real_hash(client, session, monkeypatch):
    user = build_user(username="frank", hashed_password=hash_password("realpassword"))
    session.add(user)
    await session.commit()
    await session.refresh(user)

    calls = []
    original_verify_password = auth_router.verify_password

    def _spy(plain_password, hashed_password):
        calls.append((plain_password, hashed_password))
        return original_verify_password(plain_password, hashed_password)

    monkeypatch.setattr(auth_router, "verify_password", _spy)

    resp = await client.post(
        "/api/auth/login", json={"username": "frank", "password": "wrongpassword"}
    )
    assert resp.status_code == 401

    assert len(calls) == 1
    called_password, called_hash = calls[0]
    assert called_password == "wrongpassword"
    # Called against the real user's hash, not the dummy -- proves the
    # dummy-hash substitution only applies to the not-found case.
    assert called_hash == user.hashed_password
    assert called_hash != auth_router._DUMMY_HASH


async def test_login_correct_credentials_token_accepted_by_me(client, session):
    user = build_user(username="grace", hashed_password=hash_password("correctpassword1"))
    session.add(user)
    await session.commit()

    login_resp = await client.post(
        "/api/auth/login", json={"username": "grace", "password": "correctpassword1"}
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    client.headers["Authorization"] = f"Bearer {token}"
    me_resp = await client.get("/api/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "grace"


# --------------------------------------------------------------------------
# POST /api/auth/telegram-link
# --------------------------------------------------------------------------


async def test_telegram_link_happy_path(client, session):
    from app.core.auth import decode_access_token

    user = build_user(username="tg-alice", hashed_password=hash_password("correct-horse-battery"))
    session.add(user)
    await session.commit()

    resp = await client.post(
        "/api/auth/telegram-link",
        json={
            "username": "tg-alice",
            "password": "correct-horse-battery",
            "telegram_user_id": 555111,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "tg-alice"
    assert body["role"] == UserRole.OPERATOR.value
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert isinstance(body["bot_access_token"], str) and body["bot_access_token"]

    payload = decode_access_token(body["bot_access_token"])
    assert payload["username"] == "tg-alice"
    assert payload["scope"] == "telegram_bot"

    await session.refresh(user)
    assert user.telegram_user_id == 555111
    # Persisted encrypted, never the raw JWT itself.
    assert user.telegram_bot_token_encrypted is not None
    assert user.telegram_bot_token_encrypted != body["bot_access_token"]


async def test_telegram_link_wrong_password_is_401_generic(client, session):
    user = build_user(username="tg-bob", hashed_password=hash_password("realpassword"))
    session.add(user)
    await session.commit()

    resp = await client.post(
        "/api/auth/telegram-link",
        json={"username": "tg-bob", "password": "wrongpassword", "telegram_user_id": 2},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


async def test_telegram_link_unknown_username_is_401_generic(client):
    resp = await client.post(
        "/api/auth/telegram-link",
        json={"username": "nobody-tg", "password": "whatever", "telegram_user_id": 3},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


async def test_telegram_link_inactive_user_is_401_generic(client, session):
    user = build_user(
        username="tg-carol", hashed_password=hash_password("realpassword"), is_active=False
    )
    session.add(user)
    await session.commit()

    resp = await client.post(
        "/api/auth/telegram-link",
        json={"username": "tg-carol", "password": "realpassword", "telegram_user_id": 4},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid username or password"


async def test_telegram_link_already_linked_to_different_user_is_409(client, session):
    user1 = build_user(username="tg-dave", hashed_password=hash_password("pw1-secret"))
    user2 = build_user(username="tg-erin", hashed_password=hash_password("pw2-secret"))
    session.add_all([user1, user2])
    await session.commit()

    first = await client.post(
        "/api/auth/telegram-link",
        json={"username": "tg-dave", "password": "pw1-secret", "telegram_user_id": 999888},
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/auth/telegram-link",
        json={"username": "tg-erin", "password": "pw2-secret", "telegram_user_id": 999888},
    )
    assert second.status_code == 409

    await session.refresh(user2)
    assert user2.telegram_user_id is None
