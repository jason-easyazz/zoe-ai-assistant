"""CI wrapper: run the panel alarms node harness (ZOE_ALARMS; dist/test_touch_alarms.js).

Pins, against the REAL js/touch-alarms.js (no browser, no network): a ring takes the sleep page, tap ANYWHERE snoozes, Turn off is
press-and-hold 1 s (a tap or an early release neither snoozes nor turns off), no snoozes left = tap does nothing, a snooze that
could not reach the server is retried and not re-rung, a ring the server stops listing is removed, a 404 hides the tabs, the
editor saves the right body, and no server string is ever parsed as HTML.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]


def test_touch_alarms_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_alarms.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


# The alarm ring push (ZOE_ALARMS_RINGING_PUSH): one negative control per panel rule. Each mutation of the REAL js/touch-alarms.js
# must turn the named harness check red, so the checks above cannot pass vacuously.
PUSH_CONTROLS = [
    ("if (msg.type === 'alarms_ringing_changed') { p.cap = true; p.seq = seq; pollNow(); return; }",
     "if (msg.type === 'alarms_ringing_changed') { p.cap = true; p.seq = seq; return; }", "a change push reads at once"),
    ("if (st.enabled !== false) pollNow();\n            return;", "return;", "a reconnect (hello) reads at once"),
    ("if (p.seq !== null && seq !== p.seq) pollNow();", "", "a missed push"),
    ("return st.push.cap && Date.now() - st.push.rxAt <= BEAT_STALE_MS;", "return st.push.cap;", "a silent socket is caught"),
    ("p.cap = typeof msg.alarms_ringing_seq === 'number';", "p.cap = true;", "beats after a hello without the capability"),
    ("HEALTHY_HOLD_MS = 30000", "HEALTHY_HOLD_MS = 0", "proven for under 30 s"),
    ("SLOW_POLL_MS = 20000", "SLOW_POLL_MS = 2000", "the poll slows to 20 s"),
    ("hideTabs(); schedule(OFF_RECHECK_MS); } else schedule(POLL_MS);", "hideTabs(); schedule(OFF_RECHECK_MS); } else schedule(pollEvery());",
     "a failed read is retried within 2 s"),
    ("if (st.ring || Object.keys(st.acks).length) return POLL_MS;", "", "a ring on screen keeps the 2 s poll"),
    ("if (!document.hidden && st.enabled !== false) pollNow();", "", "the page becoming visible"),
    ("st.push.rxAt = 0; watch();", "", "the socket closing reads at once"),
]


@pytest.mark.parametrize("old,new,check", PUSH_CONTROLS, ids=[c[2] for c in PUSH_CONTROLS])
def test_alarm_push_negative_controls(tmp_path, old, new, check):
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    src = (ROOT / "zoe-ui" / "dist" / "js" / "touch-alarms.js").read_text()
    assert src.count(old) == 1, f"control no longer matches the source: {old!r}"
    mutated = tmp_path / "touch-alarms.js"
    mutated.write_text(src.replace(old, new))
    harness = ROOT / "zoe-ui" / "dist" / "test_touch_alarms.js"
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120, env={**os.environ, "AL_SRC": str(mutated)})
    assert proc.returncode != 0 and check in proc.stderr, f"mutation survived ({check}):\n{proc.stdout}\n{proc.stderr[-800:]}"
