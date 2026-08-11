"""Shared generic/utility schemas used across resource-specific modules."""
from datetime import UTC, datetime
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    detail: str


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo to a naive datetime before JSON serialization.

    DB-sourced datetimes come back tz-naive after a round trip through
    SQLite (see app/core/timeutils.py::as_naive_utc) even though they were
    written from an aware `datetime.now(UTC)` or a UTC `server_default`.
    By the time a value reaches a Pydantic response schema, "naive" already
    means "UTC" everywhere in this codebase except
    VerificationRun.msdb_backup_date (sourced from SQL Server's own clock,
    NOT this app -- see that field's schema annotation). This only affects
    how the value is labeled when serialized to JSON; it does not change
    the value's meaning or touch DB storage, arithmetic, or comparisons.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


UtcDatetime = Annotated[
    datetime, PlainSerializer(_ensure_utc, return_type=datetime, when_used="json")
]
