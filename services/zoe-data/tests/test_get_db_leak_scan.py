"""No `return`/`break` inside ``async for db in get_db():`` — the #953 leak.

Exiting the get_db() generator early leaks the pooled asyncpg connection
(pool max_size=10). This pattern drained the whole pool on 2026-07-03 and took
every DB-backed endpoint down. Files are added here as they are cleaned
(sweep of ~38 legacy sites); use ``async with get_db_ctx() as db:`` instead.

main.py is pinned separately by tests/test_ws_guard_invariants.py (PR #978);
once the sweep completes the two scans can be consolidated.
"""
import os
import re

import pytest

# Slim-dep-green (pure source scan): runs in the ci_safe lane on GitHub-hosted
# CI (marker-based selection, no validate.yml enumeration — see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cleaned files — append per sweep PR. intent_router.py joins when its
# conversion lands.
CLEANED_FILES = [
    "routers/chat.py",
    "chat_stream_protocol.py",  # W4-C2: protocol mechanics moved out of chat.py — keep scanned
    "routers/dashboard.py",
    "routers/panel_auth.py",
    "routers/system.py",
    "routers/voice_tts.py",
    "system_updates.py",
    "intent_router.py",
]


def _early_exit_sites(path: str) -> list[int]:
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    inside_indent = None
    offenders: list[int] = []
    for i, line in enumerate(lines, 1):
        if "async for db in get_db()" in line:
            inside_indent = len(line) - len(line.lstrip())
            continue
        if inside_indent is not None:
            cur = len(line) - len(line.lstrip())
            if line.strip() and cur <= inside_indent:
                inside_indent = None
                continue
            if re.match(r"\s*(return\b|break\b)", line):
                offenders.append(i)
    return offenders


@pytest.mark.parametrize("relpath", CLEANED_FILES)
def test_no_early_exit_inside_get_db(relpath):
    path = os.path.join(DATA, relpath)
    offenders = _early_exit_sites(path)
    assert not offenders, (
        f"{relpath} lines {offenders}: return/break inside `async for db in "
        f"get_db()` leaks the pooled connection (#953, 2026-07-03 outage) — "
        f"use `async with get_db_ctx() as db:`"
    )
