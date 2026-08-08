"""Persisted (local sqlite) telegram_user_id -> encrypted bearer-token cache.

Survives bot restarts (bot/main.py's `_post_init` hook restarts the alert
poller and needs the poll high-water-mark from here; every command handler
needs the linked-user row for its chat). Deliberately plain
`sqlite3`/`aiosqlite`, no SQLAlchemy/ORM -- this is a small local cache
file, not a shared relational database, and does not need Alembic
migrations. This file needs the same operational care (backups, filesystem
permissions) as the API's own database file -- losing it forces every user
to `/link` again and resets the alert-poll high-water-mark.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from bot.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS linked_users (
    telegram_user_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    bearer_token_encrypted TEXT NOT NULL,
    linked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seen_alert_id INTEGER NOT NULL DEFAULT 0
);
"""

_COLUMNS = "telegram_user_id, chat_id, username, role, bearer_token_encrypted, linked_at"


@dataclass(frozen=True)
class LinkedUser:
    telegram_user_id: int
    chat_id: int
    username: str
    role: str
    bearer_token_encrypted: str
    linked_at: str


def _db_path() -> str:
    return settings.BOT_STATE_DB_PATH


async def init_db() -> None:
    """Create the local state db + tables if they don't already exist.
    Called once at bot startup (bot/main.py). Safe to call repeatedly."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(_SCHEMA)
        await db.execute(
            "INSERT OR IGNORE INTO notification_state (id, last_seen_alert_id) VALUES (1, 0)"
        )
        await db.commit()


async def get_linked_user(telegram_user_id: int) -> LinkedUser | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT {_COLUMNS} FROM linked_users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return LinkedUser(**dict(row)) if row is not None else None


async def upsert_linked_user(
    *,
    telegram_user_id: int,
    chat_id: int,
    username: str,
    role: str,
    bearer_token_encrypted: str,
    linked_at: str | None = None,
) -> None:
    linked_at = linked_at or datetime.now(UTC).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"INSERT OR REPLACE INTO linked_users ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            (telegram_user_id, chat_id, username, role, bearer_token_encrypted, linked_at),
        )
        await db.commit()


async def delete_linked_user(telegram_user_id: int) -> None:
    """Proactive invalidation: called whenever a given telegram_user_id's
    cached token draws a 401 from the API, so the next command immediately
    prompts re-/link instead of failing the same way repeatedly."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM linked_users WHERE telegram_user_id = ?", (telegram_user_id,))
        await db.commit()


async def list_linked_users() -> list[LinkedUser]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(f"SELECT {_COLUMNS} FROM linked_users ORDER BY telegram_user_id")
        rows = await cursor.fetchall()
        return [LinkedUser(**dict(row)) for row in rows]


async def get_last_seen_alert_id() -> int:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("SELECT last_seen_alert_id FROM notification_state WHERE id = 1")
        row = await cursor.fetchone()
        return row[0] if row is not None else 0


async def set_last_seen_alert_id(value: int) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO notification_state (id, last_seen_alert_id) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_seen_alert_id = excluded.last_seen_alert_id",
            (value,),
        )
        await db.commit()
