"""Unit tests for app.core.remote_paths.resolve_remote_directory (pure
function, no DB/session involved).

Covers the worked example from the spec, the override escape hatch, every
BackupType's directory code, sanitization of unsafe characters, and --
critically -- the path-traversal security regression the reviewer found and
the coder fixed (a server/job name consisting entirely of dots, e.g. "..",
must never survive into the computed path literally).
"""
from __future__ import annotations

import pytest

from app.core.remote_paths import resolve_remote_directory
from app.models.enums import BackupType


def test_worked_example_no_override():
    result = resolve_remote_directory(
        "trz1c8.rcku.net", "Nightly AdventureWorks Diff", 42, BackupType.DIFFERENTIAL, None
    )
    assert result == "trz1c8.rcku.net/Nightly_AdventureWorks_Diff_42/DIFF/"


def test_override_returned_literally_ignoring_other_args():
    result = resolve_remote_directory(
        "irrelevant-server",
        "irrelevant-job",
        999,
        BackupType.CUSTOM,
        "some/hand/picked/path",
    )
    assert result == "some/hand/picked/path"


def test_override_returned_literally_even_if_it_looks_unsafe():
    """The function itself does not re-validate the override -- validation
    is the schema layer's job (app.schemas.backup_job._check_remote_directory_override).
    resolve_remote_directory must return it verbatim, not silently mangle it."""
    result = resolve_remote_directory("s", "j", 1, BackupType.FULL, "  weird/path  ")
    assert result == "  weird/path  "


@pytest.mark.parametrize(
    ("backup_type", "expected_code"),
    [
        (BackupType.FULL, "FULL"),
        (BackupType.DIFFERENTIAL, "DIFF"),
        (BackupType.TRANSACTION_LOG, "TLOG"),
        (BackupType.CUSTOM, "CUSTOM"),
    ],
)
def test_backup_type_directory_codes(backup_type, expected_code):
    result = resolve_remote_directory("server", "job", 1, backup_type, None)
    assert result == f"server/job_1/{expected_code}/"


def test_sanitizes_spaces_slashes_and_special_characters():
    result = resolve_remote_directory(
        "a/b c!", "j/k l!", 7, BackupType.CUSTOM, None
    )
    assert result == "a_b_c_/j_k_l__7/CUSTOM/"
    # No literal path separator survived from within either name.
    server_segment, job_segment, _type, _trailing = result.split("/")
    assert "/" not in server_segment
    assert "/" not in job_segment


def test_ordinary_dots_in_names_are_preserved():
    """Dots are in the allowed character set -- an ordinary display name
    like a version-numbered hostname must pass through unchanged."""
    result = resolve_remote_directory("trz1c8.rcku.net", "v1.2-nightly", 3, BackupType.FULL, None)
    assert result == "trz1c8.rcku.net/v1.2-nightly_3/FULL/"


# ---------------------------------------------------------------------------
# Security regression: Server.name (or job name) == ".." must never produce
# a literal ".." path-traversal segment in the computed remote directory.
# ---------------------------------------------------------------------------


def test_security_regression_all_dot_server_and_job_names_do_not_leak_dotdot():
    result = resolve_remote_directory("..", "..", 1, BackupType.FULL, None)

    segments = [s for s in result.split("/") if s]
    assert ".." not in segments
    assert "." not in segments
    assert result == "_unnamed_server/_unnamed_job_1/FULL/"


@pytest.mark.parametrize("raw", ["..", ".", "...", "...."])
def test_security_regression_all_dots_server_name_falls_back(raw):
    result = resolve_remote_directory(raw, "job", 10, BackupType.FULL, None)
    assert result.startswith("_unnamed_server/")
    assert raw not in result.split("/")


@pytest.mark.parametrize("raw", ["..", ".", "...", "...."])
def test_security_regression_all_dots_job_name_falls_back(raw):
    result = resolve_remote_directory("server", raw, 10, BackupType.FULL, None)
    # job segment is "_unnamed_job_10", never "<raw>_10" literally containing
    # a dot-only segment.
    assert result == "server/_unnamed_job_10/FULL/"


def test_empty_server_name_falls_back():
    result = resolve_remote_directory("", "job", 5, BackupType.FULL, None)
    assert result == "_unnamed_server/job_5/FULL/"


def test_all_whitespace_server_name_falls_back():
    result = resolve_remote_directory("   ", "job", 5, BackupType.FULL, None)
    assert result == "_unnamed_server/job_5/FULL/"


def test_empty_job_name_falls_back():
    result = resolve_remote_directory("server", "", 5, BackupType.FULL, None)
    assert result == "server/_unnamed_job_5/FULL/"


def test_job_id_guarantees_uniqueness_for_colliding_sanitized_job_names():
    """Two different job_ids sharing the same (sanitized) job_name must
    resolve to distinct directories -- collision is impossible by
    construction because job_id is always appended."""
    a = resolve_remote_directory("server", "Nightly Backup", 1, BackupType.FULL, None)
    b = resolve_remote_directory("server", "Nightly/Backup", 2, BackupType.FULL, None)
    # Both job names sanitize to the same string ("Nightly_Backup") but the
    # appended job_id keeps them distinct.
    assert a != b
    assert a == "server/Nightly_Backup_1/FULL/"
    assert b == "server/Nightly_Backup_2/FULL/"
