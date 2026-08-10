"""Pure unit tests for bot/formatting.py -- no mocking, no I/O."""
from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import AlertChannel, AlertSeverity, AlertStatus, AlertType, JobRunStatus
from app.schemas.alert import AlertRead
from app.schemas.summary import DailyJobStatus, DailySummary, DailySummaryCounts
from bot.formatting import format_alert_line, format_alert_push, format_daily_summary

_NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)


def _make_alert(**overrides) -> AlertRead:
    defaults = dict(
        id=1,
        alert_type=AlertType.JOB_FAILED,
        severity=AlertSeverity.WARNING,
        entity_type="backup_job",
        server_id=None,
        disk_id=None,
        backup_job_id=7,
        job_run_id=None,
        restore_operation_id=None,
        backup_record_id=None,
        title="Job failed",
        message="Something went wrong during the backup run.",
        status=AlertStatus.ACTIVE,
        channel=AlertChannel.BOTH,
        delivered_telegram_at=None,
        acknowledged_by=None,
        acknowledged_at=None,
        resolved_at=None,
        resolved_note=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return AlertRead.model_validate(defaults)


def test_format_alert_line_uses_enum_values_not_repr():
    alert = _make_alert(id=42, severity=AlertSeverity.CRITICAL, alert_type=AlertType.DISK_SPACE_LOW, title="Low disk space")
    line = format_alert_line(alert)
    assert line == "#42 [CRITICAL] DISK_SPACE_LOW — Low disk space"
    # Never leak Python's default Enum.__str__ repr into user-facing text.
    assert "AlertSeverity." not in line
    assert "AlertType." not in line


def test_format_alert_push_includes_id_type_title_message():
    alert = _make_alert(
        id=7,
        severity=AlertSeverity.INFO,
        alert_type=AlertType.AGENT_OFFLINE,
        title="Agent gone quiet",
        message="No heartbeat for 15 minutes.",
    )
    text = format_alert_push(alert)
    assert "New alert #7 [INFO]" in text
    assert "Type: AGENT_OFFLINE" in text
    assert "Title: Agent gone quiet" in text
    assert "Message: No heartbeat for 15 minutes." in text
    assert "AlertSeverity." not in text
    assert "AlertType." not in text


def _make_summary(*, active_alerts_total=0, jobs=None) -> DailySummary:
    jobs = jobs or []
    return DailySummary(
        generated_at=_NOW,
        window_start=_NOW,
        window_end=_NOW,
        active_alerts=[],
        jobs=jobs,
        counts=DailySummaryCounts(
            active_alerts_total=active_alerts_total,
            jobs_ok=sum(1 for j in jobs if j.status == "OK"),
            jobs_fail=sum(1 for j in jobs if j.status == "FAIL"),
            jobs_missed=sum(1 for j in jobs if j.status == "MISSED"),
        ),
    )


def test_format_daily_summary_all_ok_no_alerts_hint():
    summary = _make_summary(
        active_alerts_total=0,
        jobs=[
            DailyJobStatus(
                backup_job_id=1, name="nightly-full", status="OK", last_run_id=1,
                last_run_status=JobRunStatus.SUCCESS, last_run_finished_at=_NOW,
            )
        ],
    )
    text = format_daily_summary(summary)
    lines = text.splitlines()
    assert lines[0] == "Active alerts: 0 | Jobs OK: 1 | FAIL: 0 | MISSED: 0"
    # OK jobs are not individually listed.
    assert "nightly-full" not in text
    assert "Run /alerts" not in text


def test_format_daily_summary_lists_non_ok_jobs_and_alerts_hint():
    summary = _make_summary(
        active_alerts_total=2,
        jobs=[
            DailyJobStatus(
                backup_job_id=1, name="nightly-full", status="OK", last_run_id=1,
                last_run_status=JobRunStatus.SUCCESS, last_run_finished_at=_NOW,
            ),
            DailyJobStatus(
                backup_job_id=2, name="hourly-diff", status="FAIL", last_run_id=2,
                last_run_status=JobRunStatus.FAILED, last_run_finished_at=_NOW,
            ),
            DailyJobStatus(
                backup_job_id=3, name="weekly-log", status="MISSED", last_run_id=None,
                last_run_status=None, last_run_finished_at=None,
            ),
        ],
    )
    text = format_daily_summary(summary)
    assert "Active alerts: 2 | Jobs OK: 1 | FAIL: 1 | MISSED: 1" in text
    assert "- hourly-diff (#2): FAIL" in text
    assert "- weekly-log (#3): MISSED" in text
    assert "nightly-full" not in text  # OK job not listed
    assert "Run /alerts to see active alerts." in text
