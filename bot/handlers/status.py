"""`/status` -- on-demand daily alert/job-status summary."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot import formatting
from bot.handlers._common import get_api_client, handle_api_errors, require_linked


@handle_api_errors
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    linked = await require_linked(update, context)
    if linked is None:
        return

    client = get_api_client(context, linked)
    try:
        summary = await client.get_daily_summary()
    finally:
        await client.aclose()

    await update.effective_message.reply_text(formatting.format_daily_summary(summary))
