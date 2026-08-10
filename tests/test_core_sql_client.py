"""Unit tests for `app.core.sql_client` -- the sole `pytds` import site.

No real `pytds` connection is ever opened here: `_PytdsSqlClient` is tested
against hand-built fake connection/cursor objects satisfying the exact
duck-typed interface it calls (`.cursor()` / `.execute()` / `.fetchone()` /
`.fetchall()` / `.messages` / `.close()`), and `default_sql_client_factory`
is tested by monkeypatching `app.core.sql_client.pytds.connect` -- never by
hitting a real network.
"""
from __future__ import annotations

import logging

import pytest
import pytds

from app.core import sql_client as sql_client_module
from app.core.sql_client import (
    MsdbBackupInfo,
    SqlConnectionParams,
    WindowsAuthNotSupportedError,
    _PytdsSqlClient,
    _validate_disk_path,
    default_sql_client_factory,
)


# ---------------------------------------------------------------------------
# Fake pytds-shaped connection/cursor
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(
        self,
        *,
        fetchone_result=None,
        fetchall_exc: Exception | None = None,
        execute_exc: Exception | None = None,
        messages=None,
    ):
        self.fetchone_result = fetchone_result
        self.fetchall_exc = fetchall_exc
        self.execute_exc = execute_exc
        self.messages = messages if messages is not None else []
        self.executed: list[tuple] = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.execute_exc is not None:
            raise self.execute_exc

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        if self.fetchall_exc is not None:
            raise self.fetchall_exc
        return []

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


class _RaisingCursorConnection:
    """A connection whose `.cursor()` must never be called."""

    def cursor(self):
        raise AssertionError("cursor() should never be called")


class FakeTransport:
    def __init__(self, *, raise_oserror: bool = False):
        self.raise_oserror = raise_oserror
        self.timeout_set: int | None = None

    def settimeout(self, timeout_seconds):
        if self.raise_oserror:
            raise OSError("socket already closed")
        self.timeout_set = timeout_seconds


class _FakeSession:
    def __init__(self, transport):
        self._transport = transport


class _FakeTdsSocket:
    def __init__(self, transport):
        self.main_session = _FakeSession(transport)


class FakeConnectionWithTransport(FakeConnection):
    def __init__(self, cursor: FakeCursor, transport: FakeTransport):
        super().__init__(cursor)
        self._tds_socket = _FakeTdsSocket(transport)


# ---------------------------------------------------------------------------
# _validate_disk_path
# ---------------------------------------------------------------------------


def test_validate_disk_path_accepts_ordinary_unc_path():
    _validate_disk_path(r"\\fileserver\backups$\nightly.bak")  # must not raise


def test_validate_disk_path_accepts_local_windows_path():
    _validate_disk_path(r"D:\backups\nightly_2026-01-15.bak")  # must not raise


def test_validate_disk_path_rejects_trailing_newline_regression():
    """Regression test for the `.match()` vs `.fullmatch()` bug: `$` (without
    re.MULTILINE) matches immediately before a trailing "\\n" under `match`,
    so a disallowed trailing newline could slip through. `fullmatch` closes
    that gap."""
    with pytest.raises(ValueError):
        _validate_disk_path("nightly.bak\n")


def test_validate_disk_path_rejects_embedded_quote():
    with pytest.raises(ValueError):
        _validate_disk_path("nightly.bak' ; DROP TABLE x --")


def test_validate_disk_path_rejects_empty_string():
    with pytest.raises(ValueError):
        _validate_disk_path("")


def test_validate_disk_path_rejects_semicolon():
    with pytest.raises(ValueError):
        _validate_disk_path("nightly.bak;")


# ---------------------------------------------------------------------------
# _PytdsSqlClient.get_latest_backupset
# ---------------------------------------------------------------------------


def test_get_latest_backupset_returns_info_on_row():
    from datetime import UTC, datetime

    finish_date = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)
    cursor = FakeCursor(fetchone_result=(finish_date, 0, r"\\fileserver\backups$\orders.bak"))
    client = _PytdsSqlClient(FakeConnection(cursor))

    info = client.get_latest_backupset("orders")

    assert info == MsdbBackupInfo(
        backup_finish_date=finish_date,
        is_damaged=False,
        physical_device_name=r"\\fileserver\backups$\orders.bak",
    )
    assert cursor.closed is True


def test_get_latest_backupset_returns_none_when_no_row():
    cursor = FakeCursor(fetchone_result=None)
    client = _PytdsSqlClient(FakeConnection(cursor))

    assert client.get_latest_backupset("orders") is None
    assert cursor.closed is True


def test_get_latest_backupset_is_damaged_true():
    cursor = FakeCursor(fetchone_result=(None, 1, None))
    client = _PytdsSqlClient(FakeConnection(cursor))

    info = client.get_latest_backupset("orders")
    assert info.is_damaged is True


