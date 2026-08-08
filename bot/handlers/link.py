"""`/start`, `/help`, `/link` -- the only commands that don't require
`bot.handlers._common.require_linked` (since /link IS how you become
linked in the first place).
"""
from __future__ import annotations

from datetime import UTC, datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot import auth_store
from bot.api_client import ApiConflict, ApiUnauthorized, ApiValidationError
from bot.crypto import encrypt_secret
from bot.handlers._common import handle_api_errors

HELP_TEXT = (
    "Backup Orchestrator bot commands:\n"
    "/link <username> <password> - link your account (DM only)\n"
    "/status - daily alert/job-status summary\n"
    "/alerts [ACTIVE|ACKNOWLEDGED|RESOLVED] - list alerts (default ACTIVE)\n"
    "/ack <alert_id> - acknowledge an alert (admin only)\n"
    "/resolve <alert_id> [note...] - resolve an alert (admin only)\n"
    "/restore <database_name> <backup_record_id> - start a restore\n"
    "/cancel - cancel a pending restore confirmation\n"
    "/help - show this message"
)

_NOT_LINKED_NUDGE = "\n\nYou are not linked yet -- use /link <username> <password> to get started."


async def _reply_help(update: Update) -> None:
    text = HELP_TEXT
    linked = await auth_store.get_linked_user(update.effective_user.id)
    if linked is None:
        text += _NOT_LINKED_NUDGE
    await update.effective_message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply_help(update)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply_help(update)


@handle_api_errors
async def link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Only honored in private chats -- in any group, refuse immediately and
    # do NOT parse/log the rest of the message (which would contain a
    # plaintext password).
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "For your security, please DM me privately to /link your account."
        )
        return

    args = context.args or []
    if len(args) != 2:
        await update.effective_message.reply_text("Usage: /link <username> <password>")
        return

    # NEVER log update.message.text or these parsed values, at any log level.
    username, password = args[0], args[1]
    telegram_user_id = update.effective_user.id

    factory = context.bot_data["api_client_factory"]
    client = factory(None)
    try:
        try:
            resp = await client.telegram_link(
                username=username, password=password, telegram_user_id=telegram_user_id
            )
        except ApiUnauthorized:
            await update.effective_message.reply_text("Invalid username or password.")
            return
        except ApiConflict:
            await update.effective_message.reply_text(
                "This Telegram account is already linked to a different user. Ask an admin for help."
            )
            return
        except ApiValidationError:
            # Deliberately generic and NOT forwarding error.detail: FastAPI's
            # default validation-error body echoes the raw rejected input
            # (e.g. a too-long password) verbatim. Letting that reach
            # _common.handle_api_error's generic ApiValidationError branch
            # would log the plaintext password and post it back into the
            # Telegram chat -- exactly what this module's "never log a
            # password" rule forbids. Never log `password` here either.
            await update.effective_message.reply_text(
                "That username/password could not be accepted. Please check them and try again."
            )
            return
    finally:
        await client.aclose()

    await auth_store.upsert_linked_user(
        telegram_user_id=telegram_user_id,
        chat_id=update.effective_chat.id,
        username=resp.username,
        role=resp.role.value,
        bearer_token_encrypted=encrypt_secret(resp.bot_access_token),
        linked_at=datetime.now(UTC).isoformat(),
    )
    await update.effective_message.reply_text(f"Linked as {resp.username} ({resp.role.value}).")
