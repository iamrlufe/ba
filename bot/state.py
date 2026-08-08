"""In-memory pending-restore-confirmation store, plus thin load/save
wrappers around the alert-poll high-water-mark (which is actually
persisted in `bot.auth_store`'s `notification_state` table -- see that
module -- so that it survives bot restarts; an in-memory-only high-water-
mark would re-notify every ACTIVE alert on every restart).

Pending restores are intentionally NOT persisted: they're short-lived
(`BOT_RESTORE_CONFIRMATION_TTL_SECONDS`, default 5 minutes) per-chat UX
state, not durable data -- losing them on a bot restart just means an
in-flight confirmation has to be re-issued via /restore.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot import auth_store


@dataclass
class PendingRestore:
    database_name: str
    backup_record_id: int
    sql_instance_id: int
    job_name: str
    telegram_user_id: int  # who ran /restore -- only this user may confirm or /cancel it
    created_at: float  # time.monotonic() at creation, for TTL checks
    attempts: int = 0


# Keyed by Telegram chat_id. A plain module-level dict is sufficient here --
# the bot process is single-instance/single-event-loop by design.
_pending_restores: dict[int, PendingRestore] = {}


def get_pending_restore(chat_id: int) -> PendingRestore | None:
    return _pending_restores.get(chat_id)


def set_pending_restore(chat_id: int, pending: PendingRestore) -> None:
    _pending_restores[chat_id] = pending


def clear_pending_restore(chat_id: int) -> None:
    _pending_restores.pop(chat_id, None)


async def load_last_seen_alert_id() -> int:
    return await auth_store.get_last_seen_alert_id()


async def save_last_seen_alert_id(value: int) -> None:
    await auth_store.set_last_seen_alert_id(value)
