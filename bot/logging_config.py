"""Logging setup for the bot process.

`httpx`/`httpcore` log full request lines (including headers -- i.e. the
`Authorization: Bearer <token>` header) at DEBUG, and `telegram`
(python-telegram-bot) logs full `Update` payloads (which can include a
`/link`'s plaintext password, in-flight before the handler even runs) at
DEBUG. Both are force-set to at least INFO here, regardless of
`BOT_LOG_LEVEL`, so a well-meaning operator setting `BOT_LOG_LEVEL=DEBUG`
for their own handler code can never accidentally leak a secret into logs.
"""
import logging

from bot.config import settings

_NOISY_LOGGERS_MIN_LEVEL = logging.INFO


def configure_logging() -> None:
    logging.basicConfig(
        level=settings.BOT_LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for name in ("httpx", "httpcore", "telegram"):
        logging.getLogger(name).setLevel(_NOISY_LOGGERS_MIN_LEVEL)
