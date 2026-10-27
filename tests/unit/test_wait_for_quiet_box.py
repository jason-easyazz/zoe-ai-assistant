"""The 04:30 voice regression waits for a quiet box (2026-10-27).

Root cause pinned in scripts/setup/systemd/zoe-voice-regression.service.d/20-wait-quiet-box.conf: the 04:15 Serena restart's ~40 min `.gitignore` walk over the worktrees
contends with the probe's STT timing. The fix is a bounded wait before the probe, NOT a wider speed threshold - these tests pin both halves.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "maintenance"))

import wait_for_quiet_box as wq  # noqa: E402

LIM = wq.Limits()
HZ = wq.CLK_TCK


class Clock:
    """A fake clock + sampler: ``script(t)`` -> (busy_fraction, serena_cores, load1) at second t."""

    def __init__(self, script):
        self.t, self.busy, self.total, self.serena, self.script, self.said = 0.0, 0, 0, 0, script, []

    def mono(self):
        return self.t

    def sleep(self, s):
        busy_frac, serena_cores, _load = self.script(self.t)
        self.t += s
        self.total += int(s * HZ * 8)                       # 8 cores
        self.busy += int(s * HZ * 8 * busy_frac)
        self.serena += int(s * HZ * serena_cores)

    def read(self):
        return wq.Sample(self.t, self.busy, self.total, self.serena, self.script(self.t)[2])


def run(script, **kw):
    c = Clock(script)
    ok = wq.wait_until_quiet(c.read, c.sleep, c.mono, LIM, max_wait_s=kw.get("max_wait_s", 2700), poll_s=20.0, consecutive=3, say=c.said.append)
    return ok, c


def test_a_quiet_box_returns_at_once():
    ok, c = run(lambda t: (0.02, 0.0, 0.3))
    assert ok and c.t == 60.0                                # three quiet samples, 20 s apart


def test_serenas_startup_scan_is_waited_out_even_when_the_whole_box_looks_idle():
    """One busy core of eight is 12% of the box (under the 20% bar) - the scan is caught by Serena's own CPU, which is why that gauge exists."""
    ok, c = run(lambda t: (0.12, 1.0, 0.8) if t < 2400 else (0.02, 0.0, 0.3))
    assert ok and 2400 <= c.t <= 2400 + 120 and any("serena" in m for m in c.said)


def test_system_busy_iowait_and_load_each_block_on_their_own():
    for script, word in ((lambda t: (0.6, 0.0, 0.5) if t < 400 else (0.0, 0.0, 0.5), "CPU busy"), (lambda t: (0.0, 0.0, 6.0) if t < 400 else (0.0, 0.0, 0.5), "load1")):
        ok, c = run(script)
        assert ok and c.t >= 400 and any(word in m for m in c.said), word


def test_it_gives_up_at_the_bound_says_so_and_still_lets_the_probe_run():
    ok, c = run(lambda t: (0.9, 0.0, 9.0), max_wait_s=600)
    assert ok is False and 600 <= c.t < 700 and any("proceeding anyway" in m for m in c.said)


