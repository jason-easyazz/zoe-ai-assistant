"""Unit tests for the stale-Serena reaper's kill policy (pure logic only).

The policy is the load-bearing part: a wrong KEEP re-creates the 2026-07-03
swap-exhaustion outage; a wrong REAP degrades a live dev session. scan()/reap()
are thin /proc + signal wrappers exercised by the script's dry-run mode.
"""
import importlib.util
import pathlib
import sys

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
                swap_kb=0, rss_kb=500_000, cmdline="serena start-mcp-server")
    base.update(kw)
    return reaper.SerenaProc(**base)


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


def test_scan_finds_only_serena_processes():
    # Live smoke: scan() must not crash and must only match the marker.
    for p in reaper.scan():
        assert reaper.SERENA_MARKER in p.cmdline


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
