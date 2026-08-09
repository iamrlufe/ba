"""Transport-level chat allowlist gate.

Registered in `bot/main.py::build_application` as a `TypeHandler(Update,
allowlist_gate)` in an EARLIER handler group (-1) than every command/message
handler (all in the default group 0). python-telegram-bot processes handler
groups in ascending numeric order and, per update, stops looking at any
group after the current group's matching handler raises
`ApplicationHandlerStop` -- so a disallowed chat's update never reaches
group 0 at all, meaning /link's own logic (including its DM-only check)
never runs for a disallowed chat. This replaces the old per-handler
`filters=chat_filter` AND'd onto each CommandHandler/MessageHandler
individually (fragile: a handler added later without remembering to AND
the filter would silently bypass the allowlist) with a single mandatory
choke point every update must pass through.

Deliberately silent to the chat itself on denial (no reply at all) --
a random/scanning chat should get zero confirmation this bot exists or
works. The denial is logged instead, at INFO, in a single grep-able line,
for audit / allowlist-expansion decisions.

Chat id sign carries no special handling here: a private chat's id is
positive, a group/supergroup's is negative, but `chat.id in
settings.allowed_chat_ids` is the exact same integer-set membership test
either way -- do not add chat.type branching to the allow/deny decision.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.config import settings

logger = logging.getLogger(__name__)

# The only update kinds this bot's handlers ever act on (commands and plain
# text messages/replies) -- explicit tuple rather than introspecting
# Update's internal slots/fields, so this doesn't depend on python-telegram-bot
# internals or drift across library versions. Extend this tuple if a future
# handler starts reacting to a new update kind (e.g. callback_query).
_KNOWN_UPDATE_KINDS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
)


def _update_kind(update: Update) -> str:
    """Best-effort label for which payload field is populated on this
    Update, for the audit log line only -- never used for any allow/deny
    decision. Falls back to "unknown" rather than raising if none of the
    known kinds match (e.g. a brand-new update type PTB added)."""
    for kind in _KNOWN_UPDATE_KINDS:
        if getattr(update, kind, None) is not None:
            return kind
    return "unknown"


async def allowlist_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is not None and chat.id in settings.allowed_chat_ids:
        return

    logger.info(
        "chat_denied_by_allowlist chat_id=%s chat_type=%s update_kind=%s",
        chat.id if chat is not None else None,
        chat.type if chat is not None else None,
        _update_kind(update),
    )
    raise ApplicationHandlerStop
