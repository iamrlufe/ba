"""Tests for the /ws/job-runs/{job_run_id} websocket (app/routers/job_run_ws.py).

Regression coverage for 2.c in the task spec: the handler used to check the
JobRun's status BEFORE registering with `app.core.ws_manager.manager`, so a
run that completed in the gap between `websocket.accept()` and the status
read would leave a connected client hanging forever (the completion's
broadcast + close_all would already have fired for a socket that wasn't
registered yet). The fix registers with the manager *first*, then reads
status -- guaranteeing the client always gets either the live broadcast or
an explicit terminal-state message before close.

Uses `httpx_ws` (added as a dev dependency for this task -- see final
report) layered on `httpx.AsyncClient` via `httpx_ws.transport.
ASGIWebSocketTransport`, so these tests share the same asyncio event loop
and DB engine/session machinery as the rest of the async test suite
(unlike Starlette's `TestClient.websocket_connect`, which runs the ASGI app
in a separate thread with its own event loop -- incompatible with the
`StaticPool`-backed aiosqlite engine used here, whose connection is bound
to the loop it was first used on).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from httpx_ws import aconnect_ws

from app.core.config import settings
from app.core.db import get_db
from app.core.ws_manager import JobRunConnectionManager, manager
from app.main import app as fastapi_app
from app.models.enums import JobRunStatus, UserRole
from tests.conftest import build_backup_job, build_disk, build_job_run, build_server, build_user, mint_token


async def _enabled_job(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id, is_enabled=True)
    session.add(job)
    await session.commit()
    return job


async def _authed_user_and_token(session):
    """A live User row (needed by get_current_user/the WS token check) plus
    a matching JWT. ADMIN so the same token also satisfies
    require_admin_or_agent_key on the HTTP `.../complete` calls these tests
    issue against `ws_client`."""
    user = build_user(role=UserRole.ADMIN)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    token = mint_token(user.id, user.username, user.role)
    return user, token


async def _ws_client(session_maker, token: str):
    """An httpx.AsyncClient wired to the real app over a real ASGI
    websocket-capable transport, sharing the given session_maker's engine
    (see module docstring for why this can't be the plain `client` fixture
    -- that one uses `httpx.ASGITransport`, which is HTTP-only).

    The Authorization header covers the HTTP `.../complete` calls issued
    directly against this client (require_admin_or_agent_key accepts an
    admin JWT); the WS connection itself authenticates separately via a
    `?token=` query param (browsers can't set custom headers on the WS
    handshake), appended by each test at `aconnect_ws(...)` call sites.
    """
    from httpx_ws.transport import ASGIWebSocketTransport

    async def _override_get_db():
        async with session_maker() as s:
            yield s

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGIWebSocketTransport(app=fastapi_app)
    return AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def test_ws_delivers_initial_state_then_final_message_on_completion(session, session_maker):
    """Base case: connect while RUNNING, receive the initial state, then
    the run completes via a real HTTP call -- the socket must receive the
    final terminal-state message (not hang)."""
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()
    run_id = run.id

    _user, token = await _authed_user_and_token(session)

    ws_client = await _ws_client(session_maker, token)
    try:
        async with ws_client:
            async with aconnect_ws(f"/ws/job-runs/{run_id}?token={token}", ws_client) as ws:
                initial = await ws.receive_json()
                assert initial["status"] == "RUNNING"

                complete_resp = await ws_client.post(
                    f"/api/job-runs/{run_id}/complete", json={"status": "SUCCESS"}
                )
                assert complete_resp.status_code == 200

                final = await ws.receive_json()
                assert final["status"] == "SUCCESS"
                assert final["finished_at"] is not None
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


async def test_ws_sends_terminal_state_immediately_when_already_terminal_at_connect_time(session, session_maker):
    """If the run is already terminal by the time the socket connects (the
    "straightforward" already-terminal-by-connect-time case called out in
    the spec), the handler must still send the current terminal state
    itself instead of leaving the client hanging with nothing."""
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.SUCCESS)
    session.add(run)
    await session.commit()
    run_id = run.id

    _user, token = await _authed_user_and_token(session)

    ws_client = await _ws_client(session_maker, token)
    try:
        async with ws_client:
            async with aconnect_ws(f"/ws/job-runs/{run_id}?token={token}", ws_client) as ws:
                msg = await ws.receive_json()
                assert msg["status"] == "SUCCESS"
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


async def test_ws_delivers_final_message_when_run_completes_in_race_window(session, session_maker, monkeypatch):
    """Tightest regression test for 2.c: reproduces the exact race the fix
    targets by monkeypatching `app.core.ws_manager.manager.connect` so that,
    right after the *real* connect() call returns (i.e. the socket is now
    registered with the manager) but *before* job_run_ws.py proceeds to
    read the JobRun's status from the DB, we complete the run out from
    under it via a real HTTP call on a separate session.

    With the old (buggy) ordering -- status read before manager.connect())
    -- this sequencing wouldn't even be expressible: the completion would
    always be invisible to the handler (broadcast to a not-yet-registered
    socket, then the handler would independently read the already-terminal
    status and enter its own terminal branch). With the fix, the socket is
    registered before the completion fires, so it must receive the live
    broadcast produced by the completion, even though that completion lands
    squarely in the connect-to-status-read gap.
    """
    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()
    run_id = run.id

    _user, token = await _authed_user_and_token(session)

    ws_client = await _ws_client(session_maker, token)

    real_connect = JobRunConnectionManager.connect
    triggered = {"done": False}

    async def _connect_then_race_complete(self, job_run_id, websocket):
        await real_connect(self, job_run_id, websocket)
        if job_run_id == run_id and not triggered["done"]:
            triggered["done"] = True
            # Complete the run right here, in the gap between manager
            # registration and job_run_ws.py's subsequent status read.
            resp = await ws_client.post(
                f"/api/job-runs/{run_id}/complete", json={"status": "FAILED"}
            )
            assert resp.status_code == 200

    monkeypatch.setattr(manager, "connect", _connect_then_race_complete.__get__(manager, JobRunConnectionManager))

    try:
        async with ws_client:
            async with aconnect_ws(f"/ws/job-runs/{run_id}?token={token}", ws_client) as ws:
                msg = await ws.receive_json()
                # The socket must see the run as already FAILED -- either
                # via the completion's live broadcast, or via the handler's
                # own already-terminal branch -- but it must NOT hang
                # without receiving anything, and it must NOT see stale
                # RUNNING state with no further message ever arriving.
                assert msg["status"] == "FAILED"
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)

    assert triggered["done"] is True


async def test_ws_rejects_connection_missing_token(session, session_maker):
    """No `?token=` query param -- the handler must accept() then
    immediately close with 4401, not hang or 500."""
    from httpx_ws import WebSocketDisconnect

    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()
    run_id = run.id

    _user, token = await _authed_user_and_token(session)
    ws_client = await _ws_client(session_maker, token)
    try:
        async with ws_client:
            got_disconnect = None
            try:
                async with aconnect_ws(f"/ws/job-runs/{run_id}", ws_client) as ws:
                    await ws.receive_json()
                raise AssertionError("expected the connection to be closed by the server")
            except* WebSocketDisconnect as eg:
                got_disconnect = eg.exceptions[0]
            assert got_disconnect is not None and got_disconnect.code == 4401
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


async def test_ws_rejects_connection_invalid_token(session, session_maker):
    """A garbage/invalid token -- same 4401 rejection as a missing one."""
    from httpx_ws import WebSocketDisconnect

    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()
    run_id = run.id

    _user, token = await _authed_user_and_token(session)
    ws_client = await _ws_client(session_maker, token)
    try:
        async with ws_client:
            got_disconnect = None
            try:
                async with aconnect_ws(
                    f"/ws/job-runs/{run_id}?token=not-a-real-jwt", ws_client
                ) as ws:
                    await ws.receive_json()
                raise AssertionError("expected the connection to be closed by the server")
            except* WebSocketDisconnect as eg:
                got_disconnect = eg.exceptions[0]
            assert got_disconnect is not None and got_disconnect.code == 4401
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


async def test_ws_rejects_connection_expired_token(session, session_maker):
    """A structurally-valid but expired JWT -- same 4401 rejection as a
    missing/garbage token (decode_access_token raises jwt.ExpiredSignatureError,
    a jwt.PyJWTError subclass, which job_run_ws.py must catch)."""
    from httpx_ws import WebSocketDisconnect

    job = await _enabled_job(session)
    run = build_job_run(job.id, status=JobRunStatus.RUNNING)
    session.add(run)
    await session.commit()
    run_id = run.id

    user, valid_token = await _authed_user_and_token(session)

    now = datetime.now(UTC)
    expired_token = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role.value,
            "iat": now - timedelta(minutes=120),
            "exp": now - timedelta(minutes=60),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    # valid_token is used for the HTTP Authorization header on ws_client
    # (unused by this test, but required by _ws_client's signature); the
    # expired token is what's passed as the WS `?token=` query param under
    # test.
    ws_client = await _ws_client(session_maker, valid_token)
    try:
        async with ws_client:
            got_disconnect = None
            try:
                async with aconnect_ws(
                    f"/ws/job-runs/{run_id}?token={expired_token}", ws_client
                ) as ws:
                    await ws.receive_json()
                raise AssertionError("expected the connection to be closed by the server")
            except* WebSocketDisconnect as eg:
                got_disconnect = eg.exceptions[0]
            assert got_disconnect is not None and got_disconnect.code == 4401
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
