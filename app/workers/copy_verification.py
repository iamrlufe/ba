"""FTP copy-integrity module.

Sibling to `app.workers.backup_verification`, NOT a shared code path: that
module drives an internal PENDING->RUNNING->terminal state machine with a
blocking SQL Server connection (`asyncio.to_thread`). This module instead
receives an already-computed, already-terminal result over HTTP (from a
future, out-of-scope standalone agent that does a filesystem-level SHA-256
comparison against a `<file>.sha256` sidecar) and does a single insert +
alert call in one transaction -- see
POST /api/backup-records/{id}/report-copy-verification
(app/routers/backup_records.py).

The only code genuinely shared with app.workers.backup_verification is
`raise_alert_if_absent`/`resolve_active_alert` (app.routers._alerts),
which are already fully generic.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.backup_record import BackupRecord
from app.models.enums import AlertSeverity, AlertType, VerificationRunStatus, VerificationType
from app.models.verification_run import VerificationRun
from app.routers._alerts import raise_alert_if_absent, resolve_active_alert
from app.schemas.copy_verification import AgentCopyVerificationStatus, CopyVerificationReportRequest

# Mapping table -- see spec section 5.1.1. Reuses the exact same
# `severity = CRITICAL if status in (CORRUPT, MISSING) else WARNING`
# conditional already used in app.workers.backup_verification
# .execute_verification_run; do not invent a new severity policy here.
_STATUS_MAP: dict[AgentCopyVerificationStatus, VerificationRunStatus] = {
    AgentCopyVerificationStatus.OK: VerificationRunStatus.OK,
    AgentCopyVerificationStatus.MISMATCH: VerificationRunStatus.CORRUPT,
    AgentCopyVerificationStatus.MISSING_SIDECAR: VerificationRunStatus.MISSING,
    AgentCopyVerificationStatus.FILE_UNREADABLE: VerificationRunStatus.ERROR,
}

_ERROR_MESSAGES: dict[VerificationRunStatus, str] = {
    VerificationRunStatus.CORRUPT: (
        "FTP copy-integrity check reported MISMATCH: the actual SHA-256 "
        "checksum of the backup file did not match its <file>.sha256 sidecar"
    ),
    VerificationRunStatus.MISSING: (
        "FTP copy-integrity check reported MISSING_SIDECAR: no <file>.sha256 "
        "sidecar was found for this backup file"
    ),
    VerificationRunStatus.ERROR: (
        "FTP copy-integrity check reported FILE_UNREADABLE: the backup file "
        "or its sidecar could not be read to perform the check"
    ),
}


def map_agent_copy_status(status: AgentCopyVerificationStatus) -> VerificationRunStatus:
    """Maps the agent-facing status vocabulary to this backend's internal
    `VerificationRunStatus`. See module docstring / spec section 5.1.1 for
    the full mapping + rationale table."""
    return _STATUS_MAP[status]


async def record_copy_verification_result(
    session: AsyncSession,
    record: BackupRecord,
    payload: CopyVerificationReportRequest,
) -> VerificationRun:
    """Inserts a terminal `VerificationRun` (verification_type=
    FTP_COPY_INTEGRITY) for `record`, then raises/resolves
    `AlertType.FTP_COPY_INTEGRITY_FAILED` (deduplicated by
    `backup_record_id`, not `backup_job_id`).

    No PENDING/RUNNING phase, no CAS guard needed on insert: the partial
    unique index `uq_verification_runs_active_per_backup_job` only blocks
    PENDING/RUNNING rows, and this always inserts an already-terminal
    status -- multiple FTP_COPY_INTEGRITY rows may exist per
    backup_record_id over time (full history is intentional; see spec
    section 6, item 5).

    Caller (the router) is responsible for the final commit/refresh.
    """
    mapped_status = map_agent_copy_status(payload.status)

    run = VerificationRun(
        backup_job_id=record.backup_job_id,
        backup_record_id=record.id,
        verification_type=VerificationType.FTP_COPY_INTEGRITY,
        triggered_by="agent",
        status=mapped_status,
        started_at=payload.checked_at,
        finished_at=payload.checked_at,
        verifyonly_output=(
            f"actual_checksum={payload.actual_checksum}"
            if payload.actual_checksum is not None
            else None
        ),
        error_message=(
            None if mapped_status == VerificationRunStatus.OK else _ERROR_MESSAGES[mapped_status]
        ),
    )
    session.add(run)
    await session.flush()

    if mapped_status != VerificationRunStatus.OK:
        await raise_alert_if_absent(
            session,
            alert_type=AlertType.FTP_COPY_INTEGRITY_FAILED,
            severity=(
                AlertSeverity.CRITICAL
                if mapped_status in (VerificationRunStatus.CORRUPT, VerificationRunStatus.MISSING)
                else AlertSeverity.WARNING
            ),
            entity_type="backup_record",
            entity_column=Alert.backup_record_id,
            entity_id=record.id,
            title=f"FTP copy-integrity check failed for backup record {record.id}",
            message=(
                f"VerificationRun {run.id} for backup record {record.id} "
                f"('{record.file_name}' at '{record.remote_path}') came back "
                f"{mapped_status.value}: {run.error_message}"
            ),
        )
    else:
        await resolve_active_alert(
            session,
            alert_type=AlertType.FTP_COPY_INTEGRITY_FAILED,
            entity_type="backup_record",
            entity_column=Alert.backup_record_id,
            entity_id=record.id,
        )

    return run
