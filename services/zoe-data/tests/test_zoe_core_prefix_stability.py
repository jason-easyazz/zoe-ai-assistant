"""Pytest wrapper for zoe-core's KV-prefix stability suite (a node harness).

The assertions live in TypeScript because the things under test ARE TypeScript —
`services/zoe-core/extensions/memory.ts` (the system prompt must not move) and
`extensions/abilities.ts` (tool disclosure must be monotone). This wrapper exists
so that suite reaches a CI lane at all: `services/zoe-core/` is in no workflow,
while `services/zoe-data/tests` runs full-directory on the Jetson
(`.github/workflows/self-hosted-tests.yml`).

Deliberately NOT `ci_safe`: the GitHub lane is a slim python venv with no
guarantee of a Node new enough to execute `.ts` directly (type stripping is only
on by default from Node 22.18). It runs on the Jetson, which has 22.22.

Run it directly with either of:

    node --test services/zoe-core/test/prefix_stability.test.ts
    npm --prefix services/zoe-core test
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SUITE = _REPO / "services" / "zoe-core" / "test" / "prefix_stability.test.ts"

# Node's built-in TypeScript type stripping is unflagged from 22.18.0.
_MIN_NODE = (22, 18)


def tap_summary(stdout: str) -> "tuple[int | None, int | None]":
    """(pass, fail) counts from a TAP summary, or (None, None) if not TAP.

    Anchored on the counts rather than on a formatted line, so it cannot be
    satisfied by a reporter that happens to print similar-looking text.
    """
    passed = re.search(r"^# pass (\d+)$", stdout, re.MULTILINE)
    failed = re.search(r"^# fail (\d+)$", stdout, re.MULTILINE)
    if not passed or not failed:
        return None, None
    return int(passed.group(1)), int(failed.group(1))


def _node_version() -> "tuple[int, int] | None":
    node = shutil.which("node")
    if node is None:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    parts = out.stdout.strip().lstrip("v").split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


def test_zoe_core_prefix_stability_suite():
    version = _node_version()
    if version is None:
        pytest.skip("node not on PATH")
    if version < _MIN_NODE:
        pytest.skip(f"node {version} < {_MIN_NODE} — no built-in TypeScript type stripping")
    assert _SUITE.is_file(), f"missing zoe-core prefix suite at {_SUITE}"

    # Pin the reporter. `node --test` picks its DEFAULT reporter otherwise, and the
    # default is not stable across versions: Node 22 emits TAP (`# fail 0`) while
    # Node 24 emits the spec reporter (`ℹ fail 0`), so parsing the default output
    # made any newer runner false-red on a green suite.
    result = subprocess.run(
        ["node", "--test", "--test-reporter=tap", str(_SUITE)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(_SUITE.parent),
    )
    assert result.returncode == 0, (
        "zoe-core KV-prefix stability suite failed:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    # A node --test run that collects nothing exits 0. Pin that it actually ran.
    passed, failed = tap_summary(result.stdout)
    assert (passed, failed) != (None, None), (
        f"unrecognised node --test TAP summary:\n{result.stdout}"
    )
    assert failed == 0, f"node reported failures:\n{result.stdout}"
    assert passed > 0, "the node suite collected no tests — vacuous pass"


def test_tap_summary_parses_the_pinned_reporter_format():
    """Guards the parser itself, including the Node-24 shape that broke it.

    Node's DEFAULT reporter is version-dependent — 22 emits TAP, 24 emits the spec
    reporter (`ℹ fail 0`) — so the old `"# fail 0" in stdout` substring check went
    false-red on a green suite under a newer runtime. We pin `--test-reporter=tap`
    AND parse the counts, so the spec shape is now recognised as unparseable
    (loud) rather than silently read as a failure.
    """
    tap = "TAP version 13\nok 1 - t\n1..1\n# tests 1\n# pass 11\n# fail 0\n"
    assert tap_summary(tap) == (11, 0)
    assert tap_summary("# tests 3\n# pass 1\n# fail 2\n") == (1, 2)
    # The Node 24 spec-reporter shape: NOT silently treated as a pass or a fail.
    spec = "ℹ tests 11\nℹ pass 11\nℹ fail 0\n"
    assert tap_summary(spec) == (None, None)
    assert tap_summary("") == (None, None)
