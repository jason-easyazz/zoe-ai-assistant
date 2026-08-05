"""Offline tests for scripts/maintenance/reap_stale_omnigent_runners.py.

The reaper exists because of the 2026-08-04 leak: one
`python -m omnigent.runner._entry` per cross-review dispatch, never collected —
19 resident, the box at 0-245 MB available, and Omnigent's host daemon then
refusing to come online under load, killing two reviews mid-flight.

THE LOAD-BEARING ASSERTION HERE IS THAT A LIVE RUNNER IS NEVER REAPED. A missed
reap costs ~50 MB until the next hourly run; a wrong reap kills an agent turn
mid-flight and destroys work already paid for. Every KEEP rule therefore gets a
negative control: flip the one fact that makes the runner live and the same
process must become reapable, so the test can actually go red.

Process trees are SYNTHETIC — a temp directory shaped like /proc, driven through
the module's `proc_root` parameter. No live Omnigent, no signals, no network.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import time

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts" / "maintenance" / "reap_stale_omnigent_runners.py"
)
_spec = importlib.util.spec_from_file_location("reap_stale_omnigent_runners", _SCRIPT)
reaper = importlib.util.module_from_spec(_spec)
# Registered before exec: the script uses `from __future__ import annotations`,
# so dataclass field resolution looks the module up by name.
sys.modules["reap_stale_omnigent_runners"] = reaper
_spec.loader.exec_module(reaper)

SID = "3db7562bdfec4653b31fd89055e65b45"
OTHER_SID = "23f7e424dda244cfa894e31823ec2e68"

RUNNER_ARGV = ["/usr/bin/python3", "-m", "omnigent.runner._entry"]
HOST_ARGV = ["/usr/bin/python3", "/root/.local/bin/omnigent", "host", "http://127.0.0.1:6767"]


def harness_argv(sid):
    return [
        "/usr/bin/python3", "-m", "omnigent.runtime.harnesses._runner",
        "--harness", "claude-sdk",
        "--uds", f"/tmp/omnigent-abc/conv-{sid}.sock",
        "--conversation-id", sid,
        "--parent-pid", "4242",
    ]


# --------------------------------------------------------- synthetic /proc ---


class FakeProc:
    """A temp directory shaped like /proc, for scan()/live_conversations()."""

    BOOT = 1_000_000.0
    TICKS = os.sysconf("SC_CLK_TCK")

    def __init__(self, root: pathlib.Path):
        self.root = root
        (root / "stat").write_text(f"cpu 1 2 3\nbtime {int(self.BOOT)}\n")

    def add(self, pid, argv, *, ppid=1000, uid=1000, age_s=3600.0, rss_kb=51_200,
            log_env="__default__", extra_env=()):
        d = self.root / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        start_ticks = int((self.now() - self.BOOT - age_s) * self.TICKS)
        # /proc/<pid>/stat: `pid (comm) state ppid ...`; after the LAST ')' the
        # fields start at `state`, so starttime (field 22) lands at index 19.
        after = ["S", str(ppid)] + ["0"] * 17 + [str(start_ticks)] + ["0"] * 30
        (d / "stat").write_text(f"{pid} (python3) " + " ".join(after) + "\n")
        (d / "status").write_text(
            f"Name:\tpython3\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nVmRSS:\t{rss_kb} kB\n"
        )
        env = list(extra_env)
        if log_env == "__default__":
            log_env = f"/root/.omnigent/logs/runner/runner-{SID}-20260804-001420-811333.log"
        if log_env is not None:
            env.append(f"{reaper.LOG_ENV_VAR}={log_env}")
        (d / "environ").write_bytes(b"\0".join(e.encode() for e in env) + b"\0")
        return d

    def now(self):
        return self.BOOT + 500_000.0


@pytest.fixture
def fp(tmp_path):
    return FakeProc(tmp_path)


# ------------------------------------------------------------- attribution ---


def test_session_id_is_recovered_from_the_runner_environment():
    """The ONLY place the session id exists outside the process's memory."""
    blob = b"\0".join([
        b"HOME=/root",
        f"{reaper.LOG_ENV_VAR}=/root/.omnigent/logs/runner/"
        f"runner-{SID}-20260804-001420-811333.log".encode(),
        b"PATH=/usr/bin",
    ])
    assert reaper.session_id_from_environ(blob) == SID