def test_get_latest_backupset_returns_physical_device_name_from_row():
    cursor = FakeCursor(fetchone_result=(None, 0, r"D:\backups\orders.bak"))
    client = _PytdsSqlClient(FakeConnection(cursor))

    info = client.get_latest_backupset("orders")
    assert info.physical_device_name == r"D:\backups\orders.bak"


def test_get_latest_backupset_physical_device_name_none_on_left_join_miss():
    """A backupmediafamily row may not exist even though the backupset row
    does (LEFT JOIN miss) -- physical_device_name must be None, not raise,
    and the backupset row must still be returned (not treated as MISSING)."""
    cursor = FakeCursor(fetchone_result=(None, 0, None))
    client = _PytdsSqlClient(FakeConnection(cursor))

    info = client.get_latest_backupset("orders")
    assert info is not None
    assert info.physical_device_name is None


def test_get_latest_backupset_uses_parameterized_query_not_string_interpolation():
    cursor = FakeCursor(fetchone_result=None)
    client = _PytdsSqlClient(FakeConnection(cursor))

    client.get_latest_backupset("orders'; DROP TABLE x --")

    assert len(cursor.executed) == 1
    sql, params = cursor.executed[0]
    assert "%s" in sql
    assert "orders'; DROP TABLE x --" not in sql  # never interpolated into the SQL text
    assert params == ("orders'; DROP TABLE x --",)  # passed as a bound parameter instead


def test_get_latest_backupset_sql_joins_backupmediafamily():
    """Regression guard: the LEFT JOIN to backupmediafamily (needed to
    resolve physical_device_name) must not be dropped in a future edit."""
    cursor = FakeCursor(fetchone_result=None)
    client = _PytdsSqlClient(FakeConnection(cursor))

    client.get_latest_backupset("orders")

    sql, _params = cursor.executed[0]
    assert "backupmediafamily" in sql
    assert "media_set_id" in sql
    assert "family_sequence_number" in sql


def test_get_latest_backupset_closes_cursor_even_on_execute_error():
    cursor = FakeCursor(execute_exc=pytds.Error("boom"))
    client = _PytdsSqlClient(FakeConnection(cursor))

    with pytest.raises(pytds.Error):
        client.get_latest_backupset("orders")

    assert cursor.closed is True


# ---------------------------------------------------------------------------
# _PytdsSqlClient.restore_verifyonly
# ---------------------------------------------------------------------------


def test_restore_verifyonly_success():
    cursor = FakeCursor(messages=[])
    client = _PytdsSqlClient(FakeConnectionWithTransport(cursor, FakeTransport()))

    result = client.restore_verifyonly(r"\\fileserver\backups$\nightly.bak", timeout_seconds=60)

    assert result.succeeded is True
    assert result.error_message is None
    assert result.error_number is None
    assert cursor.closed is True
    sql, _params = cursor.executed[0]
    assert "RESTORE VERIFYONLY FROM DISK" in sql
    assert r"\\fileserver\backups$\nightly.bak" in sql


def test_restore_verifyonly_collects_server_messages_on_success():
    class _FakeMsg:
        text = "Processed 100 pages"

    cursor = FakeCursor(messages=[(Exception, _FakeMsg())])
    client = _PytdsSqlClient(FakeConnectionWithTransport(cursor, FakeTransport()))

    result = client.restore_verifyonly("nightly.bak", timeout_seconds=60)
    assert result.output == "Processed 100 pages"


def test_restore_verifyonly_swallows_fetchall_error_not_a_failure():
    """RESTORE VERIFYONLY normally produces no result set; some pytds paths
    raise pytds.Error on fetchall() for a statement with no rows. This must
    NOT be classified as a verify failure."""
    cursor = FakeCursor(fetchall_exc=pytds.Error("no results"))
    client = _PytdsSqlClient(FakeConnectionWithTransport(cursor, FakeTransport()))

    result = client.restore_verifyonly("nightly.bak", timeout_seconds=60)
    assert result.succeeded is True


def test_restore_verifyonly_pytds_error_on_execute_returns_failure_result():
    exc = pytds.Error("Cannot open backup device")
    exc.number = 3201
    cursor = FakeCursor(execute_exc=exc)
    client = _PytdsSqlClient(FakeConnectionWithTransport(cursor, FakeTransport()))

    result = client.restore_verifyonly("nightly.bak", timeout_seconds=60)

    assert result.succeeded is False
    assert result.error_number == 3201
    assert "Cannot open backup device" in result.error_message
    assert cursor.closed is True  # cursor always closed, even on failure


