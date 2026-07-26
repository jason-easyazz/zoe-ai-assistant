"""CI wrapper: stored-XSS in the desktop notes and memories renderers.

notes.html embedded whole note objects in a single-quoted inline handler
(``onclick='selectNote(${JSON.stringify(note)})'``) and built its tag chips as
``onclick="removeTag('${escHtml(t)}')"``. Neither is safe: ``JSON.stringify``
does not escape ``'``, and the page's own ``escHtml`` escaped ``& < > "`` but
NOT ``'`` either -- so a note title of ``x' onmouseover='alert(1)`` closed the
attribute and ran. Notes are family-visible across users, making this a
cross-user stored XSS. The same renderer also interpolated an unvalidated
colour into ``style="border-left-color:${color}"``.

memories.html injected memory content, collection names and notification text
straight into ``innerHTML`` and defined no escape helper at all. Memory content
comes from conversations and ingest, so it is attacker-influenceable.

The fix is DOM construction (createElement + textContent + addEventListener)
rather than harder escaping: embedding data in an ``on*`` attribute is the root
cause, and escaping only moves the goalposts.

Validated against the REAL pre-fix files from origin/main (copied to a scratch
tree, never overwriting the working tree): the harness fails 13 of its 16
checks there, including every behavioural XSS check, so this is not a synthetic
guard.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "zoe-ui" / "dist"
HARNESS = DIST / "test_notes_memories_xss.js"


def _strip_js_comments(src: str) -> str:
    """Drop // and /* */ comments so explanatory prose cannot satisfy a match."""
    out = []
    i, n = 0, len(src)
    in_block = False
    while i < n:
        if in_block:
            if src.startswith("*/", i):
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if src.startswith("/*", i):
            in_block = True
            i += 2
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def test_notes_memories_xss_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        # A silent skip on CI means the XSS guard quietly stops running while
        # the build still goes green. Skip is acceptable on a dev box; on CI it
        # is a failure.
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the notes/memories XSS harness")
        pytest.skip("Node.js is not installed on this host")
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    try:
        # The harness executes extracted renderers in a vm. Bound it so a
        # runaway loop fails here with a useful message instead of burning the
        # whole CI job. It normally runs in well under a second.
        proc = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"notes/memories XSS harness did not finish within 60s: {HARNESS}")
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_no_data_interpolated_into_inline_handlers():
    """The root cause, asserted independently of node.

    Data inside an on* attribute is a template-injection sink no matter how it
    is escaped, so assert the pattern is simply absent from both pages.
    """
    import re

    pattern = re.compile(r"""\son[a-z]+\s*=\s*(['"])[^'"]*\$\{[^'"]*\1""", re.I)
    for name in ("notes.html", "memories.html"):
        code = _strip_js_comments((DIST / name).read_text(encoding="utf-8"))
        match = pattern.search(code)
        assert not match, (
            f"{name} interpolates data into an inline handler: {match.group(0)!r}"
        )


def test_notes_page_has_no_single_quote_blind_escaper():
    """notes.html's escHtml did not escape ', which is what made it exploitable."""
    code = _strip_js_comments((DIST / "notes.html").read_text(encoding="utf-8"))
    assert "escHtml" not in code, (
        "notes.html still references escHtml; it escapes & < > \" but not ', "
        "so it cannot protect a single-quoted context"
    )
    assert "JSON.stringify(note)" not in code, (
        "notes.html still serialises a note object into markup"
    )


def test_escape_helpers_are_reachable_on_both_pages():
    """Guard against shipping a ReferenceError instead of a fix.

    Both pages load js/common.js (which exports zoeEscapeHtml), but the fix
    deliberately relies on local DOM-building helpers so it holds even if the
    shared script fails to load. Assert both facts.
    """
    for name, helper in (("notes.html", "function makeDiv("), ("memories.html", "function memDiv(")):
        raw = (DIST / name).read_text(encoding="utf-8")
        assert "js/common.js" in raw, f"{name} no longer loads common.js"
        assert helper in _strip_js_comments(raw), f"{name} lost its DOM-building helper {helper!r}"


def test_pages_are_not_precached_by_service_worker():
    """Pin the SW_VERSION decision.

    Neither page is in the sw.js precache list, so no version bump was needed.
    If either is added later, the stale-cache question has to be revisited --
    this test goes red to force that.
    """
    sw = (DIST / "sw.js").read_text(encoding="utf-8")
    precache = sw.split("precacheAndRoute", 1)
    assert len(precache) > 1, "sw.js no longer has a precache list; revisit this guard"
    block = precache[1][:2000]
    for name in ("notes.html", "memories.html"):
        assert name not in block, (
            f"{name} is now precached by sw.js -- bump SW_VERSION so the fixed "
            "page actually reaches clients"
        )
