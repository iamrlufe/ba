"""Background push-notification poller.

Polling (not a synchronous hook from `app.routers._alerts.raise_alert_if_absent`)
by design -- the bot is a separate process, and this mirrors the existing
`app.workers.alert_worker` precedent. Fan-out: every row in
`bot.auth_store`'s `linked_users` table gets DM'd for every new
push-eligible alert (no separate broadcast-list concept -- matches the 1:1
`telegram_user_id` data model). The high-water-mark (`last_seen_alert_id`)
is persisted via `bot.state`/`bot.auth_store` (survives bot restarts -- an
in-memory-only high-water-mark would re-notify every ACTIVE alert on every
restart).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from telegram import Bot
from telegram.error import TelegramError

from app.models.enums import AlertChannel, AlertStatus
from app.schemas.alert import AlertRead
from bot import auth_store, formatting, state
from bot.api_client import ApiClientError, ApiUnauthorized, OrchestratorApiClient
from bot.config import settings
from bot.crypto import decrypt_secret

logger = logging.getLogger(__name__)

ApiClientFactory = Callable[[str | None], OrchestratorApiClient]

_PAGE_LIMIT = 200


def _default_api_client_factory(bearer_token: str | None) -> OrchestratorApiClient:
    return OrchestratorApiClient(
        base_url=settings.BOT_API_BASE_URL,
        bearer_token=bearer_token,
        timeout=settings.BOT_HTTP_TIMEOUT_SECONDS,
    )


async def _fetch_active_alerts(client: OrchestratorApiClient) -> list[AlertRead]:
    alerts: list[AlertRead] = []
    offset = 0
    while True:
        page = await client.list_alerts(status=AlertStatus.ACTIVE, limit=_PAGE_LIMIT, offset=offset)
        alerts.extend(page.items)
        offset += len(page.items)
        if not page.items or offset >= page.total:
            break
    return alerts


async def _run_poll_tick(bot: Bot, factory: ApiClientFactory) -> None:
    last_seen_alert_id = await state.load_last_seen_alert_id()

    linked_users = await auth_store.list_linked_users()
    if not linked_users:
        logger.debug("alert poller: no linked users, skipping tick")
        return

    # Step 2: pick an authenticating token -- iterate linked users in
    # order, trying each against GET /api/alerts (step 3's actual fetch,
    # reused here rather than issuing a separate throwaway probe request)
    # until one succeeds. On 401, delete that row and try the next.
    client: OrchestratorApiClient | None = None
    alerts: list[AlertRead] = []
    any_401 = False

    for linked_user in linked_users:
        candidate = factory(decrypt_secret(linked_user.bearer_token_encrypted))
        try:
            alerts = await _fetch_active_alerts(candidate)
        except ApiUnauthorized:
            any_401 = True
            await candidate.aclose()
            await auth_store.delete_linked_user(linked_user.telegram_user_id)
            continue
        except ApiClientError:
            # Network/5xx failure fetching alerts -- log, do NOT advance
            # the high-water-mark, retry next tick (step 9).
            await candidate.aclose()
            logger.warning(
                "alert poller: failed to fetch alerts this tick; will retry next tick", exc_info=True
            )
            return
        else:
            client = candidate
            break

    if client is None:
        # List was non-empty but every single token 401'd -- a real
        # problem worth a WARNING, not just DEBUG (distinct from the
        # empty-list case above, which is expected/normal).
        if any_401:
            logger.warning(
                "alert poller: all %d linked user token(s) are invalid (401); "
                "affected users must run /link again",
                len(linked_users),
            )
        return

    try:
        new_max_id = max((a.id for a in alerts), default=last_seen_alert_id)

        candidates = sorted(
            (
                a
                for a in alerts
                if a.id > last_seen_alert_id and a.channel in (AlertChannel.TELEGRAM, AlertChannel.BOTH)
            ),
            key=lambda a: a.id,
        )

        all_linked_users = await auth_store.list_linked_users()
        mark_delivered_token_ok = True

        for alert in candidates:
            text = formatting.format_alert_push(alert)
            for user in all_linked_users:
                try:
                    await bot.send_message(chat_id=user.chat_id, text=text)
                except TelegramError:
                    # Never log the token -- just the chat_id -- and keep
                    # going: one bad chat must not block the rest of the
                    # fan-out.
                    logger.warning(
                        "alert poller: failed to send alert #%s to chat_id=%s", alert.id, user.chat_id
                    )

            if mark_delivered_token_ok:
                try:
                    await client.mark_alert_telegram_delivered(alert.id)
                except ApiUnauthorized:
                    logger.warning(
                        "alert poller: mark-telegram-delivered token invalidated mid-tick; "
                        "DMs already sent for remaining candidates, only the delivered_telegram_at "
                        "bookkeeping timestamp will lag until next tick"
                    )
                    mark_delivered_token_ok = False
                except ApiClientError:
                    logger.warning(
                        "alert poller: mark-telegram-delivered failed for alert #%s", alert.id, exc_info=True
                    )

        # Persisted regardless of individual push/mark-delivered outcomes
        # (step 8) -- only a failure to FETCH alerts at all (handled above)
        # skips this.
        await state.save_last_seen_alert_id(new_max_id)
    finally:
        await client.aclose()


async def alert_poll_loop(
    bot: Bot,
    *,
    stop_event: asyncio.Event | None = None,
    api_client_factory: ApiClientFactory | None = None,
) -> None:
    """Runs forever until `stop_event` is set (or the task is cancelled):
    every `settings.BOT_ALERT_POLL_INTERVAL_SECONDS`, polls for new ACTIVE
    alerts and DMs every linked user. Structurally mirrors
    `app.workers.alert_worker.alert_worker_loop` -- a single tick's
    exception is caught and logged, never propagated, so one bad tick
    never kills the poller permanently.

    `api_client_factory` defaults to real-network `OrchestratorApiClient`
    instances built from `bot.config.settings`; tests may substitute a
    factory built around `httpx.ASGITransport` (see
    bot/api_client.py's `transport` param).
    """
    factory = api_client_factory or _default_api_client_factory
    while stop_event is None or not stop_event.is_set():
        try:
            await _run_poll_tick(bot, factory)
        except Exception:
            logger.exception("alert poller tick failed; will retry next tick")
        await asyncio.sleep(settings.BOT_ALERT_POLL_INTERVAL_SECONDS)
