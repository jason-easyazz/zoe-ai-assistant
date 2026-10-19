"""CI wrapper: "What Zoe knows about you" on the desktop / phone Memories page (``zoe-ui/dist/js/memories-about-me.js``) and the
memories card's use of the shared panel review-card renderer (``js/touch-review-cards.js``).

The node harnesses run the REAL modules against DOM shims that refuse ``innerHTML`` and fake servers. Negative controls run the
same harness against mutated copies (never the working tree) and require it to go red, so a green run is not a harness that
checks nothing. Static checks pin that every ``/api/memories/mine`` literal the page calls is a route the memories router serves,
and that neither page nor module is precached (no ``SW_VERSION`` bump; both are cache-busted by ``?v=``).
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
ABOUT = DIST / "js" / "memories-about-me.js"
CARDS = DIST / "js" / "touch-review-cards.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the Memories harnesses")
        pytest.skip("Node.js is not installed on this host")
    return node


def _run(harness: str, env_key: str = "", src: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if src is not None:
        env[env_key] = str(src)
    return subprocess.run([_node(), str(DIST / harness)], capture_output=True, text=True, timeout=60, env=env)


def test_about_me_harness_passes():
    proc = _run("test_memories_about_me.js")
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


@pytest.mark.parametrize("old,new", [
    ("if (text !== undefined && text !== null) n.textContent = String(text);",
     "if (text !== undefined && text !== null) n.innerHTML = String(text);"),                   # string-built DOM
    ("            state.actions = null;                       // flag off (404) or signed out: the section never appears\n"
     "            return false;",
     "            state.actions = { correct: true };"),                                            # flag off still shows the list
    ("    function forgetPane(it, row, pane, note) {\n        clear(pane);",
     "    function forgetPane(it, row, pane, note) {\n        api('POST', '/api/memories/mine/' + encodeURIComponent(it.id)"
     " + '/forget');\n        clear(pane);"),                                                     # forget before the confirm
    ("            if (!text) { note.textContent = 'Type what’s right first.'; return; }", ""),     # an empty fix is sent
    ("        if (nav) nav.hidden = false;\n", ""),                                                 # phone menu link never appears
    ("        if (!state.section) return false;\n        try {",
     "        if (!state.section) return false;\n        { const n0 = state.doc.getElementById('aboutMeNav'); if (n0) n0.hidden = false; }\n        try {"),
                                                                                                  # menu link shown while the flag is off
])
def test_about_me_negative_controls(tmp_path, old, new):
    src = ABOUT.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"mutation anchor drifted: {old!r}"
    mutated = tmp_path / "memories-about-me.js"
    mutated.write_text(src.replace(old, new), encoding="utf-8")
    proc = _run("test_memories_about_me.js", "ABOUT_ME_SRC", mutated)
    assert proc.returncode != 0, f"the harness stayed green on a mutation: {new!r}\n{proc.stdout}"


@pytest.mark.parametrize("old,new", [
    ("                        if (j && j.keep) {", "                        if (false) {"),                 # a kept row vanishes
    ("var base = (typeof d.endpoint === 'string' && /^\\/api\\/panel\\/[a-z]+$/.test(d.endpoint)) ? d.endpoint : '/api/panel/cards';",
     "var base = d.endpoint || '/api/panel/cards';"),                                                       # taps leave the panel routes
    ("                if (r.status === 403 && detail === 'pin_needed') say(PIN_FIRST);", ""),               # PIN-first is not said
])
def test_review_cards_negative_controls(tmp_path, old, new):
    src = CARDS.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"mutation anchor drifted: {old!r}"
    mutated = tmp_path / "touch-review-cards.js"
    mutated.write_text(src.replace(old, new), encoding="utf-8")
    proc = _run("test_touch_review_cards.js", "RC_SRC", mutated)
    assert proc.returncode != 0, f"the harness stayed green on a mutation: {new!r}\n{proc.stdout}"


def test_every_api_literal_is_a_served_memories_route():
    from fastapi import FastAPI
    from routers.memories import router

    app = FastAPI()
    app.include_router(router)
    routes = [(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops]
    pats = [(m, re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(p)) + "$")) for m, p in routes]
    src = ABOUT.read_text(encoding="utf-8")
    calls = re.findall(r"api\('(GET|POST)', '(/api/[^']+)'(?: \+ encodeURIComponent\([^)]*\) \+ '([^']*)')?", src)
    assert len(calls) >= 4, calls
    assert {(m, b + t) for m, b, t in calls} >= {("GET", "/api/memories/mine/actions"), ("GET", "/api/memories/mine"),
                                                ("POST", "/api/memories/mine//correct"), ("POST", "/api/memories/mine//forget")}
    for method, base, tail in calls:
        path = base + ("X" + tail if base.endswith("/") else "")
        assert any(m == method and p.match(path) for m, p in pats), f"{method} {path} is not served by routers/memories.py"


def test_nothing_new_is_precached():
    sw = (DIST / "sw.js").read_text(encoding="utf-8")
    block = sw.split("precacheAndRoute([", 1)[1].split("]);", 1)[0]
    assert "/index.html" in block, "the precache list was not found"
    for name in ("memories", "about-me", "touch-review-cards", "touch/home"):
        assert name not in block, f"{name} is precached now: bump SW_VERSION and the precache digest"
    home = (DIST / "touch" / "home.html").read_text(encoding="utf-8")
    assert "/js/touch-review-cards.js?v=rc-2" in home, "the renderer changed: its ?v= must change with it"
