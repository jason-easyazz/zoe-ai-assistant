"""CI wrapper: an expired PIN session never deafens the kiosk — dist/test_touch_panel_session_recovery.js.

Runs the REAL executor functions against a fake server, fake push sockets, a fake service
worker and a fake clock: a 401 on the poll leaves the push socket open and the poll alive,
re-mints a guest session and resumes delivery; a 403 stops poll+sync only (the kiosk keeps its
socket); an expiring member session is downgraded before the server refuses it; the watchdog
heals a socket that stays down (restart, then a rate-limited reload); a persistent 401 is not
a mint storm. Fails (not skips) without node on CI.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_touch_panel_session_recovery.js"


def _node() -> str:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node.js is not installed on this CI host, so the panel session recovery harness cannot run.")
        pytest.skip("Node.js is not installed on this host")
    return node


def test_touch_panel_session_recovery_harness():
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    proc = subprocess.run([_node(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"panel session recovery harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout
    assert "A1: a 401 on poll keeps socket" in proc.stdout and "D1: watchdog restarts" in proc.stdout


def test_sw_panel_poll_rides_out_the_guest_reclaim_window():
    """The SW poll survives the member->guest handover's expected 403s (bounded), and stops on a real one."""
    harness = ROOT / "zoe-ui" / "dist" / "test_sw_panel_poll.js"
    assert harness.is_file(), f"harness missing: {harness}"
    proc = subprocess.run([_node(), str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"sw panel poll harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "6 checks passed" in proc.stdout


def test_voice_form_replies_are_gated_on_panel_liveness():
    """The voice path only says 'check the panel / tap Done / tap Confirm' through the gated helpers."""
    src = (ROOT / "zoe-data" / "routers" / "voice_tts.py").read_text()
    assert "_form_live = _panel_can_see(panel_id)" in src
    assert "_list_add_reply_text(_item_text, _prefill_items, _form_live)" in src and "_calendar_form_reply_text(" in src
    # a dead panel queues NO form (a stale Done must not add what the reply said was not added)
    assert src.count("if _form_live:") == 2
    # the literal tails live ONLY inside the helpers
    assert src.count("Tap Done on the panel to save") == 1
    assert src.count("check the details and tap Confirm") == 1


def test_form_done_refusal_is_not_toasted_as_saved_and_boot_guard_stamps_the_handover():
    """PR #1999 review pins (the behaviour itself is in the node harness + test_panel_form_confirm_owner.py)."""
    ex = (ROOT / "zoe-ui" / "dist" / "js" / "touch-ui-executor.js").read_text()
    i = ex.index("const confirmRes = await api('/api/ui/panel/form/confirm'")
    j = ex.index("showToast(panelType === 'shopping_list' ? 'List saved'", i)
    assert "!confirmRes.ok" in ex[i:j] and "throw new Error" in ex[i:j], "a refused Done must throw before the 'saved' toast"
    home = (ROOT / "zoe-ui" / "dist" / "touch" / "home.html").read_text()
    guard = home[: home.index("<style>")]
    assert "zoe_member_handover_at" in guard and guard.index("zoe_member_handover_at") < guard.index("removeItem('zoe_session')"), \
        "the boot guard must stamp the member handover BEFORE it drops the expired session"
    assert "adoptMemberHandover();" in ex
