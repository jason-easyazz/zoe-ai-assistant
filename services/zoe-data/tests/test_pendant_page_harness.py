"""CI wrapper: the desktop/phone Pendant page (``zoe-ui/dist/pendant.html`` + ``js/pendant-page.js``).

The node harness runs the REAL module against a DOM shim that refuses ``innerHTML`` and a fake server, and asserts the exact
request each button sends. Negative controls run the same harness against mutated copies of the module (never the working
tree) and require it to go red, so a green run is not a harness that checks nothing. Static checks pin that every ``/api/omi``
literal the page calls is a route the Omi routers actually serve, that the page is gated by ``auth.js`` and not precached by
the service worker (no ``SW_VERSION`` bump needed), and that the desktop Memories page links it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
DIST = SVC.parent / "zoe-ui" / "dist"
HARNESS = DIST / "test_pendant_page.js"
MODULE = DIST / "js" / "pendant-page.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the Pendant page harness")
        pytest.skip("Node.js is not installed on this host")
    return node


def _run(src: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if src is not None:
        env["PENDANT_SRC"] = str(src)
    return subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=60, env=env)


def test_pendant_page_harness_passes():
    proc = _run()
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


@pytest.mark.parametrize("old,new", [
    ("if (text !== undefined && text !== null) n.textContent = String(text);",
     "if (text !== undefined && text !== null) n.innerHTML = String(text);"),                 # string-built DOM
    ("'/me'", "'/tag'"),                                                                      # "That's me" names a person
    ("done(label('you'))", "done(label('other'))"),                                         # "Me" says not me
    ("onDone(exact.length === 1 ? { person_id: exact[0].id } : { name });", "onDone({ name });"),  # a duplicate person per tag
    ("root.fetch('/api/omi/segments/' + encodeURIComponent(segmentId) + '/clip'",
     "root.fetch('/api/omi/recordings/' + encodeURIComponent(segmentId) + '/clip'"),        # plays the wrong thing
    ("const unmark = () => (seg.not_person ?", "const unmark = () => (false ?"),            # a mistaken TV mark cannot be undone
])
def test_negative_controls_turn_the_harness_red(tmp_path, old, new):
    src = MODULE.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"mutation anchor drifted: {old!r}"
    mutated = tmp_path / "pendant-page.js"
    mutated.write_text(src.replace(old, new), encoding="utf-8")
    proc = _run(mutated)
    assert proc.returncode != 0, f"the harness stayed green on a mutation: {new!r}\n{proc.stdout}"


def test_every_api_literal_on_the_page_is_a_served_omi_route(monkeypatch):
    from fastapi import FastAPI
    from routers import omi_recordings as omi_router

    monkeypatch.setenv("ZOE_OMI_SYNC_ENABLED", "1")
    app = FastAPI()
    assert omi_router.register(app) is True
    routes = [(m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops]     # survives nested included routers
    pats = [(m, re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(p)) + "$")) for m, p in routes]
    src = MODULE.read_text(encoding="utf-8")
    calls = re.findall(r"api\('(GET|POST|PATCH|PUT|DELETE)', '(/api/[^']+)'(?: \+ [^',+ ]+(?: \+ '([^']*)')?)?", src)
    assert len(calls) >= 12, calls
    for method, base, tail in calls:
        path = base.split("?")[0]
        if path.endswith("/"):
            path += "X" + tail
        assert any(m == method and p.match(path) for m, p in pats), f"{method} {path} is not served by the Omi routers"
    assert any(m == "GET" and p.match("/api/omi/segments/X/clip") for m, p in pats)


def test_page_is_gated_linked_and_not_precached():
    html = (DIST / "pendant.html").read_text(encoding="utf-8")
    assert '<script src="/js/auth.js"></script>' in html and "/js/pendant-page.js?v=" in html
    sw = (DIST / "sw.js").read_text(encoding="utf-8")
    block = sw.split("precacheAndRoute", 1)[1][:2000]
    assert "pendant" not in block, "pendant.html / pendant-page.js are precached now: bump SW_VERSION and the precache digest"
    mem = (DIST / "memories.html").read_text(encoding="utf-8")
    assert 'href="pendant.html"' in mem and "navigateToPage('pendant.html')" in mem
