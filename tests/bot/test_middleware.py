"""Tests for bot/middleware.py -- the mandatory chat-id allowlist gate.

`tests/bot/conftest.py` sets `BOT_ALLOWED_CHAT_IDS=555,-100999` in the
environment before any `bot.*` import happens, so `bot.middleware.settings`
(imported from `bot.config`) has `.allowed_chat_ids ==
frozenset({555, -100999})` for the whole test process. `555` also matches
`make_update`'s default `chat_id`, so we reuse that fixture directly for the
"allowed" cases rather than monkeypatching settings.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from bot.middleware import _update_kind, allowlist_gate

ALLOWED_PRIVATE_CHAT_ID = 555
ALLOWED_GROUP_CHAT_ID = -100999
DISALLOWED_CHAT_ID = 999999


async def test_allowed_private_chat_passes_through_silently(make_update, make_context, caplog):
    caplog.set_level(logging.INFO)
    update = make_update(chat_id=ALLOWED_PRIVATE_CHAT_ID, chat_type="private")
    context = make_context()

    result = await allowlist_gate(update, context)

    assert result is None
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)
    assert not any("chat_denied_by_allowlist" in record.message for record in caplog.records)


async def test_allowed_group_chat_passes_through_silently(make_update, make_context, caplog):
    caplog.set_level(logging.INFO)
    update = make_update(chat_id=ALLOWED_GROUP_CHAT_ID, chat_type="group")
    context = make_context()

    result = await allowlist_gate(update, context)

    assert result is None
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)
    assert not any("chat_denied_by_allowlist" in record.message for record in caplog.records)


async def test_disallowed_chat_raises_and_logs_denial_without_replying(make_update, make_context, caplog):
    caplog.set_level(logging.INFO)
    update = make_update(chat_id=DISALLOWED_CHAT_ID, chat_type="private")
    context = make_context()

    with pytest.raises(ApplicationHandlerStop):
        await allowlist_gate(update, context)

    denial_records = [r for r in caplog.records if "chat_denied_by_allowlist" in r.message]
    assert len(denial_records) == 1
    message = denial_records[0].message
    assert str(DISALLOWED_CHAT_ID) in message
    assert "private" in message

    update.effective_message.reply_text.assert_not_called()
    update.effective_message.reply_text.assert_not_awaited()


async def test_disallowed_group_chat_raises_and_logs_chat_type(make_update, make_context, caplog):
    caplog.set_level(logging.INFO)
    update = make_update(chat_id=-1, chat_type="group")
    context = make_context()

    with pytest.raises(ApplicationHandlerStop):
        await allowlist_gate(update, context)

    denial_records = [r for r in caplog.records if "chat_denied_by_allowlist" in r.message]
    assert len(denial_records) == 1
    assert "group" in denial_records[0].message


async def test_none_effective_chat_raises_gate_stop_not_attribute_error(make_context, caplog):
    caplog.set_level(logging.INFO)
    update = SimpleNamespace(effective_chat=None, effective_message=None)
    context = make_context()

    with pytest.raises(ApplicationHandlerStop):
        await allowlist_gate(update, context)

    denial_records = [r for r in caplog.records if "chat_denied_by_allowlist" in r.message]
    assert len(denial_records) == 1
    assert "chat_id=None" in denial_records[0].message
    assert "chat_type=None" in denial_records[0].message


def test_update_kind_returns_message_when_message_field_set():
    update = SimpleNamespace(message=SimpleNamespace())

    assert _update_kind(update) == "message"


def test_update_kind_returns_unknown_when_no_known_field_set():
    update = SimpleNamespace()

    assert _update_kind(update) == "unknown"