@pytest.mark.parametrize(
    "value",
    [
        # The CLI launch path names its log plainly — no session slug at all.
        "/root/.omnigent/logs/runner/runner-20260804-001420-811333.log",
        # A degenerate short id must not become a wildcard (same floor
        # cross_review.sh enforces on the session id it interpolates).
        "/root/.omnigent/logs/runner/runner-abc-20260804-001420-811333.log",
        "/root/.omnigent/logs/cli/cli-20260804-001420-811333.log",
        "",
    ],
)
def test_an_unrecognised_log_name_yields_no_session_id(value):
    blob = f"{reaper.LOG_ENV_VAR}={value}".encode()
    assert reaper.session_id_from_environ(blob) is None


def test_a_runner_with_no_log_env_is_unattributable():
    assert reaper.session_id_from_environ(b"HOME=/root\0PATH=/usr/bin\0") is None


# --------------------------------------------------------------- the scan ---


def test_scan_finds_only_per_session_runners(fp):
    fp.add(100, HOST_ARGV, log_env=None)               # the host daemon: infrastructure
    fp.add(101, harness_argv(SID), log_env=None)       # the harness: reaped by cmdline scan
    fp.add(102, RUNNER_ARGV, ppid=100, age_s=7200.0, rss_kb=61_440)

    found = reaper.scan(str(fp.root), now=fp.now())

    assert [p.pid for p in found] == [102]
    p = found[0]
    assert p.ppid == 100, "the runner is parented to the live host, never to init"
    assert p.session_id == SID
    assert p.rss_kb == 61_440
    assert abs(p.age_s - 7200.0) < 2.0


def test_scan_survives_a_process_that_exits_mid_walk(fp):
    fp.add(200, RUNNER_ARGV)
    broken = fp.root / "201"
    broken.mkdir()
    (broken / "cmdline").write_bytes(b"\0".join(a.encode() for a in RUNNER_ARGV) + b"\0")
    # No stat/status/environ: the racing-exit shape.
    assert [p.pid for p in reaper.scan(str(fp.root), now=fp.now())] == [200]


def test_live_conversations_reads_the_harness_argv(fp):
    fp.add(300, harness_argv(SID), log_env=None)
    fp.add(301, harness_argv(OTHER_SID), log_env=None)
    fp.add(302, RUNNER_ARGV)
    assert reaper.live_conversations(str(fp.root)) == {SID, OTHER_SID}


# ------------------------------------------------------------ the policy ----


def runner(**kw):
    base = dict(pid=4242, ppid=1000, uid=1000, age_s=3 * 3600, rss_kb=51_200,
                cmdline=" ".join(RUNNER_ARGV), session_id=SID)
    base.update(kw)
    return reaper.RunnerProc(**base)


def policy(status="idle", busy=(), grace_min=20, asked=None):
    def status_of(sid):
        if asked is not None:
            asked.append(sid)
        return status
    return dict(grace_min=grace_min, busy_sessions=set(busy), status_of=status_of)


def test_a_runner_serving_a_live_harness_is_never_reaped():
    """RULE 3 — the hard local proof of life."""
    p = runner()
    assert reaper.classify(p, **policy(status="idle", busy=[SID])) is None


def test_negative_control_the_same_runner_without_its_harness_is_reaped():
    """Flip the ONE live fact and the KEEP must become a REAP.

    Without this control the rule above is indistinguishable from a reaper that
    never reaps anything, which is precisely the state the box was in.
    """
    p = runner()
    assert reaper.classify(p, **policy(status="idle", busy=[])) is not None


@pytest.mark.parametrize("status", ["running", "waiting"])
def test_a_nonterminal_session_keeps_its_runner(status):
    """RULE 4 — `waiting` is awaiting external work, NOT finished."""
    assert reaper.classify(runner(), **policy(status=status)) is None


