"""Shared handler-support helpers.

- `require_linked`: the mandatory "must be linked" first gate for every
  command except /start, /help, /link itself.
- `get_api_client`: builds a fresh `OrchestratorApiClient` for a given
  linked user, decrypting the cached bearer token only here, at the point
  of use (never earlier, never logged).
- `handle_api_errors` / `handle_api_error`: the shared `ApiClientError` ->
  user-facing-reply mapping used by every handler. Handlers may still
  catch specific `ApiClientError` subtypes themselves *before* this layer
  runs, for a friendlier message (e.g. bot/handlers/restore.py's
  confirmation step).

`api_client`/`auth_store` are threaded through `context.bot_data` (set up
in bot/main.py::build_application) rather than constructed/imported ad hoc
in each handler, so both are substitutable in tests (e.g. swapping in an
`httpx.ASGITransport`-backed factory, or monkeypatching `bot.auth_store`'s
underlying db path).

Absolute rule: never log BotSettings.TELEGRAM_BOT_TOKEN, a decrypted
bearer token, or a user's password -- not even at DEBUG.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable

from cryptography.fernet import InvalidToken
from telegram import Update
from telegram.ext import ContextTypes

from bot import auth_store
from bot.api_client import (
    ApiClientError,
    ApiConflict,
    ApiForbidden,
    ApiNotFound,
    ApiServerError,
    ApiUnauthorized,
    ApiUnavailable,
    ApiValidationError,
    OrchestratorApiClient,
)
from bot.auth_store import LinkedUser
from bot.crypto import decrypt_secret

logger = logging.getLogger(__name__)

PLEASE_LINK_MESSAGE = (
    "You're not linked yet. DM me /link <username> <password> to connect your account first."
)


async def require_linked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> LinkedUser | None:
    """Mandatory first gate for every command except /start, /help, /link.
    Replies with a "please /link" message and returns None if this chat's
    Telegram user has no row in auth_store -- callers must return early
    when this returns None."""
    telegram_user_id = update.effective_user.id
    linked = await auth_store.get_linked_user(telegram_user_id)
    if linked is None:
        await update.effective_message.reply_text(PLEASE_LINK_MESSAGE)
        return None
    return linked


def get_api_client(context: ContextTypes.DEFAULT_TYPE, linked_user: LinkedUser) -> OrchestratorApiClient:
    """Build a fresh, single-use `OrchestratorApiClient` authenticated as
    `linked_user` -- a new instance per call, never a shared client with a
    mutated bearer token (see bot/api_client.py). Decrypts the cached
    token only here, at the point of use; the caller is responsible for
    `await client.aclose()` when done (or use it as an async context
    manager)."""
    factory = context.bot_data["api_client_factory"]
    bearer_token = decrypt_secret(linked_user.bearer_token_encrypted)
    return factory(bearer_token)


async def handle_api_error(
    error: ApiClientError,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    telegram_user_id: int | None = None,
) -> None:
    """Map an ApiClientError to a user-facing reply."""
    message = update.effective_message
    if isinstance(error, ApiUnauthorized):
        # Proactive invalidation: delete the stale row so the next command
        # immediately prompts re-/link instead of failing the same way
        # repeatedly.
        if telegram_user_id is not None:
            await auth_store.delete_linked_user(telegram_user_id)
        await message.reply_text("Your session expired or is no longer valid. Please run /link again.")
    elif isinstance(error, ApiForbidden):
        await message.reply_text(f"You don't have permission to do that ({error.detail}).")
    elif isinstance(error, ApiNotFound):
        await message.reply_text(f"Not found: {error.detail}")
    elif isinstance(error, ApiConflict):
        await message.reply_text(f"Conflict: {error.detail}")
    elif isinstance(error, ApiValidationError):
        # Signals bot/API drift (the bot sent something the API's Pydantic
        # validation rejected) -- worth a WARNING, not just an INFO.
        logger.warning("API validation error (possible bot/API drift): %s", error.detail)
        await message.reply_text(f"Invalid request: {error.detail}")
    elif isinstance(error, ApiServerError):
        logger.error("API server error: %s", error)
        await message.reply_text("The backend is having trouble right now -- please try again shortly.")
    elif isinstance(error, ApiUnavailable):
        logger.warning("API unavailable: %s", error)
        await message.reply_text("Could not reach the backend -- please try again shortly.")
    else:
        logger.error("Unmapped ApiClientError: %r", error)
        await message.reply_text("Something went wrong.")


def handle_api_errors(
    handler: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """Decorator wrapping a handler body: catches any `ApiClientError` the
    handler doesn't catch itself and maps it to a user-facing reply via
    `handle_api_error`.

    Any OTHER (non-`ApiClientError`) exception is deliberately NOT caught
    here -- that generic catch-all is wired via
    `Application.add_error_handler` in bot/main.py, so a bug in one
    handler can never crash the whole polling loop, and the full
    traceback is logged at ERROR exactly once.
    """

    @functools.wraps(handler)
    async def _wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: object, **kwargs: object) -> None:
        telegram_user_id = update.effective_user.id if update.effective_user else None
        try:
            await handler(update, context, *args, **kwargs)
        except InvalidToken:
            # The cached bearer token in auth_store failed to decrypt --
            # most likely FERNET_KEY was rotated without migrating stored
            # tokens. Treat exactly like an expired/invalid session: drop
            # the now-unusable row and prompt re-/link, rather than
            # leaving a permanently-broken linked_users row that silently
            # fails the same way on every future command.
            if telegram_user_id is not None:
                await auth_store.delete_linked_user(telegram_user_id)
            logger.warning(
                "stored bearer token failed to decrypt for telegram_user_id=%s (key rotated?)",
                telegram_user_id,
            )
            await update.effective_message.reply_text(
                "Your linked session could not be read and was cleared. Please run /link again."
            )
        except ApiClientError as exc:
            await handle_api_error(exc, update, context, telegram_user_id=telegram_user_id)

    return _wrapped
