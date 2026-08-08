"""Tests for bot/handlers/alerts.py -- /alerts, /ack, /resolve."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.models.enums import AlertChannel, AlertSeverity, AlertStatus, AlertType
from app.schemas.alert import AlertRead
from app.schemas.common import PaginatedResponse
from bot.api_client import ApiForbidden, ApiNotFound
from bot.handlers import alerts as alerts_handlers

_NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)


def _make_alert(**overrides) -> AlertRead:
    defaults = dict(
        id=1, alert_type=AlertType.JOB_FAILED, severity=AlertSeverity.WARNING,
        entity_type="backup_job", server_id=None, disk_id=None, backup_job_id=1,
        job_run_id=None, restore_operation_id=None, title="t", message="m",
        status=AlertStatus.ACTIVE, channel=AlertChannel.BOTH, delivered_telegram_at=None,
        acknowledged_by=None, acknowledged_at=None, resolved_at=None, resolved_note=None,
        created_at=_NOW, updated_at=_NOW,
    )
    defaults.update(overrides)
    return AlertRead.model_validate(defaults)


class _StubClient:
    def __init__(self, *, list_result=None, ack_result=None, resolve_result=None, error=None):
        self._list_result = list_result
        self._ack_result = ack_result
        self._resolve_result = resolve_result
        self._error = error
        self.list_calls = []
        self.ack_calls = []
        self.resolve_calls = []
        self.closed = False

    async def list_alerts(self, *, status=None, limit=50, offset=0):
        self.list_calls.append(status)
        if self._error is not None:
            raise self._error
        return self._list_result

    async def acknowledge_alert(self, alert_id):
        self.ack_calls.append(alert_id)
        if self._error is not None:
            raise self._error
        return self._ack_result

    async def resolve_alert(self, alert_id, note=None):
        self.resolve_calls.append((alert_id, note))
        if self._error is not None:
            raise self._error
        return self._resolve_result

    async def aclose(self):
        self.closed = True


async def test_alerts_requires_linked_user(bot_state_db, make_update, make_context):
    update = make_update(user_id=999)  # not linked
    context = make_context(args=[], bot_data={"api_client_factory": MagicMock()})

    await alerts_handlers.alerts(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not linked yet" in reply


async def test_alerts_default_filter_is_active(bot_state_db, linked_user, make_update, make_context):
    page = PaginatedResponse[AlertRead](items=[_make_alert(id=1)], total=1, limit=50, offset=0)
    stub = _StubClient(list_result=page)
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=[], bot_data={"api_client_factory": factory})

    await alerts_handlers.alerts(update, context)

    assert stub.list_calls == [AlertStatus.ACTIVE]
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "#1" in reply


async def test_alerts_explicit_status_filter(bot_state_db, linked_user, make_update, make_context):
    page = PaginatedResponse[AlertRead](items=[], total=0, limit=50, offset=0)
    stub = _StubClient(list_result=page)
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["resolved"], bot_data={"api_client_factory": factory})

    await alerts_handlers.alerts(update, context)

    assert stub.list_calls == [AlertStatus.RESOLVED]
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No RESOLVED alerts." == reply


async def test_alerts_invalid_status_filter_shows_usage_no_api_call(bot_state_db, linked_user, make_update, make_context):
    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["NOT_A_STATUS"], bot_data={"api_client_factory": factory})

    await alerts_handlers.alerts(update, context)

    factory.assert_not_called()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Usage: /alerts" in reply


async def test_alerts_too_many_args_shows_usage(bot_state_db, linked_user, make_update, make_context):
    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["ACTIVE", "extra"], bot_data={"api_client_factory": factory})

    await alerts_handlers.alerts(update, context)

    factory.assert_not_called()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Usage: /alerts" in reply


async def test_ack_usage_when_missing_arg(bot_state_db, linked_user, make_update, make_context):
    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=[], bot_data={"api_client_factory": factory})

    await alerts_handlers.ack(update, context)

    factory.assert_not_called()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Usage: /ack" in reply


async def test_ack_usage_when_non_integer_arg(bot_state_db, linked_user, make_update, make_context):
    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["not-an-int"], bot_data={"api_client_factory": factory})

    await alerts_handlers.ack(update, context)

    factory.assert_not_called()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Usage: /ack" in reply


async def test_ack_relays_403_verbatim(bot_state_db, linked_user, make_update, make_context):
    stub = _StubClient(error=ApiForbidden("Requires role: ADMIN"))
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["5"], bot_data={"api_client_factory": factory})

    await alerts_handlers.ack(update, context)

    assert stub.ack_calls == [5]
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "You don't have permission to do that (Requires role: ADMIN)." == reply


async def test_ack_happy_path(bot_state_db, linked_user, make_update, make_context):
    result_alert = _make_alert(id=9, status=AlertStatus.ACKNOWLEDGED)
    stub = _StubClient(ack_result=result_alert)
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["9"], bot_data={"api_client_factory": factory})

    await alerts_handlers.ack(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Alert #9 acknowledged (status ACKNOWLEDGED)."


async def test_resolve_usage_when_missing_arg(bot_state_db, linked_user, make_update, make_context):
    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=[], bot_data={"api_client_factory": factory})

    await alerts_handlers.resolve(update, context)

    factory.assert_not_called()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Usage: /resolve" in reply


async def test_resolve_relays_403_verbatim(bot_state_db, linked_user, make_update, make_context):
    stub = _StubClient(error=ApiForbidden("Requires role: ADMIN"))
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["5", "some", "note"], bot_data={"api_client_factory": factory})

    await alerts_handlers.resolve(update, context)

    assert stub.resolve_calls == [(5, "some note")]
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "You don't have permission to do that (Requires role: ADMIN)." == reply


async def test_resolve_happy_path_with_note(bot_state_db, linked_user, make_update, make_context):
    result_alert = _make_alert(id=3, status=AlertStatus.RESOLVED)
    stub = _StubClient(resolve_result=result_alert)
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["3", "fixed", "it"], bot_data={"api_client_factory": factory})

    await alerts_handlers.resolve(update, context)

    assert stub.resolve_calls == [(3, "fixed it")]
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Alert #3 resolved (status RESOLVED)."


async def test_alerts_relays_404_via_generic_handler(bot_state_db, linked_user, make_update, make_context):
    stub = _StubClient(error=ApiNotFound("nope"))
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=[], bot_data={"api_client_factory": factory})

    await alerts_handlers.alerts(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Not found: nope" == reply