def test_an_unreachable_server_keeps_every_runner():
    """RULE 4, fail-safe half: no answer is never permission to kill."""
    assert reaper.classify(runner(), **policy(status=reaper.STATUS_UNKNOWN)) is None


def test_an_unattributable_runner_is_kept_even_though_nothing_can_vouch_for_it():
    """RULE 2 — we cannot ask about a session we cannot name."""
    p = runner(session_id=None)
    asked = []
    assert reaper.classify(p, **policy(status="idle", asked=asked)) is None
    assert asked == [], "an unattributable runner must not trigger a session lookup"


def test_a_fresh_runner_is_untouchable_even_when_its_session_is_gone():
    """RULE 1 — a runner is tracked from Popen, before it connects or spawns."""
    p = runner(age_s=5 * 60)
    assert reaper.classify(p, **policy(status=reaper.STATUS_GONE)) is None
    # Negative control: past the grace window the same process is collectable.
    assert reaper.classify(runner(age_s=25 * 60), **policy(status=reaper.STATUS_GONE))


def test_a_gone_session_is_reaped():
    reason = reaper.classify(runner(), **policy(status=reaper.STATUS_GONE))
    assert reason and "no longer exists" in reason and SID in reason


def test_a_terminal_session_is_reaped():
    reason = reaper.classify(runner(), **policy(status="idle"))
    assert reason and "terminal (idle)" in reason


def test_the_policy_only_ever_asks_about_its_own_session():
    """ATTRIBUTION CONTROL — one runner's verdict must not consult another's.

    The whole failure class here is a cleanup that cannot prove which session a
    process belongs to. Every lookup must be keyed on the id read from THAT
    process's own environment.
    """
    asked = []
    reaper.classify(runner(session_id=SID), **policy(status="idle", asked=asked))
    assert asked == [SID]


def test_another_sessions_busy_harness_does_not_protect_this_runner():
    """Attribution, the other way round: liveness is not transferable.

    A busy sibling session must neither shield this runner (a leak that hides
    behind whatever else is running) nor be mistaken for it.
    """
    p = runner(session_id=SID)
    assert reaper.classify(p, **policy(status="idle", busy=[OTHER_SID])) is not None
    # And the sibling itself, when it IS the one being classified, is kept.
    assert reaper.classify(
        runner(session_id=OTHER_SID), **policy(status="idle", busy=[OTHER_SID])
    ) is None


def test_a_mixed_tree_reaps_exactly_the_stale_runner(fp):
    """End to end over a synthetic tree: two runners, one live, one leaked."""
    fp.add(400, HOST_ARGV, log_env=None)
    fp.add(401, RUNNER_ARGV, ppid=400, age_s=7200.0,
           log_env=f"/root/.omnigent/logs/runner/runner-{SID}-20260804-001420-811333.log")
    fp.add(402, RUNNER_ARGV, ppid=400, age_s=7200.0,
           log_env=f"/root/.omnigent/logs/runner/runner-{OTHER_SID}-20260804-0014"
                   "20-811334.log")
    fp.add(403, harness_argv(OTHER_SID), ppid=402, log_env=None)

    procs = reaper.scan(str(fp.root), now=fp.now())
    busy = reaper.live_conversations(str(fp.root))
    verdicts = {
        p.pid: reaper.classify(p, grace_min=20, busy_sessions=busy, status_of=lambda _s: "idle")
        for p in procs
    }

    assert verdicts[401] is not None, "the leaked runner must be collected"
    assert verdicts[402] is None, "the runner serving a live conversation must survive"


# ----------------------------------------------------------- reap mechanics ---


class FakePoller:
    GONE = "gone"
    OK = "ok"

    def __init__(self, rc=0, doc=None, kind="ok"):
        self.rc = rc
        self.doc = doc if doc is not None else {"status": "idle"}
        self.kind = kind
        self.stops = []

    def stop_session(self, server, sid, budget, interval, http_timeout):
        self.stops.append(sid)
        return self.rc

    def fetch_session(self, server, sid, timeout):
        return self.kind, self.doc, ""


