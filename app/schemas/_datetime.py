"""Shared datetime-normalization helper for request schemas.

SQLite (via SQLAlchemy's `DateTime(timezone=True)` bind processor on this
dialect) does NOT convert a datetime to UTC before storing it -- it takes
the "raw" year/month/day/hour/minute/second fields as-is and ignores
`tzinfo` entirely. If a client sends a non-UTC offset (e.g.
"2026-08-08T09:00:00+05:00"), storing it naively would silently corrupt
any later arithmetic (e.g. `duration_seconds = finished_at - started_at`)
by up to the size of the offset.

`normalize_to_utc` must be used as a `field_validator` on every datetime
field accepted from a client that may later be persisted or used in
arithmetic against other persisted datetimes, so that by the time a value
reaches the ORM layer it is guaranteed to be either UTC-aware or
tz-naive-and-already-UTC (never a naive value carrying a hidden non-UTC
offset).
"""
from datetime import UTC, datetime


def normalize_to_utc(value: datetime | None) -> datetime | None:
    """Convert an aware datetime to UTC; leave naive datetimes untouched
    (naive values are treated as already being UTC, matching the
    round-trip behavior of values read back from SQLite -- see
    `app/routers/job_runs.py::_as_naive_utc`).
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value
