"""`/restore`, `/cancel`, and the plain-text confirmation reply handler.

SECURITY NOTE: the server's own `RestoreOperationCreate` validator
(`confirmation_database_name == database_name`, see
app/schemas/restore_operation.py) is the actual security control that
prevents a mistyped/mismatched restore from ever being created. The
exact-match check in `confirm_restore` below is a UX/typo-guard only --
it exists to give the user a fast, friendly "doesn't match, try again"
reply instead of a generic 422 from the API -- NOT a substitute for
server-side validation. Even if this bot-side check were skipped
entirely, the server would still reject a mismatched confirmation.
"""
from __future__ import annotations

import time

from telegram import Update
from telegram.ext import ContextTypes

from bot import state
from bot.api_client import ApiConflict, ApiForbidden, ApiNotFound
from bot.config import settings
from bot.handlers._common import get_api_client, handle_api_errors, require_linked
from bot.state import PendingRestore

_RESTORE_USAGE = "Usage: /restore <database_name> <backup_record_id>"


@handle_api_errors
async def restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    linked = await require_linked(update, context)
    if linked is None:
        return

    chat_id = update.effective_chat.id
    args = context.args or []
    if len(args) != 2:
        await update.effective_message.reply_text(_RESTORE_USAGE)
        return
    database_name = args[0]
    try:
        backup_record_id = int(args[1])
    except ValueError:
        await update.effective_message.reply_text(_RESTORE_USAGE)
        return

    existing = state.get_pending_restore(chat_id)
    if existing is not None:
        await update.effective_message.reply_text(
            f"You already have a pending restore confirmation for database "
            f"'{existing.database_name}' (backup record #{existing.backup_record_id}). "
            f"Reply with that exact database name to confirm, or /cancel first."
        )
        return

    client = get_api_client(context, linked)
    try:
        try:
            record = await client.get_backup_record(backup_record_id)
        except ApiNotFound:
            await update.effective_message.reply_text(
                f"No backup record with id {backup_record_id} found."
            )
            return

        job = await client.get_backup_job(record.backup_job_id)
    finally:
        await client.aclose()

    if job.sql_instance_id is None:
        await update.effective_message.reply_text(
            f"Backup job '{job.name}' (#{job.id}) has no SQL Server instance configured, "
            f"so it cannot be restored via this bot."
        )
        return

    pending = PendingRestore(
        database_name=database_name,
        backup_record_id=backup_record_id,
        sql_instance_id=job.sql_instance_id,
        job_name=job.name,
        telegram_user_id=update.effective_user.id,
        created_at=time.monotonic(),
        attempts=0,
    )
    state.set_pending_restore(chat_id, pending)

    await update.effective_message.reply_text(
        f"About to restore database '{database_name}' from backup record #{backup_record_id} "
        f"(backup job '{job.name}', SQL instance #{job.sql_instance_id}), mode=MISSING "
        f"(only objects missing from the target database will be restored; existing objects "
        f"are left untouched).\n\n"
        f"To confirm, reply with EXACTLY: {database_name}\n"
        f"This confirmation expires in {settings.BOT_RESTORE_CONFIRMATION_TTL_SECONDS} seconds. "
        f"Send /cancel to abort."
    )


