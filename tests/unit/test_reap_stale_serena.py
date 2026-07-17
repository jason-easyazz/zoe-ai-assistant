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
