"""Tests for bot/handlers/restore.py -- /restore, /cancel, confirm_restore.

Covers the full state machine, including the two security-fix properties
called out in the spec: the ownership check (only the /restore-initiating
Telegram user may confirm/cancel) and the stale sql_instance_id
re-validation at confirmation time.
"""
from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock

from cryptography.fernet import InvalidToken

from app.models.enums import BackupType, RestoreMode, RestoreStatus
from app.schemas.backup_job import BackupJobRead
from app.schemas.backup_record import BackupRecordRead
from app.schemas.restore_operation import RestoreOperationRead
from bot import state
from bot.api_client import ApiConflict, ApiForbidden, ApiNotFound
from bot.config import settings
from bot.handlers import restore as restore_handlers


def _make_backup_record(**overrides) -> BackupRecordRead:
    defaults = dict(
        id=1, backup_job_id=10, job_run_id=None, file_name="f.bak", remote_path="/r/f.bak",
        file_size_bytes=1024, checksum=None, checksum_algorithm=None,
        detected_at=datetime.now(), updated_at=datetime.now(),
    )
    defaults.update(overrides)
    return BackupRecordRead.model_validate(defaults)


def _make_backup_job(**overrides) -> BackupJobRead:
    defaults = dict(
        id=10, server_id=1, disk_id=1, sql_instance_id=7, name="nightly-full",
        database_name=None, source_path="/data/x", backup_type=BackupType.FULL,
        schedule_cron="0 * * * *", timezone="UTC", retention_days=30, retention_min_copies=1,
        verification_method="CHECKSUM", expected_max_duration_minutes=None,
        missed_run_grace_minutes=60, is_enabled=True, last_run_at=None, next_run_at=None,
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    defaults.update(overrides)
    return BackupJobRead.model_validate(defaults)


def _make_restore_op(**overrides) -> RestoreOperationRead:
    defaults = dict(
        id=99, backup_record_id=1, sql_instance_id=7, server_id=1, database_name="mydb",
        mode=RestoreMode.MISSING, requested_by="alice", requested_by_channel="TELEGRAM",
        status=RestoreStatus.PENDING, requested_at=datetime.now(), started_at=None,
        completed_at=None, error_message=None, created_at=datetime.now(), updated_at=datetime.now(),
    )
    defaults.update(overrides)
    return RestoreOperationRead.model_validate(defaults)


class _StubClient:
    """Configurable stub -- `get_backup_job` supports returning different
    values on successive calls (list) to simulate the sql_instance_id
    changing between /restore time and confirmation time."""

    def __init__(
        self, *, record=None, jobs=None, record_error=None, job_error=None,
        create_result=None, create_error=None,
    ):
        self._record = record
        self._jobs = list(jobs) if jobs is not None else []
        self._record_error = record_error
        self._job_error = job_error
        self._create_result = create_result
        self._create_error = create_error
        self.get_record_calls = 0
        self.get_job_calls = 0
        self.create_calls = []
        self.closed = False

    async def get_backup_record(self, backup_record_id):
        self.get_record_calls += 1
        if self._record_error is not None:
            raise self._record_error
        return self._record

    async def get_backup_job(self, backup_job_id):
        self.get_job_calls += 1
        if self._job_error is not None:
            raise self._job_error
        idx = min(self.get_job_calls - 1, len(self._jobs) - 1)
        return self._jobs[idx]

    async def create_restore_operation(self, **kwargs):
        self.create_calls.append(kwargs)
        if self._create_error is not None:
            raise self._create_error
        return self._create_result

    async def aclose(self):
        self.closed = True


def _pending_for(chat_id):
    return state.get_pending_restore(chat_id)


# --------------------------------------------------------------------------
# /restore
# --------------------------------------------------------------------------


async def test_restore_requires_linked_user(bot_state_db, make_update, make_context):
    update = make_update(user_id=999)
    context = make_context(args=["mydb", "1"], bot_data={"api_client_factory": MagicMock()})

    await restore_handlers.restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not linked yet" in reply


async def test_restore_usage_on_wrong_arg_count(bot_state_db, linked_user, make_update, make_context):
    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["onlyone"], bot_data={"api_client_factory": factory})

    await restore_handlers.restore(update, context)
    factory.assert_not_called()
    assert "Usage: /restore" in update.effective_message.reply_text.await_args.args[0]


