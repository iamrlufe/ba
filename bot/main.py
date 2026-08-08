"""Entrypoint: builds the PTB `Application`, registers handlers, starts the
alert-poller as an `asyncio.Task` (via `post_init`/`post_shutdown` hooks,
which run inside the event loop `Application.run_polling()` manages
internally), and runs polling.

Run as `python -m bot.main` (see bot/Dockerfile).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot import auth_store
from bot.api_client import OrchestratorApiClient
from bot.config import settings
from bot.handlers import alerts as alerts_handlers
from bot.handlers import link as link_handlers
from bot.handlers import restore as restore_handlers
from bot.handlers import status as status_handlers
from bot.logging_config import configure_logging
from bot.poller import ApiClientFactory, alert_poll_loop

logger = logging.getLogger(__name__)


def build_api_client_factory(transport: httpx.AsyncBaseTransport | None = None) -> ApiClientFactory:
    """Factory closure building a fresh `OrchestratorApiClient` per call --
    stashed on `application.bot_data` so handlers (and tests) can
    substitute it rather than each handler constructing a client inline.
    `transport=None` means real network; tests pass an
    `httpx.ASGITransport` here (see bot/api_client.py)."""

    def factory(bearer_token: str | None) -> OrchestratorApiClient:
        return OrchestratorApiClient(
            base_url=settings.BOT_API_BASE_URL,
            bearer_token=bearer_token,
            timeout=settings.BOT_HTTP_TIMEOUT_SECONDS,
            transport=transport,
        )

    return factory


def _allowed_chat_filter() -> filters.BaseFilter:
    """BOT_ALLOWED_CHAT_IDS unset (the default) -> every chat is allowed.
    If set, every command/message handler below is additionally gated on
    this comma-separated allowlist of Telegram chat ids."""
    if not settings.BOT_ALLOWED_CHAT_IDS:
        return filters.ALL
    chat_ids = [int(x.strip()) for x in settings.BOT_ALLOWED_CHAT_IDS.split(",") if x.strip()]
    return filters.Chat(chat_id=chat_ids)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wired via `Application.add_error_handler` so a bug in any single
    handler can never crash the whole polling loop. Logs the full
    traceback at ERROR. `context.error` is a Python exception object, not
    raw update text -- this never risks logging a token or password."""
    logger.error("unhandled exception while processing an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text("Something went wrong.")


def build_application(*, transport: httpx.AsyncBaseTransport | None = None) -> Application:
    application = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

    factory = build_api_client_factory(transport)
    application.bot_data["api_client_factory"] = factory
    application.bot_data["auth_store"] = auth_store

    chat_filter = _allowed_chat_filter()

    application.add_handler(CommandHandler("start", link_handlers.start, filters=chat_filter))
    application.add_handler(CommandHandler("help", link_handlers.help_command, filters=chat_filter))
    application.add_handler(CommandHandler("link", link_handlers.link, filters=chat_filter))
    application.add_handler(CommandHandler("status", status_handlers.status, filters=chat_filter))
    application.add_handler(CommandHandler("alerts", alerts_handlers.alerts, filters=chat_filter))
    application.add_handler(CommandHandler("ack", alerts_handlers.ack, filters=chat_filter))
    application.add_handler(CommandHandler("resolve", alerts_handlers.resolve, filters=chat_filter))
    application.add_handler(CommandHandler("restore", restore_handlers.restore, filters=chat_filter))
    application.add_handler(CommandHandler("cancel", restore_handlers.cancel, filters=chat_filter))
    # Plain-text confirmation replies -- registered after every
    # CommandHandler, in the same (default) handler group, so command
    # handlers always take priority. Only acts when a pending restore
    # exists for the chat (see bot/handlers/restore.py::confirm_restore).
    application.add_handler(
        MessageHandler(chat_filter & filters.TEXT & ~filters.COMMAND, restore_handlers.confirm_restore)
    )

    application.add_error_handler(_on_error)

    async def _post_init(app: Application) -> None:
        await auth_store.init_db()
        app.bot_data["alert_poll_task"] = asyncio.create_task(
            alert_poll_loop(app.bot, api_client_factory=factory)
        )

    async def _post_shutdown(app: Application) -> None:
        task = app.bot_data.get("alert_poll_task")
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    application.post_init = _post_init
    application.post_shutdown = _post_shutdown

    return application


def main() -> None:
    configure_logging()
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
