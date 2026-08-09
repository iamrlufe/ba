"""Tests for bot/config.py::BotSettings' BOT_ALLOWED_CHAT_IDS validation.

Each test constructs `BotSettings` directly with explicit kwargs (never via
the cached module-level `bot.config.settings` singleton, which is fixed for
the whole test process by `tests/bot/conftest.py`'s env-var setup) so tests
are self-contained regardless of the surrounding environment.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from bot.config import BotSettings


def _make_settings(**overrides):
    """Build a `BotSettings` with all other required fields filled in, only
    overriding what a given test cares about. `_env_file=None` so a
    developer's local `bot/.env` (if any) never leaks into these tests."""
    kwargs = {
        "TELEGRAM_BOT_TOKEN": "123456:TEST-TOKEN",
        "FERNET_KEY": Fernet.generate_key().decode("utf-8"),
        **overrides,
    }
    return BotSettings(_env_file=None, **kwargs)


def test_valid_chat_ids_construct_and_parse_to_frozenset():
    settings = _make_settings(BOT_ALLOWED_CHAT_IDS="123456789,-100987654321")

    assert settings.allowed_chat_ids == frozenset({123456789, -100987654321})


def test_valid_chat_ids_with_whitespace_strip_correctly():
    settings = _make_settings(BOT_ALLOWED_CHAT_IDS=" 123 , -456 ")

    assert settings.allowed_chat_ids == frozenset({123, -456})


def test_valid_chat_ids_with_duplicate_deduplicates():
    settings = _make_settings(BOT_ALLOWED_CHAT_IDS="123,123")

    assert settings.allowed_chat_ids == frozenset({123})


def test_missing_chat_ids_field_raises_validation_error(monkeypatch):
    # `tests/bot/conftest.py` sets BOT_ALLOWED_CHAT_IDS in os.environ (via
    # `setdefault`, at import time) so the rest of the bot test suite can
    # import `bot.*` without error -- BaseSettings would otherwise silently
    # pick that up here too, masking the "field omitted entirely" case this
    # test exists to cover. Remove it for just this test.
    monkeypatch.delenv("BOT_ALLOWED_CHAT_IDS", raising=False)

    with pytest.raises(ValidationError):
        BotSettings(
            _env_file=None,
            TELEGRAM_BOT_TOKEN="123456:TEST-TOKEN",
            FERNET_KEY=Fernet.generate_key().decode("utf-8"),
        )


def test_empty_string_chat_ids_raises_validation_error():
    with pytest.raises(ValidationError):
        _make_settings(BOT_ALLOWED_CHAT_IDS="")


def test_whitespace_only_chat_ids_raises_validation_error():
    with pytest.raises(ValidationError):
        _make_settings(BOT_ALLOWED_CHAT_IDS="   ")


def test_non_integer_token_raises_validation_error_mentioning_bad_token():
    with pytest.raises(ValidationError) as excinfo:
        _make_settings(BOT_ALLOWED_CHAT_IDS="abc,123")

    assert "abc" in str(excinfo.value)


def test_empty_middle_token_raises_validation_error():
    with pytest.raises(ValidationError):
        _make_settings(BOT_ALLOWED_CHAT_IDS="123,,456")


def test_trailing_comma_raises_validation_error():
    with pytest.raises(ValidationError):
        _make_settings(BOT_ALLOWED_CHAT_IDS="123,")
