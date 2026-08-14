"""Static content-assertion test for docker-entrypoint.sh.

Deliberately does NOT spin up a real uvicorn process or test actual
keep-alive timing behavior: that would be flaky, slow, and would mostly
test uvicorn's own already-tested behavior rather than this codebase.
Instead, this just guards against someone silently dropping one of the
uvicorn flags in a future edit to the entrypoint script.
"""
from __future__ import annotations

import re
from pathlib import Path

ENTRYPOINT_PATH = Path(__file__).resolve().parent.parent / "docker-entrypoint.sh"


def _find_exec_uvicorn_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("exec") and "uvicorn" in stripped:
            return stripped
    raise AssertionError("no 'exec ... uvicorn' line found in docker-entrypoint.sh")


def test_uvicorn_exec_line_has_limit_concurrency_and_keep_alive_flags():
    content = ENTRYPOINT_PATH.read_text()
    exec_line = _find_exec_uvicorn_line(content)

    assert re.search(r"--limit-concurrency\s+200\b", exec_line), (
        f"expected '--limit-concurrency 200' in exec line, got: {exec_line!r}"
    )
    assert re.search(r"--timeout-keep-alive\s+90\b", exec_line), (
        f"expected '--timeout-keep-alive 90' in exec line, got: {exec_line!r}"
    )