def test_restore_verifyonly_invalid_disk_path_raises_before_touching_connection():
    conn = _RaisingCursorConnection()
    client = _PytdsSqlClient(conn)

    with pytest.raises(ValueError):
        client.restore_verifyonly("nightly.bak\n", timeout_seconds=60)


# ---------------------------------------------------------------------------
# _set_query_timeout fallback (WARNING logging, AttributeError + OSError)
# ---------------------------------------------------------------------------


def test_set_query_timeout_success_path_sets_socket_timeout():
    cursor = FakeCursor()
    transport = FakeTransport()
    client = _PytdsSqlClient(FakeConnectionWithTransport(cursor, transport))

    client.restore_verifyonly("nightly.bak", timeout_seconds=42)

    assert transport.timeout_set == 42


def test_set_query_timeout_missing_attribute_chain_logs_warning_and_continues(caplog):
    cursor = FakeCursor()
    # Plain FakeConnection has no `_tds_socket` attribute at all.
    client = _PytdsSqlClient(FakeConnection(cursor))

    with caplog.at_level(logging.WARNING, logger=sql_client_module.__name__):
        result = client.restore_verifyonly("nightly.bak", timeout_seconds=60)

    assert result.succeeded is True  # must not raise out of restore_verifyonly
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_set_query_timeout_oserror_from_settimeout_logs_warning_and_continues(caplog):
    cursor = FakeCursor()
    transport = FakeTransport(raise_oserror=True)
    client = _PytdsSqlClient(FakeConnectionWithTransport(cursor, transport))

    with caplog.at_level(logging.WARNING, logger=sql_client_module.__name__):
        result = client.restore_verifyonly("nightly.bak", timeout_seconds=60)

    assert result.succeeded is True  # must not raise out of restore_verifyonly
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------------
# default_sql_client_factory
# ---------------------------------------------------------------------------


def test_default_sql_client_factory_windows_auth_raises_before_connecting(monkeypatch):
    calls = []
    monkeypatch.setattr(sql_client_module.pytds, "connect", lambda **kwargs: calls.append(kwargs))

    params = SqlConnectionParams(
        host="10.0.0.5",
        port=None,
        instance_name=None,
        username=None,
        password=None,
        use_windows_auth=True,
        connect_timeout_seconds=30,
    )

    with pytest.raises(WindowsAuthNotSupportedError):
        default_sql_client_factory(params)

    assert calls == []  # pytds.connect must never have been attempted


def test_default_sql_client_factory_connects_and_wraps_client(monkeypatch):
    fake_connection = FakeConnection(FakeCursor())
    captured_kwargs = {}

    def fake_connect(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_connection

    monkeypatch.setattr(sql_client_module.pytds, "connect", fake_connect)

    params = SqlConnectionParams(
        host="10.0.0.5",
        port=1433,
        instance_name=None,
        username="sa",
        password="FAKE_PASSWORD_MARKER",
        use_windows_auth=False,
        connect_timeout_seconds=30,
    )

    client = default_sql_client_factory(params)

    assert isinstance(client, _PytdsSqlClient)
    assert captured_kwargs["dsn"] == "10.0.0.5"
    assert captured_kwargs["user"] == "sa"
    assert captured_kwargs["password"] == "FAKE_PASSWORD_MARKER"
    assert captured_kwargs["login_timeout"] == 30
    assert captured_kwargs["port"] == 1433
    assert captured_kwargs["autocommit"] is True


def test_default_sql_client_factory_named_instance_drops_port(monkeypatch):
    """pytds raises ValueError if both instance and port are given -- the
    factory must silently drop port whenever instance_name is set."""
    captured_kwargs = {}

    def fake_connect(**kwargs):
        captured_kwargs.update(kwargs)
        return FakeConnection(FakeCursor())

    monkeypatch.setattr(sql_client_module.pytds, "connect", fake_connect)

    params = SqlConnectionParams(
        host="10.0.0.5",
        port=1433,
        instance_name="SQLEXPRESS",
        username="sa",
        password="pw",
        use_windows_auth=False,
        connect_timeout_seconds=30,
    )

    default_sql_client_factory(params)

    assert captured_kwargs["dsn"] == "10.0.0.5\\SQLEXPRESS"
    assert "port" not in captured_kwargs


def test_default_sql_client_factory_propagates_connect_errors(monkeypatch):
    def fake_connect(**kwargs):
        raise ConnectionRefusedError("no route to host")

    monkeypatch.setattr(sql_client_module.pytds, "connect", fake_connect)

    params = SqlConnectionParams(
        host="10.0.0.5",
        port=None,
        instance_name=None,
        username="sa",
        password="pw",
        use_windows_auth=False,
        connect_timeout_seconds=30,
    )

    with pytest.raises(ConnectionRefusedError):
        default_sql_client_factory(params)
