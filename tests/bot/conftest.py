"""Shared fixtures for the `bot/` test suite.

CRITICAL ORDERING NOTE: `bot/config.py` does `settings = get_bot_settings()`
at *module import time*, and `BotSettings` has three required fields with no
defaults (`TELEGRAM_BOT_TOKEN`, `FERNET_KEY`, `BOT_ALLOWED_CHAT_IDS`). There
is no committed `bot/.env`. That means the very first `import bot...`
anywhere in this test process will raise a pydantic `ValidationError`
unless all three are already present in `os.environ` *before* that import
happens.

pytest loads a directory's `conftest.py` before collecting sibling test
modules in that same directory, so setting these env vars here -- as plain
module-level `os.environ[...] = ...` (NOT inside a fixture, which would run
far too late) -- guarantees every `tests/bot/test_*.py` module can safely
`import bot...` at its own module level.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST-TELEGRAM-BOT-TOKEN")
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("utf-8"))
# 555 matches this file's `make_update` fixture's default `chat_id` and the
# `linked_user` fixture's `chat_id`, so existing tests that don't care about
# the allowlist keep passing unmodified.
os.environ.setdefault("BOT_ALLOWED_CHAT_IDS", "555,-100999")

import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport  # noqa: E402

import bot.auth_store as auth_store  # noqa: E402
import bot.config as bot_config  # noqa: E402
import bot.state as bot_state  # noqa: E402
from app.core.db import get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from bot.api_client import OrchestratorApiClient  # noqa: E402


# --------------------------------------------------------------------------
# bot/auth_store.py-backed local state db, isolated per-test via tmp_path.
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def bot_state_db(tmp_path, monkeypatch):
    """Points `bot.config.settings.BOT_STATE_DB_PATH` at a fresh tmp sqlite
    file and initializes its schema. `bot/auth_store.py::_db_path()` reads
    `settings.BOT_STATE_DB_PATH` fresh on every call, so this monkeypatch
    (unlike TELEGRAM_BOT_TOKEN/FERNET_KEY above) is safe to do per-test."""
    db_path = str(tmp_path / "bot_state.db")
    monkeypatch.setattr(bot_config.settings, "BOT_STATE_DB_PATH", db_path)
    await auth_store.init_db()
    return db_path


@pytest.fixture(autouse=True)
def _clear_pending_restores():
    """`bot.state._pending_restores` is a bare module-level dict (by
    design -- see bot/state.py) -- clear it before and after every test so
    state never leaks across tests."""
    bot_state._pending_restores.clear()
    yield
    bot_state._pending_restores.clear()


# --------------------------------------------------------------------------
# Real-backend API client factory, wired to the same in-memory test engine
# as tests/conftest.py's own `client` fixture, via httpx.ASGITransport --
# for tests that want genuine end-to-end coverage of bot/api_client.py's
# request/error-mapping logic against the real FastAPI app.
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def asgi_transport(session_maker):
    async def _override_get_db():
        async with session_maker() as s:
            yield s

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=fastapi_app)
    try:
        yield transport
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def api_client_factory(asgi_transport):
    """`bot.poller.ApiClientFactory`-shaped callable: `(bearer_token) ->
    OrchestratorApiClient`, backed by the real FastAPI app in-process."""

    def _factory(bearer_token: str | None) -> OrchestratorApiClient:
        return OrchestratorApiClient(
            base_url="http://test",
            bearer_token=bearer_token,
            timeout=5.0,
            transport=asgi_transport,
        )

    return _factory


# --------------------------------------------------------------------------
# Lightweight fake python-telegram-bot Update/Context builders -- handlers
# under test only ever read a handful of attributes off these objects, so a
# real `telegram.Update`/live Bot connection is unnecessary.
# --------------------------------------------------------------------------


@pytest.fixture
def make_update():
    def _make(
        *,
        user_id: int = 111,
        username: str = "tguser",
        chat_id: int = 555,
        chat_type: str = "private",
        text: str = "",
        user=None,
    ):
        effective_user = user if user is not None else SimpleNamespace(id=user_id, username=username)
        effective_chat = SimpleNamespace(id=chat_id, type=chat_type)
        effective_message = SimpleNamespace(text=text, reply_text=AsyncMock())
        return SimpleNamespace(
            effective_user=effective_user,
            effective_chat=effective_chat,
            effective_message=effective_message,
        )

    return _make


@pytest.fixture
def make_context():
    def _make(*, args=None, bot_data=None, bot=None):
        return SimpleNamespace(
            args=list(args) if args is not None else [],
            bot_data=bot_data if bot_data is not None else {},
            bot=bot if bot is not None else AsyncMock(),
        )

    return _make


# --------------------------------------------------------------------------
# A telegram_user_id/chat_id already linked in auth_store, for tests of
# handlers gated by `bot.handlers._common.require_linked`.
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def linked_user(bot_state_db):
    from bot.crypto import encrypt_secret

    telegram_user_id = 111
    chat_id = 555
    await auth_store.upsert_linked_user(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        username="tguser",
        role="OPERATOR",
        bearer_token_encrypted=encrypt_secret("plaintext-bearer-token"),
    )
    return SimpleNamespace(telegram_user_id=telegram_user_id, chat_id=chat_id, username="tguser")


@pytest.fixture
def monotonic_now():
    return time.monotonic()
