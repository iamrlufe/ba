"""HTTP-layer tests for POST /api/backup-records/{id}/report-copy-verification
(app/routers/backup_records.py + app/workers/copy_verification.py).
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.models.alert import Alert
from app.models.enums import AlertSeverity, AlertStatus, AlertType, VerificationType
from tests.conftest import build_backup_job, build_backup_record, build_disk, build_server


async def _job(session):
    server = build_server()
    session.add(server)
    await session.commit()
    disk = build_disk(server.id)
    session.add(disk)
    await session.commit()
    job = build_backup_job(server.id, disk.id)
    session.add(job)
    await session.commit()
    return job


async def _record(session, job=None):
    if job is None:
        job = await _job(session)
    record = build_backup_record(job.id)
    session.add(record)
    await session.commit()
    return record, job


async def _active_alert(session, record_id: int):
    from sqlalchemy import select

    result = await session.execute(
        select(Alert).where(
            Alert.alert_type == AlertType.FTP_COPY_INTEGRITY_FAILED,
            Alert.status == AlertStatus.ACTIVE,
            Alert.backup_record_id == record_id,
        )
    )
    return result.scalar_one_or_none()


# --------------------------------------------------------------------------
# Happy paths -- all 4 agent statuses.
# --------------------------------------------------------------------------


async def test_report_ok_status_creates_terminal_run(admin_client, session):
    record, job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "OK", "actual_checksum": "abc123", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "OK"
    assert body["verification_type"] == "FTP_COPY_INTEGRITY"
    assert body["backup_job_id"] == job.id
    assert body["backup_record_id"] == record.id
    assert body["error_message"] is None
    assert body["verifyonly_output"] == "actual_checksum=abc123"
    assert body["triggered_by"] == "agent"


async def test_report_mismatch_status_maps_to_corrupt(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "MISMATCH", "actual_checksum": "wrong-hash", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "CORRUPT"
    assert body["error_message"] is not None


async def test_report_missing_sidecar_status_maps_to_missing(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "MISSING_SIDECAR", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "MISSING"
    assert body["verifyonly_output"] is None


async def test_report_file_unreadable_status_maps_to_error(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "FILE_UNREADABLE", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ERROR"


# --------------------------------------------------------------------------
# Alert raise/dedup/resolve behavior.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_status,expected_severity",
    [
        ("MISMATCH", AlertSeverity.CRITICAL),
        ("MISSING_SIDECAR", AlertSeverity.CRITICAL),
        ("FILE_UNREADABLE", AlertSeverity.WARNING),
    ],
)
async def test_failing_report_raises_alert_with_correct_severity(
    admin_client, session, agent_status, expected_severity
):
    record, _job = await _record(session)
    payload = {"status": agent_status, "checked_at": "2026-08-10T00:00:00Z"}
    if agent_status == "MISMATCH":
        payload["actual_checksum"] = "wrong-hash"

    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification", json=payload
    )
    assert resp.status_code == 201

    alert = await _active_alert(session, record.id)
    assert alert is not None
    assert alert.severity == expected_severity
    assert alert.entity_type == "backup_record"
    assert alert.backup_record_id == record.id


async def test_second_failing_report_does_not_duplicate_alert(admin_client, session):
    record, _job = await _record(session)
    payload = {"status": "MISMATCH", "actual_checksum": "bad-1", "checked_at": "2026-08-10T00:00:00Z"}

    first = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification", json=payload
    )
    assert first.status_code == 201

    second_payload = {**payload, "actual_checksum": "bad-2"}
    second = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification", json=second_payload
    )
    assert second.status_code == 201

    from sqlalchemy import func, select

    count = (
        await session.execute(
            select(func.count()).select_from(Alert).where(
                Alert.alert_type == AlertType.FTP_COPY_INTEGRITY_FAILED,
                Alert.status == AlertStatus.ACTIVE,
                Alert.backup_record_id == record.id,
            )
        )
    ).scalar_one()
    assert count == 1


async def test_ok_report_after_failure_resolves_alert(admin_client, session):
    record, _job = await _record(session)
    record_id = record.id

    fail_resp = await admin_client.post(
        f"/api/backup-records/{record_id}/report-copy-verification",
        json={"status": "MISMATCH", "actual_checksum": "bad", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert fail_resp.status_code == 201
    assert await _active_alert(session, record_id) is not None

    ok_resp = await admin_client.post(
        f"/api/backup-records/{record_id}/report-copy-verification",
        json={"status": "OK", "actual_checksum": "good", "checked_at": "2026-08-10T01:00:00Z"},
    )
    assert ok_resp.status_code == 201
    session.expire_all()
    assert await _active_alert(session, record_id) is None

    from sqlalchemy import select

    resolved = (
        await session.execute(
            select(Alert).where(
                Alert.alert_type == AlertType.FTP_COPY_INTEGRITY_FAILED,
                Alert.backup_record_id == record_id,
                Alert.status == AlertStatus.RESOLVED,
            )
        )
    ).scalar_one()
    assert resolved.resolved_at is not None


async def test_multiple_runs_recorded_as_full_history(admin_client, session):
    """No PENDING/RUNNING phase, no active-run exclusivity for
    FTP_COPY_INTEGRITY runs -- repeated reports for the same record must
    each insert a new terminal VerificationRun row (spec section 6, item 5)."""
    record, _job = await _record(session)

    for i in range(3):
        resp = await admin_client.post(
            f"/api/backup-records/{record.id}/report-copy-verification",
            json={"status": "OK", "actual_checksum": f"hash-{i}", "checked_at": "2026-08-10T00:00:00Z"},
        )
        assert resp.status_code == 201

    from sqlalchemy import func, select
    from app.models.verification_run import VerificationRun

    count = (
        await session.execute(
            select(func.count()).select_from(VerificationRun).where(
                VerificationRun.backup_record_id == record.id,
                VerificationRun.verification_type == VerificationType.FTP_COPY_INTEGRITY,
            )
        )
    ).scalar_one()
    assert count == 3


# --------------------------------------------------------------------------
# Schema validation.
# --------------------------------------------------------------------------


async def test_ok_status_without_actual_checksum_is_422(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "OK", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 422


async def test_mismatch_status_without_actual_checksum_is_422(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "MISMATCH", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 422


async def test_missing_sidecar_status_with_actual_checksum_is_422(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "MISSING_SIDECAR", "actual_checksum": "x", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 422


async def test_file_unreadable_status_with_actual_checksum_is_422(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "FILE_UNREADABLE", "actual_checksum": "x", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# 404
# --------------------------------------------------------------------------


async def test_report_copy_verification_missing_record_is_404(admin_client):
    resp = await admin_client.post(
        "/api/backup-records/999999/report-copy-verification",
        json={"status": "OK", "actual_checksum": "x", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Auth matrix (require_admin_or_agent_key).
# --------------------------------------------------------------------------


async def test_report_copy_verification_valid_agent_key_succeeds_without_jwt(client, session):
    record, _job = await _record(session)
    client.headers["X-Agent-Key"] = settings.AGENT_API_KEY
    resp = await client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "OK", "actual_checksum": "x", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 201


async def test_report_copy_verification_admin_jwt_succeeds_without_agent_key(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "OK", "actual_checksum": "x", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 201


async def test_report_copy_verification_operator_jwt_is_403(operator_client, session):
    record, _job = await _record(session)
    resp = await operator_client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "OK", "actual_checksum": "x", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 403


async def test_report_copy_verification_wrong_agent_key_is_401(client, session):
    record, _job = await _record(session)
    client.headers["X-Agent-Key"] = "not-the-real-key"
    resp = await client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "OK", "actual_checksum": "x", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 401


async def test_report_copy_verification_no_auth_at_all_is_401(client, session):
    record, _job = await _record(session)
    resp = await client.post(
        f"/api/backup-records/{record.id}/report-copy-verification",
        json={"status": "OK", "actual_checksum": "x", "checked_at": "2026-08-10T00:00:00Z"},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# GET /api/backup-records (new list endpoint)
# --------------------------------------------------------------------------


async def test_list_backup_records_no_filter(admin_client, session):
    record, _job = await _record(session)
    resp = await admin_client.get("/api/backup-records")
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert record.id in ids


async def test_list_backup_records_filter_by_backup_job_id(admin_client, session):
    record_a, job_a = await _record(session)
    record_b, job_b = await _record(session)

    resp = await admin_client.get("/api/backup-records", params={"backup_job_id": job_a.id})
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert record_a.id in ids
    assert record_b.id not in ids


async def test_list_backup_records_filter_by_remote_path_and_file_name(admin_client, session):
    job = await _job(session)
    record = build_backup_record(job.id, remote_path="/remote/shared", file_name="dump.bak")
    session.add(record)
    await session.commit()

    other_job = await _job(session)
    other_record = build_backup_record(other_job.id, remote_path="/remote/other", file_name="other.bak")
    session.add(other_record)
    await session.commit()

    resp = await admin_client.get(
        "/api/backup-records", params={"remote_path": "/remote/shared", "file_name": "dump.bak"}
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {record.id}


async def test_list_backup_records_matches_across_multiple_jobs_when_job_id_omitted(admin_client, session):
    """Same relative path/filename under two different jobs -- may
    legitimately return more than one row when backup_job_id is omitted."""
    job_a = await _job(session)
    job_b = await _job(session)
    record_a = build_backup_record(job_a.id, remote_path="/remote/shared", file_name="common.bak")
    record_b = build_backup_record(job_b.id, remote_path="/remote/shared", file_name="common.bak")
    session.add_all([record_a, record_b])
    await session.commit()

    resp = await admin_client.get(
        "/api/backup-records", params={"remote_path": "/remote/shared", "file_name": "common.bak"}
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {record_a.id, record_b.id}


async def test_list_backup_records_partial_remote_path_only_is_422(admin_client, session):
    await _record(session)
    resp = await admin_client.get("/api/backup-records", params={"remote_path": "/remote/x"})
    assert resp.status_code == 422


async def test_list_backup_records_partial_file_name_only_is_422(admin_client, session):
    await _record(session)
    resp = await admin_client.get("/api/backup-records", params={"file_name": "x.bak"})
    assert resp.status_code == 422


async def test_list_backup_records_requires_admin_or_agent_key(client, session):
    await _record(session)
    resp = await client.get("/api/backup-records")
    assert resp.status_code == 401


async def test_list_backup_records_operator_jwt_is_403(operator_client, session):
    await _record(session)
    resp = await operator_client.get("/api/backup-records")
    assert resp.status_code == 403


# --------------------------------------------------------------------------
# GET /api/backup-jobs/{id}/verification-runs -- verification_type /
# backup_record_id filter extension.
# --------------------------------------------------------------------------


async def test_verification_runs_filter_by_verification_type_isolates_flows(admin_client, session):
    from app.models.enums import VerificationRunStatus
    from tests.conftest import build_verification_run

    job = await _job(session)
    restore_run = build_verification_run(job.id, status=VerificationRunStatus.OK)
    copy_run = build_verification_run(
        job.id,
        status=VerificationRunStatus.OK,
        verification_type=VerificationType.FTP_COPY_INTEGRITY,
        triggered_by="agent",
    )
    session.add_all([restore_run, copy_run])
    await session.commit()

    resp = await admin_client.get(
        f"/api/backup-jobs/{job.id}/verification-runs",
        params={"verification_type": "FTP_COPY_INTEGRITY"},
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {copy_run.id}

    resp2 = await admin_client.get(
        f"/api/backup-jobs/{job.id}/verification-runs",
        params={"verification_type": "RESTORE_VERIFYONLY"},
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    ids2 = {item["id"] for item in body2["items"]}
    assert ids2 == {restore_run.id}


async def test_verification_runs_filter_by_backup_record_id(admin_client, session):
    from app.models.enums import VerificationRunStatus
    from tests.conftest import build_verification_run

    job = await _job(session)
    record_a = build_backup_record(job.id)
    record_b = build_backup_record(job.id)
    session.add_all([record_a, record_b])
    await session.commit()

    run_a = build_verification_run(
        job.id,
        status=VerificationRunStatus.OK,
        verification_type=VerificationType.FTP_COPY_INTEGRITY,
        backup_record_id=record_a.id,
        triggered_by="agent",
    )
    run_b = build_verification_run(
        job.id,
        status=VerificationRunStatus.OK,
        verification_type=VerificationType.FTP_COPY_INTEGRITY,
        backup_record_id=record_b.id,
        triggered_by="agent",
    )
    session.add_all([run_a, run_b])
    await session.commit()

    resp = await admin_client.get(
        f"/api/backup-jobs/{job.id}/verification-runs",
        params={"backup_record_id": record_a.id},
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {run_a.id}


async def test_verification_runs_mismatched_backup_record_and_job_returns_empty(admin_client, session):
    """A backup_record_id filter that doesn't actually belong to
    backup_job_id must return an empty list, not an error -- pure AND
    filter, no cross-validation (spec section 5.3)."""
    from tests.conftest import build_verification_run

    job_a = await _job(session)
    job_b = await _job(session)
    record_b = build_backup_record(job_b.id)
    session.add(record_b)
    await session.commit()

    run_a = build_verification_run(job_a.id, triggered_by="scheduler")
    session.add(run_a)
    await session.commit()

    resp = await admin_client.get(
        f"/api/backup-jobs/{job_a.id}/verification-runs",
        params={"backup_record_id": record_b.id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
