"""Tests for bot/poller.py's `_run_poll_tick` (and a light smoke test of
the outer `alert_poll_loop`)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import bot.auth_store as auth_store
import bot.poller as poller
from app.models.enums import AlertChannel, AlertSeverity, AlertStatus, AlertType
from app.schemas.alert import AlertRead
from app.schemas.common import PaginatedResponse
from bot.api_client import ApiUnauthorized
from bot.crypto import encrypt_secret

_NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)


def _make_alert(id_, *, channel=AlertChannel.BOTH, status=AlertStatus.ACTIVE) -> AlertRead:
    return AlertRead.model_validate(dict(
        id=id_, alert_type=AlertType.JOB_FAILED, severity=AlertSeverity.WARNING,
        entity_type="backup_job", server_id=None, disk_id=None, backup_job_id=1,
        job_run_id=None, restore_operation_id=None, title=f"alert {id_}", message="m",
        status=status, channel=channel, delivered_telegram_at=None,
        acknowledged_by=None, acknowledged_at=None, resolved_at=None, resolved_note=None,
        created_at=_NOW, updated_at=_NOW,
    ))


class _FakeApiClient:
    def __init__(self, *, alerts=None, raise_unauthorized=False):
        self._alerts = list(alerts) if alerts is not None else []
        self._raise_unauthorized = raise_unauthorized
        self.mark_delivered_calls: list[int] = []
        self.list_alerts_calls: list[tuple] = []
        self.closed = False

    async def list_alerts(self, *, status=None, limit=50, offset=0):
        self.list_alerts_calls.append((status, limit, offset))
        if self._raise_unauthorized:
            raise ApiUnauthorized("token invalid")
        total = len(self._alerts)
        items = self._alerts[offset:offset + limit]
        return PaginatedResponse[AlertRead](items=items, total=total, limit=limit, offset=offset)

    async def mark_alert_telegram_delivered(self, alert_id):
        self.mark_delivered_calls.append(alert_id)

    async def aclose(self):
        self.closed = True


async def _link(telegram_user_id, chat_id, token):
    await auth_store.upsert_linked_user(
        telegram_user_id=telegram_user_id, chat_id=chat_id, username=f"user{telegram_user_id}",
        role="OPERATOR", bearer_token_encrypted=encrypt_secret(token),
    )


async def test_run_poll_tick_no_linked_users_is_noop(bot_state_db):
    fake_bot = AsyncMock()
    factory_calls = []

    def factory(token):
        factory_calls.append(token)
        raise AssertionError("factory should never be called with no linked users")

    await poller._run_poll_tick(fake_bot, factory)

    fake_bot.send_message.assert_not_called()
    assert factory_calls == []


async def test_run_poll_tick_advances_high_water_mark_and_fans_out_telegram_alerts(bot_state_db):
    await _link(1, 100, "token-a")
    await _link(2, 200, "token-b")

    alert_telegram = _make_alert(1, channel=AlertChannel.TELEGRAM)
    alert_web_only = _make_alert(2, channel=AlertChannel.WEB)
    client_a = _FakeApiClient(alerts=[alert_telegram, alert_web_only])

    def factory(token):
        assert token == "token-a"  # first linked user's token succeeds -> never tries token-b
        return client_a

    fake_bot = AsyncMock()
    await poller._run_poll_tick(fake_bot, factory)

    # Only the TELEGRAM-channel alert is pushed, fanned out to every linked user.
    assert fake_bot.send_message.await_count == 2
    sent_chat_ids = {call.kwargs["chat_id"] for call in fake_bot.send_message.await_args_list}
    assert sent_chat_ids == {100, 200}
    for call in fake_bot.send_message.await_args_list:
        assert "alert #1" in call.kwargs["text"]

    assert client_a.mark_delivered_calls == [1]
    assert client_a.closed is True

    # High-water mark advances to the max alert id seen, regardless of channel.
    assert await auth_store.get_last_seen_alert_id() == 2


async def test_run_poll_tick_no_new_alerts_sends_nothing(bot_state_db):
    await auth_store.set_last_seen_alert_id(5)
    await _link(1, 100, "token-a")

    client_a = _FakeApiClient(alerts=[_make_alert(3, channel=AlertChannel.BOTH)])
    fake_bot = AsyncMock()

    await poller._run_poll_tick(fake_bot, lambda token: client_a)

    fake_bot.send_message.assert_not_called()
    assert client_a.mark_delivered_calls == []
    # `new_max_id` is computed purely from the CURRENTLY ACTIVE alert set
    # (`max(a.id for a in alerts, default=last_seen_alert_id)`), not
    # `max(that, last_seen_alert_id)` -- so it can move the persisted
    # high-water-mark *down* to 3 here (alert #3 is the only alert still
    # ACTIVE; ids 4/5 -- if they ever existed -- are no longer in the
    # ACTIVE set at all, e.g. resolved). This is safe/intentional, not a
    # bug: alert ids are strictly monotonic and once an id drops out of the
    # ACTIVE set it can never reappear, so no id can ever be re-notified
    # merely because the mark moved down -- any id <= the new (lower) mark
    # was already excluded (id > last_seen_alert_id) under the STRICTLY
    # HIGHER last_seen_alert_id that was in effect back when it was last
    # fetched, so `id > <today's regressed mark>` can never newly become
    # true for an id that was already <= that former higher mark.
    assert await auth_store.get_last_seen_alert_id() == 3


async def test_run_poll_tick_survives_simulated_restart_no_renotify(bot_state_db):
    """After a tick advances the high-water-mark and the sqlite state file
    is 'reopened' (nothing but the tmp file persists across `_run_poll_tick`
    calls -- there is no other in-process cache), a second tick against the
    same still-ACTIVE alerts must not re-notify."""
    await _link(1, 100, "token-a")

    alert1 = _make_alert(1, channel=AlertChannel.TELEGRAM)
    client_first_tick = _FakeApiClient(alerts=[alert1])
    fake_bot = AsyncMock()

    await poller._run_poll_tick(fake_bot, lambda token: client_first_tick)
    assert fake_bot.send_message.await_count == 1
    assert await auth_store.get_last_seen_alert_id() == 1

    # Second tick, fresh client instance (simulating a fresh poll cycle),
    # same still-ACTIVE alert set.
    client_second_tick = _FakeApiClient(alerts=[alert1])
    await poller._run_poll_tick(fake_bot, lambda token: client_second_tick)

    # No additional sends -- the high-water-mark already covers alert #1.
    assert fake_bot.send_message.await_count == 1
    assert client_second_tick.mark_delivered_calls == []
    assert await auth_store.get_last_seen_alert_id() == 1


async def test_run_poll_tick_401_deletes_user_and_tries_next_token(bot_state_db):
    await _link(1, 100, "token-a")
    await _link(2, 200, "token-b")

    client_a = _FakeApiClient(raise_unauthorized=True)
    alert1 = _make_alert(1, channel=AlertChannel.TELEGRAM)
    client_b = _FakeApiClient(alerts=[alert1])

    clients_by_token = {"token-a": client_a, "token-b": client_b}

    def factory(token):
        return clients_by_token[token]

    fake_bot = AsyncMock()
    await poller._run_poll_tick(fake_bot, factory)

    # user 1's token 401'd -> deleted from auth_store.
    assert await auth_store.get_linked_user(1) is None
    assert client_a.closed is True

    # user 2's token succeeded and was used to fetch + mark-delivered.
    assert await auth_store.get_linked_user(2) is not None
    assert client_b.mark_delivered_calls == [1]
    assert client_b.closed is True

    # Fan-out only targets remaining linked users (user 1 already deleted).
    assert fake_bot.send_message.await_count == 1
    assert fake_bot.send_message.await_args.kwargs["chat_id"] == 200

    assert await auth_store.get_last_seen_alert_id() == 1


async def test_run_poll_tick_all_tokens_401_advances_nothing(bot_state_db):
    await _link(1, 100, "token-a")

    client_a = _FakeApiClient(raise_unauthorized=True)
    fake_bot = AsyncMock()

    await poller._run_poll_tick(fake_bot, lambda token: client_a)

    assert await auth_store.get_linked_user(1) is None
    fake_bot.send_message.assert_not_called()
    # No client ever successfully fetched -- high-water-mark must not move
    # (still the initial default 0).
    assert await auth_store.get_last_seen_alert_id() == 0


async def test_fetch_active_alerts_paginates(monkeypatch, bot_state_db):
    monkeypatch.setattr(poller, "_PAGE_LIMIT", 2)
    alerts = [_make_alert(i) for i in range(1, 6)]  # 5 alerts, page size 2 -> 3 pages
    client = _FakeApiClient(alerts=alerts)

    result = await poller._fetch_active_alerts(client)

    assert [a.id for a in result] == [1, 2, 3, 4, 5]
    assert len(client.list_alerts_calls) == 3
    offsets = [call[2] for call in client.list_alerts_calls]
    assert offsets == [0, 2, 4]


async def test_alert_poll_loop_runs_at_least_one_tick_then_stops(bot_state_db, monkeypatch):
    monkeypatch.setattr(poller.settings, "BOT_ALERT_POLL_INTERVAL_SECONDS", 0)
    await _link(1, 100, "token-a")

    client = _FakeApiClient(alerts=[_make_alert(1, channel=AlertChannel.TELEGRAM)])
    fake_bot = AsyncMock()
    stop_event = asyncio.Event()

    async def factory_side_effect(token):
        return client

    call_count = {"n": 0}

    def factory(token):
        call_count["n"] += 1
        if call_count["n"] >= 1:
            stop_event.set()
        return client

    task = asyncio.create_task(poller.alert_poll_loop(fake_bot, stop_event=stop_event, api_client_factory=factory))
    await asyncio.wait_for(task, timeout=2.0)

    assert call_count["n"] >= 1
    assert await auth_store.get_last_seen_alert_id() == 1