def test_three_quiet_samples_in_a_row_are_required_not_one():
    ok, c = run(lambda t: (0.9, 0.0, 0.5) if int(t // 20) % 2 == 0 else (0.0, 0.0, 0.5), max_wait_s=300)    # alternating busy / quiet never makes three in a row
    assert ok is False


def test_the_real_samplers_parse_proc_text():
    assert wq.cpu_ticks("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 1 1 1 1 1 1 1 1 1\n") == (200, 1000)          # iowait (50) stays in busy
    assert wq.cpu_ticks("garbage") == (0, 0)
    line = "123 (a b) c) S 7 1 1 0 -1 0 0 0 0 0 40 2 0 0 20 0 1 0 100 0 0"                                      # a comm with spaces AND parens
    assert wq._ppid_and_ticks(line) == (7, 42)
    assert wq._ppid_and_ticks("no parens") is None


def test_tree_ticks_adds_the_language_servers_children(tmp_path):
    def proc(pid, ppid, ticks, cmd):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} (x) S {ppid} 1 1 0 -1 0 0 0 0 0 {ticks} 0 0 0 20 0 1 0 100 0 0")
        (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())
    proc(10, 1, 100, "/usr/bin/python3 /home/zoe/.local/bin/serena start-mcp-server --project x")
    proc(11, 10, 30, "jedi-language-server")
    proc(12, 11, 5, "grandchild")
    proc(20, 1, 9999, "unrelated")
    assert wq.tree_ticks(wq.SERENA_MARK, proc=str(tmp_path)) == 135


def test_the_dropin_is_bounded_ignores_a_missing_helper_and_leaves_the_speed_threshold_alone():
    dropin = (REPO / "scripts/setup/systemd/zoe-voice-regression.service.d/20-wait-quiet-box.conf").read_text()
    body = "\n".join(ln for ln in dropin.splitlines() if not ln.lstrip().startswith("#"))
    wait = int(re.search(r"--max-wait-s (\d+)", body).group(1))
    assert re.search(r"^ExecStartPre=-/usr/bin/python3 %h/assistant/scripts/maintenance/wait_for_quiet_box\.py", body, re.M), "the `-` keeps a missing helper from failing the gate"
    assert wait >= 2400, "longer than Serena's ~40 minute start-up scan"
    assert int(re.search(r"TimeoutStartSec=(\d+)", body).group(1)) >= wait + 300 + 1800, "TimeoutStartSec covers ALL ExecStartPre + ExecStart: wait + postgres + the replay"
    unit = (REPO / "scripts/setup/systemd/zoe-voice-regression.service").read_text()
    assert "wait_for_quiet_box" not in unit, "the template is voice-path scope: the wait ships as a drop-in"
    probe = (REPO / "scripts/maintenance/voice_regression_probe.py").read_text()
    assert '"--warn-ratio", type=float, default=float(os.environ.get("ZOE_VOICE_WARN_RATIO", "1.5"))' in probe, "the 1.5x speed threshold must not be widened to hide contention"


def test_the_wait_never_runs_past_not_after_when_the_run_starts_in_the_hour_before_it():
    from datetime import datetime
    cap, note = wq.cap_by_clock(2700.0, datetime(2026, 10, 28, 4, 30, 0), "04:50")
    assert cap == 1200.0 and "04:50" in note                                          # the nightly replay may not START after 04:50
    assert wq.cap_by_clock(600.0, datetime(2026, 10, 28, 4, 30, 0), "04:50") == (600.0, "")        # an already shorter wait is left alone
    assert wq.cap_by_clock(2700.0, datetime(2026, 10, 28, 12, 0, 0), "04:50") == (2700.0, "")      # a noon catch-up fire is not clipped
    assert wq.cap_by_clock(2700.0, datetime(2026, 10, 28, 2, 0, 0), "04:50") == (2700.0, "")        # nor one more than an hour before
    assert wq.cap_by_clock(2700.0, datetime(2026, 10, 28, 4, 49, 30), "04:50")[0] == 30.0
    assert wq.cap_by_clock(2700.0, datetime(2026, 10, 28, 4, 55, 0), "04:50") == (2700.0, "")       # past it already: outside the rule (the lander gate covers a late fire)


def test_the_dropin_bounds_the_nightly_replays_start_before_landings_resume():
    body = (REPO / "scripts/setup/systemd/zoe-voice-regression.service.d/20-wait-quiet-box.conf").read_text()
    m = re.search(r"--not-after (\d\d):(\d\d)", "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#")))
    assert m and int(m.group(1)) * 60 + int(m.group(2)) <= 4 * 60 + 50, "the replay must not start after 04:50: landings resume at 04:55 (night_busy.sh)"


def test_a_clock_capped_wait_ends_at_the_cap_not_one_poll_later():
    """04:49:50 start with --not-after 04:50: the capped wait (10 s) must end at 10 s, not after a full 20 s poll."""
    from datetime import datetime
    cap, _ = wq.cap_by_clock(2700.0, datetime(2026, 10, 28, 4, 49, 50), "04:50")
    ok, c = run(lambda t: (0.9, 0.0, 9.0), max_wait_s=cap)
    assert ok is False and c.t == 10.0 and any("proceeding anyway" in m for m in c.said)
