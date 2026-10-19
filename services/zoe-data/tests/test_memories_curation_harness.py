"""CI wrapper: "Family memories" (caregiver curation, memories-space P7) on the phone / desktop Memories page
(``zoe-ui/dist/js/memories-curation.js``).

The node harness runs the REAL module against a DOM shim that refuses ``innerHTML`` and a fake server. Negative controls run the
same harness against mutated copies (never the working tree) and require it to go red, so a green run is not a harness that checks
nothing. Static checks pin that the routes the module calls are served by ``routers/memories_curation.py`` and that the module is
not precached (cache-busted by ``?v=``, no ``SW_VERSION`` bump).
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
MOD = DIST / "js" / "memories-curation.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the Memories harnesses")
        pytest.skip("Node.js is not installed on this host")
    return node


def _run(src: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if src is not None:
        env["CURATION_SRC"] = str(src)
    return subprocess.run([_node(), str(DIST / "test_memories_curation.js")], capture_output=True, text=True, timeout=60, env=env)


def test_curation_harness_passes():
    proc = _run()
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


@pytest.mark.parametrize("old,new", [
    ("if (text !== undefined && text !== null) n.textContent = String(text);",
     "if (text !== undefined && text !== null) n.innerHTML = String(text);"),                  # string-built DOM
    ("            state.me = null;                             // flag off (404) or signed out: nothing appears\n"
     "            return false;",
     "            state.me = { kind: 'adult', categories: [], on: [], editable: true };"),        # flag off still shows
    ("        acts.appendChild(button('Remove', 'am-danger', () => {\n            clear(acts);",
     "        acts.appendChild(button('Remove', 'am-danger', () => {\n            api('POST', path(m.user_id, '/rows/' + "
     "encodeURIComponent(r.id) + '/forget'));\n            clear(acts);"),                                    # remove before the yes
    ("            state.members = null;                        // not a curator (403): that part never appears",
     "            state.members = [{ user_id: 'kid/1', name: 'Mia' }];"),                         # family part for a non-curator
])
def test_curation_negative_controls(tmp_path, old, new):
    src = MOD.read_text()
    assert old in src, "the mutation anchor moved: update the control"
    bad = tmp_path / "memories-curation.js"
    bad.write_text(src.replace(old, new, 1))
    assert _run(bad).returncode != 0, "the harness stayed green against a broken module"


def test_every_route_the_page_calls_is_served():
    js = MOD.read_text()
    router = (SVC / "routers" / "memories_curation.py").read_text()
    for tail in ("/me", "/members", "/rows", "/edit", "/forget", "/sensitive/", "/held"):
        assert tail in js, tail
        assert tail in router, tail
    assert "'/api/memories/curation'" in js and 'prefix="/api/memories/curation"' in router


def test_not_precached():
    sw = (DIST / "sw.js").read_text()
    assert "memories-curation.js" not in sw
    assert re.search(r"/js/memories-curation\.js\?v=cu-\d+", (DIST / "memories.html").read_text())