async def test_restore_usage_on_non_integer_backup_record_id(bot_state_db, linked_user, make_update, make_context):
    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id)
    context = make_context(args=["mydb", "notanint"], bot_data={"api_client_factory": factory})

    await restore_handlers.restore(update, context)
    factory.assert_not_called()
    assert "Usage: /restore" in update.effective_message.reply_text.await_args.args[0]


async def test_restore_one_pending_per_chat_refusal(bot_state_db, linked_user, make_update, make_context):
    existing = state.PendingRestore(
        database_name="existingdb", backup_record_id=1, sql_instance_id=7, job_name="j",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, existing)

    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id)
    context = make_context(args=["newdb", "2"], bot_data={"api_client_factory": factory})

    await restore_handlers.restore(update, context)

    factory.assert_not_called()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "existingdb" in reply
    assert state.get_pending_restore(linked_user.chat_id) is existing


async def test_restore_404_backup_record_no_pending_created(bot_state_db, linked_user, make_update, make_context):
    stub = _StubClient(record_error=ApiNotFound("no such record"))
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id)
    context = make_context(args=["mydb", "42"], bot_data={"api_client_factory": factory})

    await restore_handlers.restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No backup record with id 42 found." == reply
    assert state.get_pending_restore(linked_user.chat_id) is None


async def test_restore_refuses_when_job_has_no_sql_instance(bot_state_db, linked_user, make_update, make_context):
    record = _make_backup_record(id=1, backup_job_id=10)
    job = _make_backup_job(id=10, sql_instance_id=None, name="no-sql-job")
    stub = _StubClient(record=record, jobs=[job])
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id)
    context = make_context(args=["mydb", "1"], bot_data={"api_client_factory": factory})

    await restore_handlers.restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "no-sql-job" in reply
    assert "cannot be restored via this bot" in reply
    assert state.get_pending_restore(linked_user.chat_id) is None


async def test_restore_happy_path_creates_pending_state(bot_state_db, linked_user, make_update, make_context):
    record = _make_backup_record(id=5, backup_job_id=10)
    job = _make_backup_job(id=10, sql_instance_id=7, name="nightly-full")
    stub = _StubClient(record=record, jobs=[job])
    factory = MagicMock(return_value=stub)
    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id)
    context = make_context(args=["mydb", "5"], bot_data={"api_client_factory": factory})

    await restore_handlers.restore(update, context)

    pending = state.get_pending_restore(linked_user.chat_id)
    assert pending is not None
    assert pending.database_name == "mydb"
    assert pending.backup_record_id == 5
    assert pending.sql_instance_id == 7
    assert pending.job_name == "nightly-full"
    assert pending.telegram_user_id == linked_user.telegram_user_id
    assert pending.attempts == 0

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "About to restore database 'mydb'" in reply
    assert "reply with EXACTLY: mydb" in reply
    assert stub.closed is True


# --------------------------------------------------------------------------
# confirm_restore -- happy path
# --------------------------------------------------------------------------


async def test_confirm_restore_no_pending_is_silent_noop(bot_state_db, linked_user, make_update, make_context):
    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": MagicMock()})

    await restore_handlers.confirm_restore(update, context)

    update.effective_message.reply_text.assert_not_called()


