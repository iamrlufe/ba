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
    TypeHandler,
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
from bot.middleware import allowlist_gate
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

    # Mandatory transport-level gate: runs in an earlier handler group (-1)
    # than every handler below (all in the default group 0), so a
    # disallowed chat's update is stopped here and never reaches any of
    # them -- see bot/middleware.py's module docstring.
    application.add_handler(TypeHandler(Update, allowlist_gate), group=-1)

    application.add_handler(CommandHandler("start", link_handlers.start))
    application.add_handler(CommandHandler("help", link_handlers.help_command))
    application.add_handler(CommandHandler("link", link_handlers.link))
    application.add_handler(CommandHandler("status", status_handlers.status))
    application.add_handler(CommandHandler("alerts", alerts_handlers.alerts))
    application.add_handler(CommandHandler("ack", alerts_handlers.ack))
    application.add_handler(CommandHandler("resolve", alerts_handlers.resolve))
    application.add_handler(CommandHandler("restore", restore_handlers.restore))
    application.add_handler(CommandHandler("cancel", restore_handlers.cancel))
    # Plain-text confirmation replies -- registered after every
    # CommandHandler, in the same (default) handler group, so command
    # handlers always take priority. Only acts when a pending restore
    # exists for the chat (see bot/handlers/restore.py::confirm_restore).
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, restore_handlers.confirm_restore)
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
