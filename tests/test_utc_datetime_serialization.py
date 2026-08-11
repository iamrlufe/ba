"""Regression tests for the UTC-suffix datetime serialization bug fix.

Guards against:
  - someone reverting/breaking `app.schemas.common.UtcDatetime` /
    `_ensure_utc` at the unit level, and
  - someone wiring a new (or un-wiring an existing) DB-sourced datetime
    field back to a plain `datetime` annotation, which would silently
    reintroduce bare (no "Z"/"+00:00" suffix) timestamps in API responses
    that the frontend misinterprets as browser-local time.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timezone

from pydantic import BaseModel

from app.schemas.common import UtcDatetime, _ensure_utc

# Matches a bare ISO-8601 datetime with NO UTC offset/Z suffix, e.g.
# "2026-08-11T10:15:48.288917" (the exact shape of the original bug).
_NO_OFFSET_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


class _Wrapper(BaseModel):
    value: UtcDatetime


# --------------------------------------------------------------------------
# Unit-level: UtcDatetime / _ensure_utc directly
# --------------------------------------------------------------------------


def test_naive_datetime_gets_utc_suffix_on_json_serialization():
    naive = datetime(2026, 8, 11, 10, 15, 48, 288917)
    assert naive.tzinfo is None  # sanity: this is the exact shape the bug produced

    model = _Wrapper(value=naive)
    dumped = model.model_dump(mode="json")

    assert not _NO_OFFSET_RE.match(dumped["value"]), (
        f"expected a UTC-suffixed timestamp, got bare ISO string: {dumped['value']!r}"
    )
    assert dumped["value"].endswith("Z") or dumped["value"].endswith("+00:00")

    # Round-trip through actual JSON text too (model_dump_json), since that's
    # what actually goes over the wire in an HTTP response body.
    json_text = model.model_dump_json()
    assert '"value":"2026-08-11T10:15:48.288917' in json_text
    assert json_text.rstrip("}").endswith('Z"') or json_text.rstrip("}").endswith('+00:00"')


def test_already_aware_utc_datetime_is_not_double_encoded():
    aware = datetime(2026, 8, 11, 10, 15, 48, tzinfo=UTC)
    model = _Wrapper(value=aware)
    dumped = model.model_dump(mode="json")

    assert dumped["value"] == "2026-08-11T10:15:48Z" or dumped["value"] == "2026-08-11T10:15:48+00:00"
    # No double timezone marker like "+00:00+00:00" or "ZZ".
    assert "+00:00+00:00" not in dumped["value"]
    assert not dumped["value"].endswith("ZZ")


def test_aware_non_utc_datetime_is_normalized_to_utc():
    # +05:00 -> should be converted to the equivalent UTC instant, not just
    # relabeled.
    from datetime import timedelta

    aware = datetime(2026, 8, 11, 15, 15, 48, tzinfo=timezone(timedelta(hours=5)))
    result = _ensure_utc(aware)

    assert result.tzinfo == UTC
    assert result.hour == 10  # 15:15 +05:00 == 10:15 UTC
    assert result.minute == 15


def test_ensure_utc_naive_input_is_labeled_utc_without_shifting_clock_value():
    naive = datetime(2026, 8, 11, 10, 15, 48)
    result = _ensure_utc(naive)

    assert result.tzinfo == UTC
    assert result.hour == 10  # value unchanged, only tzinfo attached
    assert result.minute == 15


# --------------------------------------------------------------------------
# Integration-level: a real authenticated endpoint returning a schema that
# uses UtcDatetime (ServerRead.created_at / updated_at), asserting the raw
# JSON response body -- not just the parsed/re-typed value -- carries a UTC
# suffix. This catches the case where the fix works at the type level but
# somehow isn't exercised through the actual FastAPI response path (e.g. a
# response_model override, manual dict return, or serialization mode
# mismatch).
# --------------------------------------------------------------------------


async def test_create_server_endpoint_response_has_utc_suffixed_timestamps(admin_client):
    resp = await admin_client.post(
        "/api/servers",
        json={
            "name": "utc-suffix-check",
            "host": "10.0.0.50",
            "port": 21,
            "protocol": "FTP",
        },
    )
    assert resp.status_code == 201

    # Check the raw response text, not just the parsed dict, since the bug
    # was specifically about what bytes hit the wire.
    raw_text = resp.text
    body = resp.json()

    for field in ("created_at", "updated_at"):
        value = body[field]
        assert not _NO_OFFSET_RE.match(value), (
            f"{field} is a bare ISO datetime with no UTC suffix: {value!r}"
        )
        assert value.endswith("Z") or value.endswith("+00:00"), (
            f"{field} does not end in a UTC offset marker: {value!r}"
        )
        # And confirm that exact (still-suffixed) substring is really what
        # was sent over the wire, not something httpx reconstructed.
        assert value in raw_text