async def test_confirm_restore_happy_path_creates_restore_operation(bot_state_db, linked_user, make_update, make_context):
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    record = _make_backup_record(id=5, backup_job_id=10)
    job = _make_backup_job(id=10, sql_instance_id=7)
    result_op = _make_restore_op(id=555, database_name="mydb")
    stub = _StubClient(record=record, jobs=[job], create_result=result_op)
    factory = MagicMock(return_value=stub)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": factory})

    await restore_handlers.confirm_restore(update, context)

    assert stub.create_calls == [
        {
            "backup_record_id": 5,
            "sql_instance_id": 7,
            "database_name": "mydb",
            "confirmation_database_name": "mydb",
        }
    ]
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Restore operation #555 created (status PENDING)."
    assert state.get_pending_restore(linked_user.chat_id) is None


async def test_confirm_restore_conflict_from_create_is_reported(bot_state_db, linked_user, make_update, make_context):
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    record = _make_backup_record(id=5, backup_job_id=10)
    job = _make_backup_job(id=10, sql_instance_id=7)
    stub = _StubClient(record=record, jobs=[job], create_error=ApiConflict("already running"))
    factory = MagicMock(return_value=stub)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": factory})

    await restore_handlers.confirm_restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Conflict: already running"
    # Pending is cleared unconditionally once an exact-match confirm is submitted.
    assert state.get_pending_restore(linked_user.chat_id) is None


async def test_confirm_restore_forbidden_from_create_is_reported(bot_state_db, linked_user, make_update, make_context):
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    record = _make_backup_record(id=5, backup_job_id=10)
    job = _make_backup_job(id=10, sql_instance_id=7)
    stub = _StubClient(record=record, jobs=[job], create_error=ApiForbidden("nope"))
    factory = MagicMock(return_value=stub)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": factory})

    await restore_handlers.confirm_restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "nope"


# --------------------------------------------------------------------------
# Security fix #1: ownership check
# --------------------------------------------------------------------------


