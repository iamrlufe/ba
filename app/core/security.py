"""Shared helpers for encrypting/decrypting secrets at rest.

Every credential, password, API key, or connection string that touches the
database MUST be encrypted with `encrypt_secret` before insert, and MUST
only be decrypted with `decrypt_secret` at the point of use (e.g. right
before opening an FTP/SFTP/SQL connection). Never call `Fernet` directly
anywhere else in the codebase -- go through this module so there is a
single place to audit / rotate keys.
"""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


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
