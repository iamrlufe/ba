"""Pure functions: API schema -> plain-text Telegram message.

No Telegram/httpx imports here -- trivially unit-testable in isolation.
Plain text only (`parse_mode=None` everywhere the bot sends a message --
see bot/handlers/*): alert titles/messages/usernames/database names are
free text that can contain Markdown/HTML special characters and would
cause Telegram "can't parse entities" failures under MarkdownV2/HTML parse
modes. This `Label: value` layout trades emphasis for reliable delivery.

NOTE on enum formatting: `AlertSeverity`/`AlertType`/`AlertStatus`/etc are
`class Foo(str, Enum)`, but `str()`/f-string interpolation on a member
still renders as e.g. "AlertSeverity.WARNING" (Enum's `__str__` takes
precedence over the `str` mixin here) -- every enum field below is
explicitly rendered via `.value` to avoid leaking that into a user-facing
message.
"""
from __future__ import annotations

from app.schemas.alert import AlertRead
from app.schemas.summary import DailySummary


def format_alert_line(alert: AlertRead) -> str:
    """One line, used by /alerts (bot/handlers/alerts.py)."""
    return f"#{alert.id} [{alert.severity.value}] {alert.alert_type.value} — {alert.title}"


def format_alert_push(alert: AlertRead) -> str:
    """A single new-alert push DM (bot/poller.py)."""
    lines = [
        f"New alert #{alert.id} [{alert.severity.value}]",
        f"Type: {alert.alert_type.value}",
        f"Title: {alert.title}",
        f"Message: {alert.message}",
    ]
    return "\n".join(lines)


def format_daily_summary(summary: DailySummary) -> str:
    """/status (bot/handlers/status.py): counts line, then one line per job
    whose status != "OK", then a pointer to /alerts if there are any active
    alerts."""
    counts = summary.counts
    lines = [
        f"Active alerts: {counts.active_alerts_total} | "
        f"Jobs OK: {counts.jobs_ok} | FAIL: {counts.jobs_fail} | MISSED: {counts.jobs_missed}",
    ]
    for job in summary.jobs:
        if job.status != "OK":
            lines.append(f"- {job.name} (#{job.backup_job_id}): {job.status}")
    if counts.active_alerts_total > 0:
        lines.append("Run /alerts to see active alerts.")
    return "\n".join(lines)
