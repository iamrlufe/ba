"""Local Fernet encrypt/decrypt helpers for the bot process.

Byte-for-byte mirror of the two functions in `app/core/security.py`
(`encrypt_secret`/`decrypt_secret`), except reading `BotSettings.FERNET_KEY`
(`bot/config.py`) instead of `app.core.config.settings.FERNET_KEY`.

This is a DELIBERATE duplication, not laziness: `app/core/security.py` does
`from app.core.config import settings`, and `app.core.config.Settings` has
required fields (`FERNET_KEY`, `JWT_SECRET_KEY`, `AGENT_API_KEY`) with no
defaults. Importing it from the bot process would force the bot's env to
carry secrets it has no functional need for (`JWT_SECRET_KEY`,
`AGENT_API_KEY`), widening its trust boundary for no benefit. Diff this
module against `app/core/security.py` periodically to confirm no drift.
"""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from bot.config import settings


@lru_cache
def _get_fernet() -> Fernet:
    return Fernet(settings.FERNET_KEY.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret, returning an opaque token safe to store."""
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a token previously produced by `encrypt_secret`.

    Raises `cryptography.fernet.InvalidToken` if the token is malformed or
    was encrypted with a different key.
    """
    try:
        plaintext = _get_fernet().decrypt(token.encode("utf-8"))
    except InvalidToken:
        raise
    return plaintext.decode("utf-8")
