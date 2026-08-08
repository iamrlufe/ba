"""`/alerts`, `/ack`, `/resolve`.

Neither /ack nor /resolve does a local role check -- the API's own
`require_role(ADMIN)` on those endpoints is the actual authority; a 403
from that check flows through `bot.handlers._common.handle_api_errors`
into a generic "you don't have permission" reply.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.models.enums import AlertStatus
from bot import formatting
from bot.handlers._common import get_api_client, handle_api_errors, require_linked

_STATUS_USAGE = "Usage: /alerts [ACTIVE|ACKNOWLEDGED|RESOLVED]"
_STATUS_BY_NAME = {s.name: s for s in AlertStatus}


def _parse_alert_id(token: str) -> int | None:
    try:
        return int(token)
    except ValueError:
        return None


@handle_api_errors
async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    linked = await require_linked(update, context)
    if linked is None:
        return

    args = context.args or []
    if len(args) > 1:
        await update.effective_message.reply_text(_STATUS_USAGE)
        return

    status_filter = AlertStatus.ACTIVE
    if args:
        status_name = args[0].upper()
        if status_name not in _STATUS_BY_NAME:
            await update.effective_message.reply_text(_STATUS_USAGE)
            return
        status_filter = _STATUS_BY_NAME[status_name]

    client = get_api_client(context, linked)
    try:
        page = await client.list_alerts(status=status_filter)
    finally:
        await client.aclose()

    if not page.items:
        await update.effective_message.reply_text(f"No {status_filter.value} alerts.")
        return

    lines = [formatting.format_alert_line(a) for a in page.items]
    await update.effective_message.reply_text("\n".join(lines))


@handle_api_errors
async def ack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    linked = await require_linked(update, context)
    if linked is None:
        return

    args = context.args or []
    alert_id = _parse_alert_id(args[0]) if len(args) == 1 else None
    if alert_id is None:
        await update.effective_message.reply_text("Usage: /ack <alert_id>")
        return

    client = get_api_client(context, linked)
    try:
        alert = await client.acknowledge_alert(alert_id)
    finally:
        await client.aclose()

    await update.effective_message.reply_text(f"Alert #{alert.id} acknowledged (status {alert.status.value}).")


@handle_api_errors
async def resolve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    linked = await require_linked(update, context)
    if linked is None:
        return

    args = context.args or []
    alert_id = _parse_alert_id(args[0]) if args else None
    if alert_id is None:
        await update.effective_message.reply_text("Usage: /resolve <alert_id> [note...]")
        return
    note = " ".join(args[1:]) if len(args) > 1 else None

    client = get_api_client(context, linked)
    try:
        alert = await client.resolve_alert(alert_id, note)
    finally:
        await client.aclose()

    await update.effective_message.reply_text(f"Alert #{alert.id} resolved (status {alert.status.value}).")
