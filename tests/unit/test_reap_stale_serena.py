"""Unit tests for the stale-Serena reaper's kill policy (pure logic only).

The policy is the load-bearing part: a wrong KEEP re-creates the 2026-07-03
swap-exhaustion outage; a wrong REAP degrades a live dev session. scan()/reap()
are thin /proc + signal wrappers exercised by the script's dry-run mode.
"""
import importlib.util
import os
import pathlib
import sys

import pytest
# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe


_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts" / "maintenance" / "reap_stale_serena.py"
)
_spec = importlib.util.spec_from_file_location("reap_stale_serena", _SCRIPT)
reaper = importlib.util.module_from_spec(_spec)
# Must be registered before exec: the script uses `from __future__ import
# annotations`, and dataclass field resolution looks the module up by name.
sys.modules["reap_stale_serena"] = reaper
_spec.loader.exec_module(reaper)

DEFAULTS = dict(swap_mb=1500, grace_min=30)


def proc(**kw):
    base = dict(pid=4242, ppid=1000, uid=1000, age_s=3 * 3600,
                swap_kb=0, rss_kb=500_000, cmdline="serena start-mcp-server",
                cgroup="0::/user.slice/user-1000.slice/session-1.scope")
    base.update(kw)
    return reaper.SerenaProc(**base)


SHARED_CGROUP = ("0::/user.slice/user-1000.slice/user@1000.service"
                 "/app.slice/serena-mcp.service")


def test_fresh_process_is_never_touched_even_if_orphaned_and_swapped():
    p = proc(age_s=10 * 60, ppid=1, swap_kb=4_500_000)
    assert reaper.classify(p, **DEFAULTS) is None


def test_orphan_is_reaped_after_grace():
    p = proc(ppid=1)
    assert "orphan" in reaper.classify(p, **DEFAULTS)


def test_cold_swapped_instance_is_reaped():
    p = proc(swap_kb=4_500_000)  # ~4.4 GB in swap — the outage signature
    reason = reaper.classify(p, **DEFAULTS)
    assert reason is not None and "cold" in reason


def test_warm_resident_instance_is_kept():
    # Active session: big RSS is fine, only swap matters.
    p = proc(rss_kb=3_000_000, swap_kb=200_000)
    assert reaper.classify(p, **DEFAULTS) is None


def test_swap_threshold_boundary_is_exclusive():
    p = proc(swap_kb=1500 * 1024)  # exactly at the limit -> keep
    assert reaper.classify(p, **DEFAULTS) is None
    p = proc(swap_kb=1500 * 1024 + 1)
    assert reaper.classify(p, **DEFAULTS) is not None


def test_shared_service_is_never_reaped_when_cold():
    # The shared server (serena-mcp.service) is long-lived and systemd-managed.
    # An idle one on this swap-heavy box WILL drift past the swap threshold;
    # reaping it would kill code-intel for the whole fleet and discard the warm
    # index the shared server exists to provide.
    p = proc(cgroup=SHARED_CGROUP, swap_kb=4_500_000)
    assert reaper.classify(p, **DEFAULTS) is None


def test_shared_service_is_never_reaped_even_if_reparented():
    # Belt-and-braces: the ORPHAN rule must not fire on the shared server even
    # if it somehow shows ppid=1 (e.g. relaunched through the scope wrapper).
    p = proc(cgroup=SHARED_CGROUP, ppid=1, swap_kb=4_500_000)
    assert reaper.classify(p, **DEFAULTS) is None


def test_per_session_server_still_reaped_despite_similar_cgroup_text():
    # The exemption must key on the real unit cgroup, not any stray path that
    # merely mentions serena — a per-session leftover stays reapable.
    p = proc(cgroup="0::/user.slice/user-1000.slice/app.slice/run-serena-xyz.scope",
             ppid=1)
    assert "orphan" in reaper.classify(p, **DEFAULTS)


def test_unreadable_cgroup_defaults_to_reapable():
    # scan() records "" when /proc/<pid>/cgroup cannot be read. Fail-safe here
    # means staying reapable: a missed reap can take down the live brain.
    p = proc(cgroup="", swap_kb=4_500_000)
    assert reaper.classify(p, **DEFAULTS) is not None


def _own_cmdline() -> str:
    """This process's cmdline, normalised exactly the way scan() reads /proc."""
    with open("/proc/self/cmdline", "rb") as fh:
        return fh.read().replace(b"\0", b" ").decode(errors="replace").strip()


def test_scan_populates_cgroup_field(monkeypatch):
    # The exemption is worthless if scan() silently omits the field classify()
    # reads. Iterating scan() as-is would be VACUOUS: CI runs no Serena server,
    # so the loop body would never execute and the test would go green having
    # proven nothing. Instead point the marker at THIS process — using the full
    # cmdline, which nothing else can contain — so scan() is guaranteed exactly
    # one hit, then assert the cgroup it recorded matches the kernel's view.
    monkeypatch.setattr(reaper, "SERENA_MARKER", _own_cmdline())
    with open("/proc/self/cgroup") as fh:
        own_cgroup = fh.read().strip()

    hits = [p for p in reaper.scan() if p.pid == os.getpid()]
    assert hits, "scan() failed to find this process despite a matching marker"
    assert hits[0].cgroup == own_cgroup
    assert hits[0].cgroup != ""