async def test_confirm_restore_from_different_user_is_silent_noop_and_pending_untouched(
    bot_state_db, linked_user, make_update, make_context
):
    owner_id = linked_user.telegram_user_id  # user A
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=owner_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    stub = _StubClient()  # get_backup_record/get_backup_job/create must never be called
    factory = MagicMock(return_value=stub)

    intruder_id = owner_id + 1  # user B, different telegram user, same chat
    update = make_update(user_id=intruder_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": factory})

    await restore_handlers.confirm_restore(update, context)

    # Silent no-op: no reply at all, no API calls, pending state untouched
    # (not cleared, not mutated).
    update.effective_message.reply_text.assert_not_called()
    factory.assert_not_called()
    assert stub.create_calls == []
    assert state.get_pending_restore(linked_user.chat_id) is pending
    assert pending.attempts == 0


async def test_cancel_from_different_user_is_refused_and_pending_untouched(
    bot_state_db, linked_user, make_update, make_context
):
    owner_id = linked_user.telegram_user_id
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=owner_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    intruder_id = owner_id + 1
    update = make_update(user_id=intruder_id, chat_id=linked_user.chat_id)
    context = make_context()

    await restore_handlers.cancel(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Only the user who started this restore can cancel it."
    assert state.get_pending_restore(linked_user.chat_id) is pending


async def test_cancel_by_owner_clears_pending(bot_state_db, linked_user, make_update, make_context):
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id)
    context = make_context()

    await restore_handlers.cancel(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Cancelled pending restore confirmation for database 'mydb'." == reply
    assert state.get_pending_restore(linked_user.chat_id) is None


async def test_cancel_with_no_pending_replies_nothing_to_cancel(bot_state_db, linked_user, make_update, make_context):
    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id)
    context = make_context()

    await restore_handlers.cancel(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Nothing to cancel."


# --------------------------------------------------------------------------
# Security fix #2: stale sql_instance_id re-validation at confirm time
# --------------------------------------------------------------------------


async def test_confirm_restore_refuses_when_sql_instance_changed_since_restore(
    bot_state_db, linked_user, make_update, make_context
):
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    record = _make_backup_record(id=5, backup_job_id=10)
    # get_backup_job returns sql_instance_id=99 now, vs 7 captured at /restore time.
    job_now = _make_backup_job(id=10, sql_instance_id=99)
    stub = _StubClient(record=record, jobs=[job_now], create_result=_make_restore_op())
    factory = MagicMock(return_value=stub)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": factory})

    await restore_handlers.confirm_restore(update, context)

    assert stub.create_calls == []  # must never submit with a stale sql_instance_id
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "SQL instance configuration changed" in reply
    assert "run /restore again" in reply.lower() or "re-run /restore" in reply.lower() or "run /restore" in reply
    assert state.get_pending_restore(linked_user.chat_id) is None


async def test_confirm_restore_backup_record_gone_by_confirmation_time(
    bot_state_db, linked_user, make_update, make_context
):
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    stub = _StubClient(record_error=ApiNotFound("gone"))
    factory = MagicMock(return_value=stub)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": factory})

    await restore_handlers.confirm_restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "no longer exists" in reply
    assert stub.create_calls == []


# --------------------------------------------------------------------------
# TTL expiry / mismatch / max-attempts
# --------------------------------------------------------------------------


async def test_confirm_restore_ttl_expired_clears_pending_and_does_not_count_as_attempt(
    bot_state_db, linked_user, make_update, make_context
):
    stale_created_at = time.monotonic() - (settings.BOT_RESTORE_CONFIRMATION_TTL_SECONDS + 5)
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=stale_created_at,
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    factory = MagicMock()
    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": factory})

    await restore_handlers.confirm_restore(update, context)

    factory.assert_not_called()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply == "Your restore confirmation expired. Please run /restore again."
    assert state.get_pending_restore(linked_user.chat_id) is None


async def test_confirm_restore_mismatch_increments_attempts_and_reprompts(
    bot_state_db, linked_user, make_update, make_context
):
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="wrongdb")
    context = make_context(bot_data={"api_client_factory": MagicMock()})

    await restore_handlers.confirm_restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "doesn't match 'mydb'" in reply
    still_pending = state.get_pending_restore(linked_user.chat_id)
    assert still_pending is not None
    assert still_pending.attempts == 1


async def test_confirm_restore_max_attempts_exhausted_cancels(
    bot_state_db, linked_user, make_update, make_context
):
    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
        attempts=settings.BOT_RESTORE_CONFIRMATION_MAX_ATTEMPTS - 1,
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="wrongdb")
    context = make_context(bot_data={"api_client_factory": MagicMock()})

    await restore_handlers.confirm_restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Too many mismatched confirmation attempts" in reply
    assert state.get_pending_restore(linked_user.chat_id) is None


# --------------------------------------------------------------------------
# InvalidToken-on-decrypt path (shared _common.py machinery)
# --------------------------------------------------------------------------


async def test_confirm_restore_stale_fernet_key_deletes_linked_user_and_prompts_relink(
    bot_state_db, linked_user, make_update, make_context, monkeypatch
):
    import bot.handlers._common as common_module

    def _boom(token):
        raise InvalidToken()

    monkeypatch.setattr(common_module, "decrypt_secret", _boom)

    pending = state.PendingRestore(
        database_name="mydb", backup_record_id=5, sql_instance_id=7, job_name="nightly-full",
        telegram_user_id=linked_user.telegram_user_id, created_at=time.monotonic(),
    )
    state.set_pending_restore(linked_user.chat_id, pending)

    update = make_update(user_id=linked_user.telegram_user_id, chat_id=linked_user.chat_id, text="mydb")
    context = make_context(bot_data={"api_client_factory": MagicMock()})

    await restore_handlers.confirm_restore(update, context)

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "could not be read and was cleared" in reply
    assert "/link" in reply

    import bot.auth_store as auth_store

    assert await auth_store.get_linked_user(linked_user.telegram_user_id) is None
