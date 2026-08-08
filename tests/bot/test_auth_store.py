"""Tests for bot/auth_store.py against a tmp-file-backed sqlite db."""
from __future__ import annotations

import bot.auth_store as auth_store


async def test_init_db_is_idempotent(bot_state_db):
    await auth_store.init_db()
    await auth_store.init_db()
    assert await auth_store.get_last_seen_alert_id() == 0


async def test_get_linked_user_missing_returns_none(bot_state_db):
    assert await auth_store.get_linked_user(12345) is None


async def test_upsert_and_get_linked_user_round_trips(bot_state_db):
    await auth_store.upsert_linked_user(
        telegram_user_id=1,
        chat_id=100,
        username="alice",
        role="ADMIN",
        bearer_token_encrypted="cipher-1",
        linked_at="2026-01-01T00:00:00+00:00",
    )
    linked = await auth_store.get_linked_user(1)
    assert linked is not None
    assert linked.telegram_user_id == 1
    assert linked.chat_id == 100
    assert linked.username == "alice"
    assert linked.role == "ADMIN"
    assert linked.bearer_token_encrypted == "cipher-1"
    assert linked.linked_at == "2026-01-01T00:00:00+00:00"


async def test_upsert_linked_user_replaces_existing_row(bot_state_db):
    await auth_store.upsert_linked_user(
        telegram_user_id=1, chat_id=100, username="alice", role="OPERATOR",
        bearer_token_encrypted="cipher-old",
    )
    await auth_store.upsert_linked_user(
        telegram_user_id=1, chat_id=200, username="alice2", role="ADMIN",
        bearer_token_encrypted="cipher-new",
    )
    linked = await auth_store.get_linked_user(1)
    assert linked.chat_id == 200
    assert linked.username == "alice2"
    assert linked.role == "ADMIN"
    assert linked.bearer_token_encrypted == "cipher-new"

    all_users = await auth_store.list_linked_users()
    assert len(all_users) == 1


async def test_upsert_linked_user_defaults_linked_at_when_omitted(bot_state_db):
    await auth_store.upsert_linked_user(
        telegram_user_id=1, chat_id=100, username="alice", role="OPERATOR",
        bearer_token_encrypted="cipher",
    )
    linked = await auth_store.get_linked_user(1)
    assert linked.linked_at  # non-empty ISO string was auto-generated


async def test_delete_linked_user(bot_state_db):
    await auth_store.upsert_linked_user(
        telegram_user_id=1, chat_id=100, username="alice", role="OPERATOR",
        bearer_token_encrypted="cipher",
    )
    await auth_store.delete_linked_user(1)
    assert await auth_store.get_linked_user(1) is None


async def test_delete_linked_user_missing_is_noop(bot_state_db):
    await auth_store.delete_linked_user(99999)  # must not raise


async def test_list_linked_users_ordered_by_telegram_user_id(bot_state_db):
    for uid in (3, 1, 2):
        await auth_store.upsert_linked_user(
            telegram_user_id=uid, chat_id=uid * 10, username=f"user{uid}", role="OPERATOR",
            bearer_token_encrypted=f"cipher-{uid}",
        )
    users = await auth_store.list_linked_users()
    assert [u.telegram_user_id for u in users] == [1, 2, 3]


async def test_last_seen_alert_id_defaults_to_zero(bot_state_db):
    assert await auth_store.get_last_seen_alert_id() == 0


async def test_set_and_get_last_seen_alert_id(bot_state_db):
    await auth_store.set_last_seen_alert_id(42)
    assert await auth_store.get_last_seen_alert_id() == 42

    # Repeated set (upsert-on-conflict) overwrites, not duplicates.
    await auth_store.set_last_seen_alert_id(99)
    assert await auth_store.get_last_seen_alert_id() == 99


async def test_state_survives_reopening_the_same_db_path(tmp_path, monkeypatch):
    import bot.config as bot_config

    db_path = str(tmp_path / "persisted_state.db")
    monkeypatch.setattr(bot_config.settings, "BOT_STATE_DB_PATH", db_path)
    await auth_store.init_db()
    await auth_store.upsert_linked_user(
        telegram_user_id=7, chat_id=70, username="persisted", role="ADMIN",
        bearer_token_encrypted="cipher-7",
    )
    await auth_store.set_last_seen_alert_id(11)

    # Simulate a process restart: nothing but the on-disk file survives; a
    # fresh call sequence against the same path must see the old data.
    reread = await auth_store.get_linked_user(7)
    assert reread is not None
    assert reread.username == "persisted"
    assert await auth_store.get_last_seen_alert_id() == 11