def test_reap_prefers_the_graceful_stop_over_a_signal(fp, monkeypatch):
    """A signalled runner is reported to its session as a CRASH; a stopped one
    is popped from the host's registry first and exits quietly."""
    fp.add(500, RUNNER_ARGV)
    poller = FakePoller(rc=0)
    killed = []
    monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    # The graceful stop worked: the process is gone by the time we look.
    monkeypatch.setattr(reaper, "_wait_gone", lambda *a, **k: True)

    p = reaper.scan(str(fp.root), now=fp.now())[0]
    out = reaper.reap(p, poller=poller, server="http://x", term_wait=1.0,
                      stop_budget_s=5.0, http_timeout=1.0, proc_root=str(fp.root))

    assert out == "stopped via stop_session"
    assert poller.stops == [SID], "the stop must name this runner's own session"
    assert killed == [], "no signal is sent when the graceful path works"


def test_reap_falls_back_to_signals_when_the_stop_does_not_land(fp, monkeypatch):
    fp.add(501, RUNNER_ARGV)
    poller = FakePoller(rc=8)  # EXIT_STOP_FAILED
    killed = []
    monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(reaper, "_wait_gone", lambda *a, **k: True)

    p = reaper.scan(str(fp.root), now=fp.now())[0]
    out = reaper.reap(p, poller=poller, server="http://x", term_wait=1.0,
                      stop_budget_s=5.0, http_timeout=1.0, proc_root=str(fp.root))

    assert out == "terminated"
    assert [sig for _pid, sig in killed] == [reaper.signal.SIGTERM]


def test_reap_refuses_a_recycled_pid(fp, monkeypatch):
    """The scan->reap window: the pid may now belong to something innocent."""
    fp.add(502, ["/usr/bin/python3", "-m", "something.else"], log_env=None)
    poller = FakePoller()
    monkeypatch.setattr(reaper.os, "kill", lambda *_a: pytest.fail("signalled a recycled pid"))

    p = reaper.RunnerProc(pid=502, ppid=1, uid=os.getuid(), age_s=9999.0, rss_kb=1,
                          cmdline=" ".join(RUNNER_ARGV), session_id=SID)
    out = reaper.reap(p, poller=poller, server="http://x", term_wait=0.1,
                      stop_budget_s=1.0, http_timeout=1.0, proc_root=str(fp.root))

    assert "PID reused" in out
    assert poller.stops == [], "a recycled pid must not even trigger a session stop"


def test_status_lookup_treats_a_missing_status_field_as_unknown():
    poller = FakePoller(doc={"id": SID}, kind="ok")
    status_of = reaper.make_status_of(poller, "http://x", 1.0)
    assert status_of(SID) == reaper.STATUS_UNKNOWN


def test_status_lookup_maps_a_vanished_session_to_gone():
    poller = FakePoller(kind="gone")
    status_of = reaper.make_status_of(poller, "http://x", 1.0)
    assert status_of(SID) == reaper.STATUS_GONE


# --------------------------------------------------------------- the CLI -----


def test_dry_run_is_the_default(fp, monkeypatch, capsys):
    fp.add(600, RUNNER_ARGV, uid=os.getuid(), age_s=7200.0)
    monkeypatch.setattr(reaper, "_load_poller", lambda: FakePoller())
    monkeypatch.setattr(reaper.os, "kill", lambda *_a: pytest.fail("dry run must not signal"))
    monkeypatch.setattr(time, "time", fp.now)

    rc = reaper.main(["--proc-root", str(fp.root), "--server", "http://x"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "WOULD-REAP" in out
    assert "dry run" in out
    assert "would reap 1, kept 0, skipped 0" in out


def test_a_foreign_uid_is_reported_and_skipped(fp, monkeypatch, capsys):
    fp.add(601, RUNNER_ARGV, uid=os.getuid() + 4242, age_s=7200.0)
    monkeypatch.setattr(reaper, "_load_poller", lambda: FakePoller())
    monkeypatch.setattr(time, "time", fp.now)

    reaper.main(["--proc-root", str(fp.root), "--server", "http://x", "--execute"])

    out = capsys.readouterr().out
    assert "SKIP" in out and "owned by uid" in out
    assert "reaped 0, kept 0, skipped 1" in out
