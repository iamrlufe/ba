"""Blocking SQL Server client seam for backup verification.

This is the ONLY module in this codebase that imports `pytds` -- everything
above it (app/workers/backup_verification.py) talks to the `SqlClient`
protocol, never to pytds directly, so it can be swapped out in tests via
`sql_client_factory`.

Every call in here is a BLOCKING network call. Callers MUST run this from a
worker thread (`asyncio.to_thread`), never directly on the event loop.

NEVER log a decrypted or encrypted credential value anywhere in this
module, not even at DEBUG.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

import pytds

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SqlConnectionParams:
    host: str
    port: int | None
    instance_name: str | None
    username: str | None  # decrypted plaintext; None when windows-auth
    password: str | None  # decrypted plaintext; None when windows-auth
    use_windows_auth: bool
    connect_timeout_seconds: int


@dataclass(frozen=True)
class MsdbBackupInfo:
    backup_finish_date: datetime | None
    is_damaged: bool


@dataclass(frozen=True)
class VerifyOnlyResult:
    succeeded: bool
    output: str | None
    error_message: str | None
    error_number: int | None


class SqlClient(Protocol):
    def get_latest_backupset(self, database_name: str) -> MsdbBackupInfo | None: ...

    def restore_verifyonly(self, disk_path: str, *, timeout_seconds: int) -> VerifyOnlyResult: ...

    def close(self) -> None: ...


SqlClientFactory = Callable[[SqlConnectionParams], "SqlClient"]


class WindowsAuthNotSupportedError(NotImplementedError):
    """Raised by `default_sql_client_factory` when
    `SqlConnectionParams.use_windows_auth` is True.

    A dedicated subclass (rather than a bare NotImplementedError) so
    callers/tests can catch it specifically if ever needed, while still
    satisfying "raise a clear NotImplementedError (or a small custom
    exception)" from the spec. The caller in
    app.workers.backup_verification classifies this exactly like any other
    connect-time exception (ERROR).
    """


# RESTORE VERIFYONLY FROM DISK = '<path>' requires a T-SQL string LITERAL --
# most drivers, including pytds, do not support a parameterized placeholder
# inside this statement form. A blacklist of "dangerous"
# characters/sequences is fragile (easy to miss a combination that still
# breaks out of the literal); a strict allowlist is safe by construction
# instead: only the characters that can ever legitimately appear in a
# Windows UNC path are permitted at all -- letters, digits, backslash,
# colon, dot, underscore, hyphen, space, and dollar sign (admin shares like
# \\server\D$\...). A single quote is NOT in this allowlist, so there is no
# need for (and this deliberately does NOT do) any quote-doubling/escaping
# -- a path containing a quote is rejected outright, not sanitized.
_DISK_PATH_ALLOWED_CHARS = re.compile(r"^[A-Za-z0-9\\:._\- $]+$")


def _validate_disk_path(disk_path: str) -> None:
    # fullmatch, not match: `match` only anchors at the START of the
    # string, and `$` (without re.MULTILINE) matches either end-of-string
    # OR immediately before a single trailing "\n" -- so `match` alone
    # would let a disallowed trailing "\n" slip through (e.g. a
    # BackupRecord.file_name of "nightly.bak\n"), even though "\n" is not
    # in the allowed character set. fullmatch requires the whole string to
    # match, closing that gap.
    if not disk_path or not _DISK_PATH_ALLOWED_CHARS.fullmatch(disk_path):
        raise ValueError(
            f"Refusing to use disk path for RESTORE VERIFYONLY -- it contains "
            f"characters outside the allowed set (letters, digits, "
            f"\\ : . _ - space $): {disk_path!r}"
        )


def _collect_messages(cursor: Any) -> str | None:
    """Extract server INFO/message-token text accumulated on `cursor`
    (see pytds.cursor.Cursor.messages -- a list of (exception_type,
    exception_instance) tuples, one per server message token). Returns
    None if there is nothing to report, never raises.
    """
    try:
        messages = cursor.messages
    except Exception:
        return None
    if not messages:
        return None
    lines = []
    for _exc_type, exc in messages:
        text = getattr(exc, "text", None) or str(exc)
        lines.append(text)
    return "\n".join(lines) if lines else None


class _PytdsSqlClient:
    """`SqlClient` implementation backed by `pytds.connect(...)`."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def get_latest_backupset(self, database_name: str) -> MsdbBackupInfo | None:
        cursor = self._connection.cursor()
        try:
            # pytds's installed paramstyle is 'pyformat' (%s placeholders),
            # NOT the qmark ('?') style referenced in some pytds docs/older
            # versions -- confirmed against the installed python-tds
            # version via `pytds.paramstyle`. This is still a genuinely
            # parameterized, injection-safe call (unlike the RESTORE
            # VERIFYONLY path below): pytds substitutes `database_name` as
            # a bound parameter, never via string formatting here.
            cursor.execute(
                "SELECT TOP 1 backup_finish_date, is_damaged "
                "FROM msdb.dbo.backupset WHERE database_name = %s "
                "ORDER BY backup_finish_date DESC",
                (database_name,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()

        if row is None:
            return None
        backup_finish_date, is_damaged = row[0], row[1]
        return MsdbBackupInfo(
            backup_finish_date=backup_finish_date,
            is_damaged=bool(is_damaged),
        )

    def _set_query_timeout(self, timeout_seconds: int) -> None:
        """Best-effort per-call override of the connection's socket
        timeout for the upcoming RESTORE VERIFYONLY.

        pytds does not expose a public "set query timeout for this one
        call" API on Connection/Cursor (its `timeout=` connect() kwarg only
        sets the socket timeout once, for the lifetime of the connection).
        We reach into the (private, but stable across recent pytds
        releases) transport object to override it dynamically instead.
        This is deliberately defensive: if pytds's internal layout ever
        changes and this attribute chain no longer exists, we log and
        continue with whatever timeout was set at connect time, rather
        than failing the whole verification over a best-effort timeout
        tightening. Logged at WARNING, not DEBUG: silently falling back to
        BACKUP_VERIFICATION_CONNECT_TIMEOUT_SECONDS (typically much shorter
        than BACKUP_VERIFICATION_RESTORE_TIMEOUT_SECONDS) would otherwise
        cause every large-database verification to start failing after a
        pytds upgrade, with no visible signal at the default log level.
        `OSError` is also caught here (not just `AttributeError`): the
        attribute chain can resolve to a real socket that is already
        closed/torn down, where `.settimeout()` itself raises `OSError`
        rather than `AttributeError` -- this is still a "couldn't tighten
        the timeout" case, not a fatal one, and must not escape this
        best-effort helper.
        """
        try:
            self._connection._tds_socket.main_session._transport.settimeout(timeout_seconds)
        except (AttributeError, OSError):
            logger.warning(
                "could not set the intended %ss RESTORE VERIFYONLY timeout "
                "(pytds internal transport attribute chain not found or unusable); "
                "falling back to whatever timeout was set at connect time "
                "(BACKUP_VERIFICATION_CONNECT_TIMEOUT_SECONDS), which is typically "
                "much shorter and may cause a large backup's verification to time "
                "out prematurely",
                timeout_seconds,
            )

    def restore_verifyonly(self, disk_path: str, *, timeout_seconds: int) -> VerifyOnlyResult:
        _validate_disk_path(disk_path)
        self._set_query_timeout(timeout_seconds)

        # Safe now -- the allowlist above guarantees disk_path cannot
        # contain a quote, semicolon, comment sequence, or any other T-SQL
        # metacharacter, so this f-string cannot be used to break out of
        # the string literal it's building.
        sql = f"RESTORE VERIFYONLY FROM DISK = '{disk_path}'"

        cursor = self._connection.cursor()
        try:
            cursor.execute(sql)
            try:
                cursor.fetchall()
            except pytds.Error:
                # RESTORE VERIFYONLY normally produces no result set, only
                # message tokens -- some pytds/TDS paths raise when asked
                # to fetch from a statement with no rows. Not an error
                # condition in itself.
                pass
            output = _collect_messages(cursor)
            return VerifyOnlyResult(
                succeeded=True, output=output, error_message=None, error_number=None
            )
        except pytds.Error as exc:
            output = _collect_messages(cursor)
            return VerifyOnlyResult(
                succeeded=False,
                output=output,
                error_message=str(exc),
                error_number=getattr(exc, "number", None),
            )
        finally:
            cursor.close()

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:
            logger.debug("error closing pytds connection", exc_info=True)


def default_sql_client_factory(params: SqlConnectionParams) -> SqlClient:
    """Connects via `pytds.connect(...)`. Raises on failure -- connection
    errors propagate to the caller, which classifies them as ERROR. Never
    catches/swallows connection errors here.
    """
    if params.use_windows_auth:
        raise WindowsAuthNotSupportedError(
            "use_windows_auth=True is not supported by the backup-verification "
            "module in this pass; SQL-authentication credentials are required"
        )

    dsn = params.host if not params.instance_name else f"{params.host}\\{params.instance_name}"
    connect_kwargs: dict[str, Any] = dict(
        dsn=dsn,
        user=params.username,
        password=params.password,
        login_timeout=params.connect_timeout_seconds,
        autocommit=True,
    )
    # pytds raises ValueError if both instance and port are specified.
    if params.instance_name is None and params.port is not None:
        connect_kwargs["port"] = params.port

    connection = pytds.connect(**connect_kwargs)
    return _PytdsSqlClient(connection)
