"""CI wrapper: stored-XSS escaping in the LIVE journal renderers.

js/journal-api.js interpolated entry titles, content previews, tag/people names
and photo URLs straight into innerHTML. PR #895 escaped an inline renderer in
journal.html, but that renderer (loadJournalEntriesInline) has zero callers --
it is dead code, which is why the defect stayed open on the live path.

Validated against the real pre-fix file from origin/main: the harness fails 10
of its 13 checks there, including every behavioural XSS check, so this is not a
synthetic guard.

The escape helper must live INSIDE journal-api.js. That file is loaded by both
zoe-ui/dist/journal.html and zoe-ui/dist/touch/journal.html, and only the former
defines a page-level escapeHtml(); borrowing it would throw ReferenceError on
the touch panel and break journal rendering on the live kiosk. The harness
asserts the helper is reachable from journal-api.js's own scope.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_journal_render_escaping.js"


def test_journal_render_escaping_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        # A silent skip on CI means the XSS guard quietly stops running while
        # the build still goes green. Skip is acceptable on a dev box; on CI it
        # is a failure.
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the journal XSS harness")
        pytest.skip("Node.js is not installed on this host")
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    try:
        # The harness executes journal-api.js in a vm. Bound it so a runaway
        # loop fails here with a useful message instead of burning the whole CI
        # job against the runner-level timeout. It normally runs in well under
        # a second.
        proc = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"journal XSS harness did not finish within 60s: {HARNESS}")
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_escape_helper_is_local_to_journal_api():
    """The kiosk ReferenceError guard, asserted independently of node.

    touch/journal.html defines no escapeHtml(), so journal-api.js must not
    reference one.
    """
    src = (ROOT / "zoe-ui" / "dist" / "js" / "journal-api.js").read_text(encoding="utf-8")
    # Strip comments so this file's own explanatory prose cannot satisfy the match.
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )
    assert "function escapeJournalHtml(" in code, (
        "journal-api.js must define its own escape helper"
    )

    touch = (ROOT / "zoe-ui" / "dist" / "touch" / "journal.html").read_text(encoding="utf-8")
    assert "journal-api.js" in touch, "touch/journal.html is a consumer of journal-api.js"
    assert "function escapeHtml(" not in touch, (
        "touch/journal.html defines no escapeHtml -- this is the ReferenceError risk "
        "that forces the helper to be local"
    )


def test_dead_inline_renderer_is_not_the_live_path():
    """Pin the finding that made #895 miss: the escaped renderer is dead code.

    If loadJournalEntriesInline ever gains a caller, this test goes red so the
    escaping story gets re-examined rather than silently diverging.
    """
    dist = ROOT / "zoe-ui" / "dist"
    callers = []
    for path in list(dist.glob("*.html")) + list(dist.glob("js/*.js")) + list(
        dist.glob("touch/*.html")
    ):
        text = path.read_text(encoding="utf-8", errors="ignore")
        # A definition is not a call.
        hits = text.count("loadJournalEntriesInline")
        defs = text.count("function loadJournalEntriesInline")
        if hits - defs > 0:
            callers.append(path.name)
    assert not callers, (
        f"loadJournalEntriesInline gained caller(s) {callers}; it was dead code, which "
        "is why the live renderer in journal-api.js went unescaped"
    )
