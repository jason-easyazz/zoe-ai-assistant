"""The night window and landing contract (2026-10-13 incident), pinned so it cannot drift silently.

The contract (docs/knowledge/merge-and-deploy.md "Landings and the night window"): no landing and no harness may
hold the brain-window lock (zoe-brain-window.lock under the system temp dir), nor be a live ``land_voice_pr.sh``
process, past the window's 20-min busy grace (gone by 03:10). Since 2026-10-19 the pause is MEASURED (the
operator-local night_busy.sh: voice landings do not start from 01:55 until the window has finished), not a fixed
01:50-04:55 span; the 01:50-04:55 constants below are only the conservative envelope the window's own schedule is
asserted to sit inside. The night window REFUSES on a held lock and WAITS (bounded, then refuses) on a
live lander, so a lander that sleeps through 02:50 makes the 12B window refuse. The operator landers keep their
side of the contract; this file pins the side of the window: its busy detection really does cover the lander and
the lock, and its schedule stays inside the span the landers promise to keep clear.

Read-only on night_window behaviour: nothing here changes the window, it only asserts what it already does.
"""
from __future__ import annotations

import fcntl
import inspect
import os
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))
sys.path.insert(0, str(REPO / "scripts" / "night"))

import night_window as nw  # noqa: E402
from zmb import bakeoff as bk  # noqa: E402

CLEAR_FROM = 1 * 60 + 50      # landers never START (and hold nothing) from 01:50 ...
CLEAR_UNTIL = 4 * 60 + 55     # ... until 04:55
LOCK_NAME = "zoe-brain-window.lock"


def _lander_pattern() -> str:
    pats = [p for label, p in nw.NightWindow.BUSY_PATTERNS if "landing" in label]
    assert len(pats) == 1, "the night window must carry exactly one voice-PR landing pattern"
    return pats[0]


def test_busy_detection_is_the_bakeoff_one_and_covers_land_voice_pr():
    """NightWindow must not override away the lander detection (it inherits bk.Window)."""
    for name in ("BUSY_PATTERNS", "landing_running", "busy_reason", "wait_until_quiet", "probe_lock", "take_lock"):
        assert name not in vars(nw.NightWindow), f"NightWindow overrides {name}: re-pin the landing contract against the override"
    rx = re.compile(_lander_pattern())
    # a real lander command line (pgrep -f matches the FULL argv, so a directly executed script shows its
    # shebang interpreter path first: /bin/bash or /usr/bin/bash, not a bare bash)
    for argv in (
        "bash /opt/zoe/scripts/maintenance/land_voice_pr.sh 1234",
        "bash ./land_voice_pr.sh",
        "/bin/bash /opt/zoe/scripts/maintenance/land_voice_pr.sh 1234",
        "/usr/bin/bash /opt/zoe/scripts/maintenance/land_voice_pr.sh",
        "/usr/local/bin/bash -x /opt/zoe/scripts/maintenance/land_voice_pr.sh 7",
    ):
        assert rx.search(argv), f"a live lander was not recognised: {argv}"
    # a mere mention (editor, tail, grep, a script that only names it) is not a landing
    for argv in (
        "vim /opt/zoe/scripts/maintenance/land_voice_pr.sh",
        "tail -f land_voice_pr.sh.log",
        "bash -c sleep 60 watching-land_voice_pr.sh",
        "/bin/bash /opt/zoe/scripts/other.sh land_voice_pr.sh",
        "grep land_voice_pr.sh /var/log/syslog",
    ):
        assert not rx.search(argv), f"a mere mention was taken for a lander: {argv}"


def test_negative_control_the_old_anchored_pattern_missed_a_direct_execution():
    """The pre-fix pattern (bare bash only) cannot see /bin/bash ...land_voice_pr.sh: the new fixtures must fail it."""
    old = re.compile(r"^bash .*/land_voice_pr\.sh")
    assert old.search("bash /opt/zoe/scripts/maintenance/land_voice_pr.sh 1")
    assert not old.search("/bin/bash /opt/zoe/scripts/maintenance/land_voice_pr.sh 1")
    assert not old.search("/usr/bin/bash /opt/zoe/scripts/maintenance/land_voice_pr.sh")
    assert re.compile(_lander_pattern()).search("/bin/bash /opt/zoe/scripts/maintenance/land_voice_pr.sh 1")