@handle_api_errors
async def confirm_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain-text `MessageHandler` -- only acts when a pending restore
    exists for this chat; registered (bot/main.py) so command handlers
    always take priority over this catch-all."""
    chat_id = update.effective_chat.id
    pending = state.get_pending_restore(chat_id)
    if pending is None:
        return

    # 0. Ownership check -- only the Telegram user who ran /restore may
    # confirm or otherwise consume this pending state. Without this, any
    # other member of the same chat (including one who has never /link'd
    # at all) could reply with the exact database name the bot just
    # printed and either cancel the real requester's confirmation (if
    # unlinked, since require_linked below would then reject them) or,
    # worse, complete the restore under their OWN linked identity instead
    # of the requester's. Silently ignored (not even a reply) so unrelated
    # chat traffic doesn't consume the real requester's TTL/attempt budget.
    if update.effective_user is None or update.effective_user.id != pending.telegram_user_id:
        return

    message_text = update.effective_message.text or ""

    # 1. TTL check first -- an expired reply is never treated as a
    # confirmation attempt (doesn't count against attempts).
    if time.monotonic() - pending.created_at > settings.BOT_RESTORE_CONFIRMATION_TTL_SECONDS:
        state.clear_pending_restore(chat_id)
        await update.effective_message.reply_text(
            "Your restore confirmation expired. Please run /restore again."
        )
        return

    # 2. Exact match (case-sensitive) -- create the restore operation.
    if message_text == pending.database_name:
        # Clear pending state immediately, regardless of the API call's
        # outcome below.
        state.clear_pending_restore(chat_id)

        linked = await require_linked(update, context)
        if linked is None:
            return

        client = get_api_client(context, linked)
        try:
            try:
                # Re-resolve the backup job's sql_instance_id right before
                # submitting: the value captured in `pending` at /restore
                # time can go stale if the job's SQL instance assignment
                # changes during the (up to BOT_RESTORE_CONFIRMATION_TTL_SECONDS)
                # confirmation window. Submitting a frozen, possibly-wrong
                # sql_instance_id would restore into the wrong SQL Server
                # instance without the operator ever re-seeing that value.
                record = await client.get_backup_record(pending.backup_record_id)
                job = await client.get_backup_job(record.backup_job_id)
            except ApiNotFound:
                await update.effective_message.reply_text(
                    "That backup record no longer exists. Please retry /restore."
                )
                return
            if job.sql_instance_id != pending.sql_instance_id:
                await update.effective_message.reply_text(
                    "This backup job's SQL instance configuration changed while this "
                    "confirmation was pending. Please run /restore again to pick up the "
                    "current configuration."
                )
                return
            try:
                op = await client.create_restore_operation(
                    backup_record_id=pending.backup_record_id,
                    sql_instance_id=pending.sql_instance_id,
                    database_name=pending.database_name,
                    confirmation_database_name=message_text,
                )
            except ApiConflict as exc:
                await update.effective_message.reply_text(f"Conflict: {exc.detail}")
                return
            except ApiNotFound:
                await update.effective_message.reply_text(
                    "That backup record or SQL instance no longer exists. Please retry /restore."
                )
                return
            except ApiForbidden as exc:
                await update.effective_message.reply_text(str(exc.detail))
                return
        finally:
            await client.aclose()

        await update.effective_message.reply_text(
            f"Restore operation #{op.id} created (status {op.status.value})."
        )
        return

    # 3. Mismatch -- increment attempts, enforce the max-attempts cutoff.
    pending.attempts += 1
    if pending.attempts >= settings.BOT_RESTORE_CONFIRMATION_MAX_ATTEMPTS:
        state.clear_pending_restore(chat_id)
        await update.effective_message.reply_text(
            "Too many mismatched confirmation attempts -- restore cancelled. Run /restore to try again."
        )
        return

    await update.effective_message.reply_text(
        f"That doesn't match '{pending.database_name}'. Reply again with the exact database name "
        f"to confirm, or /cancel."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    pending = state.get_pending_restore(chat_id)
    if pending is None:
        await update.effective_message.reply_text("Nothing to cancel.")
        return
    # Only the user who started this restore may cancel it -- otherwise
    # any other chat member could silently cancel someone else's pending
    # confirmation.
    if update.effective_user is None or update.effective_user.id != pending.telegram_user_id:
        await update.effective_message.reply_text(
            "Only the user who started this restore can cancel it."
        )
        return
    state.clear_pending_restore(chat_id)
    await update.effective_message.reply_text(
        f"Cancelled pending restore confirmation for database '{pending.database_name}'."
    )
