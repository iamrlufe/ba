"""user telegram columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-09 00:00:00.000000

Adds `telegram_user_id` (unique, nullable) and
`telegram_bot_token_encrypted` to `users`, backing the Telegram bot module
(see bot/ and POST /api/auth/telegram-link in app/routers/auth.py).
`telegram_bot_token_encrypted` uses Text, matching the
Server.username_encrypted/password_encrypted encrypted-at-rest convention.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("telegram_bot_token_encrypted", sa.Text(), nullable=True))
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_column("users", "telegram_bot_token_encrypted")
    op.drop_column("users", "telegram_user_id")