class _Host:
    """Answers only what busy_reason / wait_until_quiet ask: pgrep (a lander is, or is not, alive) and the panel ssh."""

    def __init__(self, lander_alive: bool):
        self.t = 1_800_000_000.0
        self.lander_alive = lander_alive
        self.pgreps: "list[str]" = []

    def now(self):
        return self.t

    def mono(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def run(self, argv, timeout=60.0, mutating=True, env=None):
        if argv[0] == "pgrep":
            self.pgreps.append(argv[-1])
            alive = self.lander_alive and "land_voice_pr" in argv[-1]
            return bk.Result(0 if alive else 1, "")
        return bk.Result(1, "")                              # ssh to the panel: unreachable = says nothing


def _window(tmp_path, monkeypatch, lander_alive: bool):
    # a private harness lock path: a real voice probe on the dev box must not colour these tests
    monkeypatch.setattr(bk.Window, "HARNESS_LOCK", str(tmp_path / "harness.lock"))
    cfg = nw.NightCfg(lock_path=str(tmp_path / LOCK_NAME), night_dir=tmp_path / "nd", report_dir=tmp_path / "nr")
    host = _Host(lander_alive)
    return nw.NightWindow(cfg, host, lambda _m: None, dry=False, run_id="contract"), host


def test_a_live_lander_makes_the_window_busy_then_refuse_after_the_bounded_wait(tmp_path, monkeypatch):
    w, host = _window(tmp_path, monkeypatch, lander_alive=True)
    assert "voice-PR landing" in w.busy_reason()
    t0 = host.t
    with pytest.raises(bk.Refused) as exc:
        w.wait_until_quiet()
    assert "still not quiet" in str(exc.value) and "voice-PR landing" in str(exc.value)
    waited_min = (host.t - t0) / 60.0
    assert waited_min >= w.cfg.quiet_wait_max_min, "the wait is bounded by quiet_wait_max_min, not skipped"
    assert waited_min <= w.cfg.quiet_wait_max_min + w.cfg.quiet_poll_s / 60.0 + 0.01


def test_negative_control_no_lander_means_not_busy(tmp_path, monkeypatch):
    w, host = _window(tmp_path, monkeypatch, lander_alive=False)
    assert w.busy_reason() == ""
    assert w.wait_until_quiet() == ""
    assert any("land_voice_pr" in p for p in host.pgreps), "the lander pattern was really consulted"


def test_a_held_brain_window_lock_is_seen_and_a_released_one_is_not(tmp_path, monkeypatch):
    w, _host = _window(tmp_path, monkeypatch, lander_alive=False)
    assert w.probe_lock() is True
    fd = os.open(w.cfg.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert w.probe_lock() is False, "a landing holding the brain-window lock must be seen"
        with pytest.raises(bk.Refused):
            w.take_lock()
    finally:
        os.close(fd)
    assert w.probe_lock() is True


def test_the_default_lock_is_the_one_the_contract_names():
    assert f"/{LOCK_NAME}" in inspect.getsource(bk.Cfg)


def test_preflight_waits_for_the_lock_and_a_quiet_box_and_rechecks_under_the_lock():
    """preflight WAITS (bounded) for the lock before stopping anything, then for a quiet box, and the post-lock recheck still looks for a lander - handing the lock back and
    waiting again instead of refusing the night on the first sighting (2026-10-26: a lander spawned a second before the lock was taken)."""
    src = inspect.getsource(nw.NightWindow.preflight)
    assert "self.wait_for_lock()" in src and "self.wait_until_quiet()" in src and "self.take_lock_when_clear(" in src
    assert src.index("self.wait_for_lock()") < src.index("self.wait_until_quiet()") < src.index("self.take_lock_when_clear(")
    lock = inspect.getsource(nw.NightWindow.wait_for_lock)
    assert "self.probe_lock()" in lock and "has the brain. Nothing was stopped" in lock and "self.refuse(why)" in lock, "a lock still held after the bounded wait must refuse, nothing stopped"
    clear = inspect.getsource(nw.NightWindow.take_lock_when_clear)
    assert "self.busy_reason()" in clear and "self.busy_now()" in clear, "the post-lock recheck must still look for a lander"
    assert clear.index("self.take_lock()") < clear.index("self.busy_reason()") < clear.index("self.release_lock()") < clear.index("self.wait_until_quiet()")
    assert "capped_waits" in clear and "self.host.sleep" in clear, "the hand-back loop is bounded by the end-by deadline and always spends time"


def test_the_night_schedule_sits_inside_the_span_the_landers_keep_clear():
    """The conservative 01:50-04:55 envelope must cover the timer, the latest allowed start, the end-by deadline and the voice-gate blackout."""
    cfg = nw.NightCfg()
    start_lo, start_hi = cfg.night_hours
    assert start_lo < CLEAR_FROM, "a manual run may start before 01:50: it waits on a lander, by design (the timer never does)"
    assert start_hi <= CLEAR_UNTIL
    assert cfg.end_by <= CLEAR_UNTIL
    assert max(b for _a, b in cfg.blackouts) <= CLEAR_UNTIL
    timer = (REPO / "scripts/night/systemd/zoe-night-window.timer").read_text()
    m = re.search(r"OnCalendar=\S+\s+(\d\d):(\d\d)", timer)
    assert m, "timer OnCalendar not found"
    fires = int(m.group(1)) * 60 + int(m.group(2))
    delay = int(re.search(r"RandomizedDelaySec=(\d+)", timer).group(1)) / 60.0
    assert CLEAR_FROM <= fires and fires + delay <= CLEAR_UNTIL


def test_the_binding_rule_lives_in_agents_md_and_the_docs_only_point_at_it():
    """Binding rules belong in AGENTS.md (DOX); docs/knowledge keeps the rationale and links to it."""
    rule = (REPO / "scripts/AGENTS.md").read_text()
    for needle in ("01:55", "04:55", "night_busy.sh", LOCK_NAME, "land_voice_pr.sh"):
        assert needle in rule, f"scripts/AGENTS.md must carry the landing contract ({needle!r} missing)"
    assert "scripts/AGENTS.md" in (REPO / "AGENTS.md").read_text() and "01:55" in (REPO / "AGENTS.md").read_text()
    for doc in ("docs/knowledge/merge-and-deploy.md", "docs/knowledge/night-window.md"):
        assert "scripts/AGENTS.md" in (REPO / doc).read_text(), f"{doc} must point at the binding rule"
