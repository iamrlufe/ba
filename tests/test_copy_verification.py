"""Unit tests for `app.workers.copy_verification.map_agent_copy_status` --
the pure status-mapping table backing POST
/api/backup-records/{id}/report-copy-verification. HTTP-layer behavior
(alert raise/dedup/resolve, insert semantics) is covered separately in
tests/test_routers_copy_verification.py.
"""
from __future__ import annotations

import pytest

from app.models.enums import VerificationRunStatus
from app.schemas.copy_verification import AgentCopyVerificationStatus
from app.workers.copy_verification import map_agent_copy_status


@pytest.mark.parametrize(
    "agent_status,expected",
    [
        (AgentCopyVerificationStatus.OK, VerificationRunStatus.OK),
        (AgentCopyVerificationStatus.MISMATCH, VerificationRunStatus.CORRUPT),
        (AgentCopyVerificationStatus.MISSING_SIDECAR, VerificationRunStatus.MISSING),
        (AgentCopyVerificationStatus.FILE_UNREADABLE, VerificationRunStatus.ERROR),
    ],
)
def test_map_agent_copy_status(agent_status, expected):
    assert map_agent_copy_status(agent_status) == expected


def test_map_agent_copy_status_covers_every_agent_status():
    """Regression guard: every AgentCopyVerificationStatus member must have
    an explicit mapping -- a missing entry would raise KeyError at request
    time instead of failing this fast, cheap unit test."""
    for status in AgentCopyVerificationStatus:
        # Must not raise.
        map_agent_copy_status(status)
