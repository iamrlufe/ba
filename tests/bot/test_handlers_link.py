"""Tests for bot/handlers/link.py -- /start, /help, /link."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import bot.auth_store as auth_store
from app.models.enums import UserRole
from app.schemas.auth import TelegramLinkResponse
from bot.api_client import ApiConflict, ApiUnauthorized, ApiValidationError
from bot.handlers import link as link_handlers


class _StubClient:
    def __init__(self, *, response=None, error=None):
        self._response = response
        self._error = error
        self.telegram_link_calls = []
        self.closed = False

    async def telegram_link(self, *, username, password, telegram_user_id):
        self.telegram_link_calls.append((username, password, telegram_user_id))
        if self._error is not None:
            raise self._error
        return self._response

    async def aclose(self):
        self.closed = True


async def test_link_refuses_in_group_chat_and_makes_no_api_call(bot_state_db, make_update, make_context):
    factory = MagicMock()
    update = make_update(chat_type="group", text="/link alice secretpassword")
    context = make_context(args=["alice", "secretpassword"], bot_data={"api_client_factory": factory})

    await link_handlers.link(update, context)

    factory.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "DM me privately" in reply


async def test_link_wrong_arg_count_shows_usage_and_makes_no_api_call(bot_state_db, make_update, make_context):
    factory = MagicMock()
    update = make_update(chat_type="private")
    context = make_context(args=["onlyone"], bot_data={"api_client_factory": factory})

    await link_handlers.link(update, context)

    factory.assert_not_called()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Usage: /link" in reply


async def test_link_happy_path_persists_linked_user_and_encrypts_token(bot_state_db, make_update, make_context):
    stub_client = _StubClient(
        response=TelegramLinkResponse(
            bot_access_token="super-secret-jwt-value",
            expires_in=3600,
            username="alice",
            role=UserRole.OPERATOR,
        )
    )
    factory = MagicMock(return_value=stub_client)
    update = make_update(user_id=222, chat_id=333, chat_type="private")
    context = make_context(args=["alice", "correct-password"], bot_data={"api_client_factory": factory})

    await link_handlers.link(update, context)

    factory.assert_called_once_with(None)
    assert stub_client.telegram_link_calls == [("alice", "correct-password", 222)]
    assert stub_client.closed is True

    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Linked as alice (OPERATOR)."

    linked = await auth_store.get_linked_user(222)
    assert linked is not None
    assert linked.chat_id == 333
    assert linked.username == "alice"
    assert linked.role == "OPERATOR"
    # The persisted token must be encrypted, never the plaintext JWT itself.
    assert linked.bearer_token_encrypted != "super-secret-jwt-value"

    from bot.crypto import decrypt_secret

    assert decrypt_secret(linked.bearer_token_encrypted) == "super-secret-jwt-value"


async def test_link_invalid_credentials_replies_generic_and_does_not_link(bot_state_db, make_update, make_context):
    stub_client = _StubClient(error=ApiUnauthorized("Invalid username or password"))
    factory = MagicMock(return_value=stub_client)
    update = make_update(user_id=222, chat_type="private")
    context = make_context(args=["alice", "wrongpassword"], bot_data={"api_client_factory": factory})

    await link_handlers.link(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Invalid username or password."
    assert await auth_store.get_linked_user(222) is None


async def test_link_conflict_replies_generic_and_does_not_link(bot_state_db, make_update, make_context):
    stub_client = _StubClient(error=ApiConflict("telegram_user_id already linked"))
    factory = MagicMock(return_value=stub_client)
    update = make_update(user_id=222, chat_type="private")
    context = make_context(args=["alice", "somepassword"], bot_data={"api_client_factory": factory})

    await link_handlers.link(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "already linked to a different user" in reply
    assert await auth_store.get_linked_user(222) is None


async def test_link_validation_error_does_not_leak_detail_into_reply(bot_state_db, make_update, make_context):
    """Regression test: FastAPI's default 422 body echoes the raw rejected
    input verbatim (e.g. an over-length password). Forwarding
    `error.detail` to the user would leak the plaintext password into the
    chat transcript/logs -- link.py must reply with a generic message
    instead."""
    leaked_secret = "the-actual-plaintext-password-was-Tr0ub4dor&3-and-too-long"
    stub_client = _StubClient(
        error=ApiValidationError(f"password: {leaked_secret} is too long (max 255 chars)")
    )
    factory = MagicMock(return_value=stub_client)
    update = make_update(user_id=222, chat_type="private")
    context = make_context(args=["alice", leaked_secret], bot_data={"api_client_factory": factory})

    await link_handlers.link(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert leaked_secret not in reply
    assert reply == "That username/password could not be accepted. Please check them and try again."
    assert await auth_store.get_linked_user(222) is None


async def test_start_nudges_unlinked_user_to_link(bot_state_db, make_update, make_context):
    update = make_update(user_id=999)
    context = make_context()

    await link_handlers.start(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not linked yet" in reply
    assert "/link <username> <password>" in reply


async def test_start_does_not_nudge_linked_user(bot_state_db, make_update, make_context, linked_user):
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context()

    await link_handlers.start(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not linked yet" not in reply


async def test_help_command_shows_help_text(bot_state_db, make_update, make_context):
    update = make_update(user_id=999)
    context = make_context()

    await link_handlers.help_command(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "/status" in reply
    assert "/restore" in reply
