"""Async SQLAlchemy engine/session setup.

CRITICAL: SQLite does not enforce foreign keys unless `PRAGMA foreign_keys=ON`
is issued on every new DBAPI connection. Without this, ON DELETE
CASCADE/RESTRICT/SET NULL declared on the models are silently ignored.
"""
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=settings.SQL_ECHO)

async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Enable foreign key enforcement on every raw SQLite DBAPI connection.

    This is a sync-level event registered against the DBAPI connection
    object (not the async engine/session), which is the only place SQLite
    PRAGMAs can be reliably applied per-connection with SQLAlchemy.
    """
    # Only relevant for SQLite connections; other dialects ignore/ don't
    # have this concept. We defensively check for the pragma execution
    # capability rather than importing sqlite3 directly, since aiosqlite
    # wraps a sqlite3 connection.
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        # Non-SQLite DBAPI connections (or ones that don't support this
        # pragma) should not break connection setup.
        pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an AsyncSession, closed after the request."""
    async with async_session_maker() as session:
        yield session
