"""Computes the remote FTP/SFTP directory a BackupJob's files are copied to.

Historically the agent (or some earlier iteration) built this path out of
internal numeric IDs, which is meaningless to a human browsing the FTP
server directly. This module replaces that with a human-readable structure
derived from the server's name, the job's name/id, and its backup type --
see `resolve_remote_directory`. Backed by
`BackupJob.remote_directory_override` (app/models/backup_job.py) for the
escape hatch, and `BackupJob.remote_directory` (hybrid_property) for the
call site that actually resolves it per-request.
"""
import re

from app.models.enums import BackupType

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

_BACKUP_TYPE_DIR_CODES = {
    BackupType.FULL: "FULL",
    BackupType.DIFFERENTIAL: "DIFF",
    BackupType.TRANSACTION_LOG: "TLOG",
    BackupType.CUSTOM: "CUSTOM",
}


_ALL_DOTS = re.compile(r"\.+")


def _sanitize_path_segment(raw: str, *, fallback: str) -> str:
    """Sanitizes a single path segment to `[A-Za-z0-9._-]`, falling back to
    `fallback` if the result would be unsafe.

    `.` is in the allowed character set (so ordinary display names like
    "v1.2" pass through untouched), but that means a name consisting
    entirely of dots -- e.g. Server.name == ".." -- survives the
    character-class substitution unchanged (".." has no disallowed
    characters to replace) and would otherwise resolve to a literal
    path-traversal segment (".", "..") in the computed remote directory.
    Guarded explicitly here: any sanitized result that is one-or-more dots
    and nothing else is treated the same as an empty result and replaced
    with `fallback`.
    """
    sanitized = _UNSAFE.sub("_", raw.strip())
    if not sanitized or _ALL_DOTS.fullmatch(sanitized):
        return fallback
    return sanitized


def resolve_remote_directory(
    server_name: str,
    job_name: str,
    job_id: int,
    backup_type: BackupType,
    remote_directory_override: str | None,
) -> str:
    """Returns the effective remote directory for a BackupJob.

    If `remote_directory_override` is not None, it is returned literally
    (the caller is responsible for validating it -- see
    app.schemas.backup_job._check_remote_directory_override) and every
    other argument is ignored. Otherwise a directory is computed as
    `<server_name>/<job_name>_<job_id>/<backup_type_code>/`, with
    `server_name`/`job_name` sanitized to a safe path-segment character set
    (`[A-Za-z0-9._-]`, everything else replaced with `_`) so operator-chosen
    display names can never introduce path separators or other unsafe
    characters into the resulting remote path. `job_id` is always appended
    to the job segment (even though job_name is already sanitized) to
    guarantee uniqueness across jobs that sanitize to the same string (e.g.
    two jobs literally named "Nightly Backup" and "Nightly/Backup").
    """
    if remote_directory_override is not None:
        return remote_directory_override
    job_segment = f"{_sanitize_path_segment(job_name, fallback='_unnamed_job')}_{job_id}"
    return (
        f"{_sanitize_path_segment(server_name, fallback='_unnamed_server')}/"
        f"{job_segment}/"
        f"{_BACKUP_TYPE_DIR_CODES[backup_type]}/"
    )
