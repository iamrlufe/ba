"""Shared datetime helpers for arithmetic against SQLite-round-tripped
values, plus cron-schedule evaluation for the background alert worker.
"""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter


def as_naive_utc(dt: datetime) -> datetime:
    """Normalize a datetime for arithmetic against SQLite-round-tripped values.

    SQLite has no native timezone-aware datetime type: even though the ORM
    columns are declared `DateTime(timezone=True)`, values read back after
    a round trip through the DB come back tz-naive, while freshly
    constructed Python datetimes (e.g. `datetime.now(UTC)`) are
    tz-aware. Subtracting a naive and an aware datetime raises `TypeError`,
    so both operands are normalized to naive UTC before arithmetic here.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def compute_next_scheduled_run(schedule_cron: str, timezone_name: str, after: datetime) -> datetime:
    """Return the next cron-scheduled fire time strictly after `after`,
    evaluated in `timezone_name` (IANA name, e.g. "UTC", "Europe/Moscow" --
    matches BackupJob.timezone), returned as naive UTC (matching the
    SQLite round-trip convention used everywhere else in this codebase).

    `after` may be naive (treated as already-UTC) or aware; normalized via
    `as_naive_utc` first.

    Raises croniter's cron-parse exception (verify the exact class name
    against the installed croniter version, e.g. `croniter.CroniterBadCronError`)
    if `schedule_cron` is not a valid 5-field cron expression. Callers MUST
    catch and handle this defensively (see check_missed_runs).
    """
    after_utc_aware = as_naive_utc(after).replace(tzinfo=UTC)
    after_local = after_utc_aware.astimezone(ZoneInfo(timezone_name))
    next_local = croniter(schedule_cron, after_local).get_next(datetime)
    return as_naive_utc(next_local.astimezone(UTC))
