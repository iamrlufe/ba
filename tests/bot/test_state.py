"""Tests for bot/state.py -- the in-memory pending-restore store plus the
thin load/save wrappers around auth_store's persisted high-water-mark."""
from __future__ import annotations

import bot.auth_store as auth_store
import bot.state as state
from bot.state import PendingRestore


def _make_pending(**overrides) -> PendingRestore:
    defaults = dict(
        database_name="mydb",
        backup_record_id=1,
        sql_instance_id=2,
        job_name="nightly-full",
        telegram_user_id=111,
        created_at=0.0,
        attempts=0,
    )
    defaults.update(overrides)
    return PendingRestore(**defaults)


def test_get_pending_restore_missing_returns_none():
    assert state.get_pending_restore(555) is None


def test_set_and_get_pending_restore_round_trips():
    pending = _make_pending()
    state.set_pending_restore(555, pending)
    assert state.get_pending_restore(555) is pending


def test_set_pending_restore_is_keyed_per_chat():
    p1 = _make_pending(database_name="db1")
    p2 = _make_pending(database_name="db2")
    state.set_pending_restore(1, p1)
    state.set_pending_restore(2, p2)
    assert state.get_pending_restore(1).database_name == "db1"
    assert state.get_pending_restore(2).database_name == "db2"


def test_clear_pending_restore():
    state.set_pending_restore(555, _make_pending())
    state.clear_pending_restore(555)
    assert state.get_pending_restore(555) is None


def test_clear_pending_restore_missing_is_noop():
    state.clear_pending_restore(99999)  # must not raise


async def test_load_last_seen_alert_id_delegates_to_auth_store(bot_state_db):
    await auth_store.set_last_seen_alert_id(17)
    assert await state.load_last_seen_alert_id() == 17


async def test_save_last_seen_alert_id_delegates_to_auth_store(bot_state_db):
    await state.save_last_seen_alert_id(23)
    assert await auth_store.get_last_seen_alert_id() == 23