def test_scan_excludes_processes_not_matching_the_marker(monkeypatch):
    # The other half of the filter: scan() must not return processes that do
    # NOT carry the marker. Without this, a scan() that returned every process
    # on the box would still satisfy the test above.
    monkeypatch.setattr(reaper, "SERENA_MARKER", "zzz-no-process-can-contain-this-marker-zzz")
    assert reaper.scan() == []


def test_scan_finds_only_serena_processes(monkeypatch):
    # Was the same vacuous shape as the cgroup test: on a box with no Serena
    # running, scan() returns [] and the loop asserts nothing. Pin the marker to
    # this process so there is guaranteed to be exactly one hit to assert on.
    own = _own_cmdline()
    monkeypatch.setattr(reaper, "SERENA_MARKER", own)
    procs = reaper.scan()

    assert procs, "scan() found nothing despite a marker matching this process"
    assert any(p.pid == os.getpid() for p in procs)
    for p in procs:
        assert own in p.cmdline  # the marker is what selects them — nothing else
        assert p.rss_kb > 0      # /proc/<pid>/status was really parsed


def test_reap_bails_on_pid_reuse(monkeypatch):
    # If the pid's cmdline is no longer serena (exited/recycled), reap must not
    # signal anything.
    monkeypatch.setattr(reaper, "_still_serena", lambda pid: False)
    sent = []
    monkeypatch.setattr(reaper.os, "kill", lambda *a: sent.append(a))
    outcome = reaper.reap(4242, term_wait=0.0)
    assert "PID reused" in outcome and sent == []


def test_still_serena_false_for_dead_pid():
    # A pid that cannot exist must classify as not-serena (never signalled).
    assert reaper._still_serena(2**31 - 1) is False


# --------------------------------------------------------------------------- #
# Recycle policy — the SHARED unit only, and never a kill.
#
# Why these exist: the shared server is exempt from both kill rules, so nothing
# bounded its growth. Its caches are never evicted (~1 GB/day, in RSS not swap,
# so the COLD rule is blind to it) and that creep starved the deploy workflow's
# memory gate three runs in a row on 2026-07-21.
# --------------------------------------------------------------------------- #
RECYCLE_DEFAULTS = dict(shared_rss_mb=900)


def test_bloated_shared_server_is_recycled():
    p = proc(cgroup=SHARED_CGROUP, rss_kb=1_100_000)
    reason = reaper.classify_shared(p, **RECYCLE_DEFAULTS)
    assert reason is not None and "bloated" in reason


def test_normal_shared_server_is_left_alone():
    # A freshly restarted server sits ~100 MB; a working cache must not trip it.
    p = proc(cgroup=SHARED_CGROUP, rss_kb=400_000)
    assert reaper.classify_shared(p, **RECYCLE_DEFAULTS) is None


def test_recycle_threshold_is_exclusive():
    p = proc(cgroup=SHARED_CGROUP, rss_kb=900 * 1024)
    assert reaper.classify_shared(p, **RECYCLE_DEFAULTS) is None
    p = proc(cgroup=SHARED_CGROUP, rss_kb=900 * 1024 + 1)
    assert reaper.classify_shared(p, **RECYCLE_DEFAULTS) is not None


def test_per_session_instance_is_never_recycled():
    # Strays are the KILL path's business; recycling restarts the shared UNIT,
    # which would be the wrong action entirely for a session-owned process.
    p = proc(rss_kb=4_000_000)
    assert reaper.classify_shared(p, **RECYCLE_DEFAULTS) is None


def test_bloated_shared_server_is_still_never_KILLED():
    # The recycle path must not have weakened the kill exemption: even huge and
    # fully swapped, the shared unit is never signalled.
    p = proc(cgroup=SHARED_CGROUP, rss_kb=4_000_000, swap_kb=4_500_000, ppid=1)
    assert reaper.classify(p, **DEFAULTS) is None


def test_recycle_restarts_the_unit_via_systemd_not_signals(monkeypatch):
    calls = []

    class _R:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(reaper.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _R())
    killed = []
    monkeypatch.setattr(reaper.os, "kill", lambda *a: killed.append(a))

    assert reaper.recycle_shared(execute=True) == "restarted"
    assert calls == [["systemctl", "--user", "restart", "serena-mcp.service"]]
    assert killed == [], "recycle must never signal the process directly"


