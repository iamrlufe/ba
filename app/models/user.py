"""User accounts for JWT authentication (app/core/auth.py).

Passwords are stored as salted hashes (never plaintext, never Fernet --
Fernet is reversible and is reserved for secrets the system itself needs to
read back, e.g. server credentials; password hashes must be one-way).
"""
from sqlalchemy import BigInteger, Boolean, Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import UserRole


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_role", "role"),
        Index("ix_users_is_active", "is_active"),
        Index("ix_users_telegram_user_id", "telegram_user_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    # Telegram bot linkage (see bot/ module + POST /api/auth/telegram-link).
    # Both nullable: a user is not required to ever link a Telegram account.
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Server-side bookkeeping only -- written by POST /api/auth/telegram-link,
    # never read back by the bot at request time (the bot keeps its own
    # encrypted copy locally in bot/auth_store.py). Never exposed in any
    # response schema; encrypted at rest via app.core.security.encrypt_secret.
    telegram_bot_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