def test_recycle_dry_run_touches_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(reaper.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    assert "would restart" in reaper.recycle_shared(execute=False)
    assert calls == []


def test_recycle_reports_systemd_failure(monkeypatch):
    class _R:
        returncode = 1
        stderr = "Failed to restart serena-mcp.service: Unit not found."

    monkeypatch.setattr(reaper.subprocess, "run", lambda cmd, **kw: _R())
    outcome = reaper.recycle_shared(execute=True)
    assert "FAILED" in outcome and "Unit not found" in outcome


# --- Greptile PR #1499: three ways the recycle could silently not-work ------ #
def _shared_proc(rss_mb):
    return proc(cgroup=SHARED_CGROUP, rss_kb=rss_mb * 1024)


def _run_main(monkeypatch, procs, *, run_result, argv=("--execute",)):
    """Drive main() over a fake process list with a fake systemctl."""
    calls = []

    monkeypatch.setattr(reaper, "scan", lambda *a, **k: list(procs))
    monkeypatch.setattr(reaper.os, "getuid", lambda: 1000)
    monkeypatch.setattr(reaper.subprocess, "run",
                        lambda cmd, **kw: calls.append((cmd, kw)) or run_result)
    monkeypatch.setattr(sys, "argv", ["reap_stale_serena.py", *argv])
    rc = reaper.main()
    return rc, calls


class _OK:
    returncode = 0
    stderr = ""


class _Fail:
    returncode = 1
    stderr = "Failed to restart serena-mcp.service: Interactive authentication required."


def test_recycle_passes_the_user_bus_env():
    # systemctl --user needs XDG_RUNTIME_DIR; the timer has it, a hand/CI run
    # may not, and without it the restart fails while the bloat survives.
    seen = {}

    class _Rec:
        returncode = 0
        stderr = ""

    import types as _t
    fake = _t.SimpleNamespace(run=lambda cmd, **kw: seen.update(kw) or _Rec())
    orig = reaper.subprocess
    reaper.subprocess = fake
    try:
        reaper.recycle_shared(execute=True)
    finally:
        reaper.subprocess = orig
    assert "XDG_RUNTIME_DIR" in seen["env"]
    assert seen["env"]["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"


def test_failed_recycle_makes_the_run_fail(monkeypatch):
    # A failed restart must not be recorded as a healthy hourly run: the unit
    # is still bloated and the deploy gate is still starving.
    rc, calls = _run_main(monkeypatch, [_shared_proc(1100)], run_result=_Fail())
    assert rc == 1, "a failed recycle must exit non-zero"
    assert len(calls) == 1


def test_successful_recycle_exits_zero(monkeypatch):
    rc, _ = _run_main(monkeypatch, [_shared_proc(1100)], run_result=_OK())
    assert rc == 0


def test_unit_is_restarted_once_however_many_procs_share_the_cgroup(monkeypatch):
    # One restart recycles the whole unit; restarting per-process would flap it.
    rc, calls = _run_main(
        monkeypatch,
        [_shared_proc(1100), _shared_proc(1000), _shared_proc(950)],
        run_result=_OK(),
    )
    assert rc == 0
    assert len(calls) == 1, f"expected exactly one restart, got {len(calls)}"


def test_no_recycle_flag_leaves_a_bloated_unit_alone(monkeypatch):
    rc, calls = _run_main(monkeypatch, [_shared_proc(1100)],
                          run_result=_OK(), argv=("--execute", "--no-recycle"))
    assert rc == 0 and calls == []


# --- Warm-up guard (measured 2026-07-22) ------------------------------------ #
# A cold shared server walks ~120 agent worktrees; its own log says "Loading of
# .gitignore files completed in 15 minutes" and it is ALREADY ~1 GB while doing
# it — above the recycle threshold before serving one request. Without a guard
# the hourly timer restarts it mid-warm-up forever and the fleet never gets a
# usable server: the recycle would cause the outage it exists to prevent.
def test_bloated_but_still_warming_up_is_not_recycled():
    p = proc(cgroup=SHARED_CGROUP, rss_kb=1_100_000, age_s=10 * 60)
    assert reaper.classify_shared(p, **RECYCLE_DEFAULTS, grace_min=30) is None


def test_bloated_after_warmup_is_recycled():
    p = proc(cgroup=SHARED_CGROUP, rss_kb=1_100_000, age_s=45 * 60)
    assert reaper.classify_shared(p, **RECYCLE_DEFAULTS, grace_min=30) is not None


def test_warmup_guard_boundary_is_exclusive():
    at = proc(cgroup=SHARED_CGROUP, rss_kb=1_100_000, age_s=30 * 60)
    assert reaper.classify_shared(at, **RECYCLE_DEFAULTS, grace_min=30) is not None
    just_under = proc(cgroup=SHARED_CGROUP, rss_kb=1_100_000, age_s=30 * 60 - 1)
    assert reaper.classify_shared(just_under, **RECYCLE_DEFAULTS, grace_min=30) is None


def test_main_does_not_restart_a_warming_up_server(monkeypatch):
    # End-to-end through main(): a fresh, bloated shared server must survive.
    rc, calls = _run_main(
        monkeypatch,
        [proc(cgroup=SHARED_CGROUP, rss_kb=1_200_000, age_s=5 * 60)],
        run_result=_OK(),
    )
    assert rc == 0 and calls == [], "restarted a server that was still warming up"
