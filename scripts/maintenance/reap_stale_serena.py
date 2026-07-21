#!/usr/bin/env python3
"""Reap stale Serena MCP servers before they exhaust the Jetson's swap.

Why this exists: every Claude Code / Codex dev session spawns its own
`serena start-mcp-server` over stdio (see .mcp.json), and each instance grows
to ~2-4.5 GB once its language servers index the repo. When sessions die or go
idle, the servers linger. On 2026-07-03 eight of them pinned ~35 GB of swap
(57/57 GB full) and OOM'd llama-server — a production voice-brain outage
caused entirely by dev tooling.

Kill policy (deliberately conservative — a wrong kill only degrades one dev
session's code-intel; a missed kill can take down the live brain):

  1. ORPHAN: parent pid is 1 (the spawning session is gone) -> reap.
  2. COLD:   VmSwap above --swap-mb (default 1500 MB) -> reap. An actively
             used LSP keeps its hot pages resident; an instance that has been
             pushed multiple GB into swap is a cold leftover by definition.

  A process younger than --grace-min (default 30) minutes is never touched,
  whatever its state — fresh sessions swap-storm briefly while indexing.

Recycle policy (the SHARED server only, and never a kill): the shared unit is
exempt from both kill rules above, so nothing used to bound its growth. Its
per-session project/symbol caches are never evicted, so it creeps ~1 GB/day
(measured 2026-07-20/21/22) and it does so in RSS, not swap, which the COLD
rule cannot see. That creep is not cosmetic: it drags the box's idle headroom
under the deploy workflow's memory gate, and three consecutive deploys failed
at 640-658 MB available on 2026-07-21 purely because of it. So when the shared
server exceeds --shared-rss-mb, this tool asks systemd to RESTART the unit
(graceful; Restart=always brings it back in ~4 s with an empty cache). It is
never signalled directly — the reap exemption stays absolute.

Dry-run by default; pass --execute to actually kill (SIGTERM, then SIGKILL
after --term-wait seconds for anything that ignored it).

Run ad hoc or via the shipped user timer (scripts/setup/systemd/
zoe-serena-reaper.{service,timer} — hourly). The reaper only signals
processes owned by the invoking user; foreign-owned instances are reported
and skipped.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

SERENA_MARKER = "serena start-mcp-server"

# The SHARED server (scripts/setup/systemd/serena-mcp.service) is a long-lived
# systemd user unit, not a per-session leftover: systemd owns its lifecycle and
# its unit carries the MemoryHigh/MemoryMax caps. It must be exempt from both
# reap rules. The ORPHAN rule would not fire on it today (a service main process
# is parented to `systemd --user`, not PID 1), but the COLD rule absolutely
# would — a legitimately idle shared server on this swap-heavy box drifts past
# the swap threshold, and reaping it would kill code-intel for the whole fleet
# and throw away the warm index that this server exists to share. Match on the
# cgroup rather than the cmdline: the cgroup is set by systemd and cannot be
# spoofed by a stray hand-launched process copying the same flags.
SHARED_SERVICE_MARKER = "serena-mcp.service"

# RSS at which the shared server is recycled. Its unit allows MemoryMax=2G, but
# the box only has ~1-1.5 GB of idle headroom to give, and the deploy gate needs
# 800 MB of it; 900 MB fires well before the cap while leaving a normal working
# cache alone (a freshly restarted server sits at ~100 MB).
SHARED_RECYCLE_RSS_MB = 900
SHARED_UNIT = "serena-mcp.service"


@dataclass
class SerenaProc:
    pid: int
    ppid: int
    uid: int
    age_s: float
    swap_kb: int
    rss_kb: int
    cmdline: str
    cgroup: str = ""


def _clock_ticks() -> float:
    return os.sysconf("SC_CLK_TCK")


def _boot_time_s() -> float:
    with open("/proc/stat") as fh:
        for line in fh:
            if line.startswith("btime "):
                return float(line.split()[1])
    raise RuntimeError("btime not found in /proc/stat")


def scan(now: float | None = None) -> list[SerenaProc]:
    """Enumerate live serena MCP server processes from /proc."""
    now = now if now is not None else time.time()
    boot = _boot_time_s()
    ticks = _clock_ticks()
    found: list[SerenaProc] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
            if SERENA_MARKER not in cmdline:
                continue
            with open(f"/proc/{pid}/stat") as fh:
                stat = fh.read()
            # field 22 (1-indexed) = starttime in ticks; fields after the
            # parenthesised comm — split on the LAST ')' to survive odd names.
            after = stat.rsplit(")", 1)[1].split()
            ppid = int(after[1])
            start_ticks = float(after[19])
            age_s = now - (boot + start_ticks / ticks)
            try:
                with open(f"/proc/{pid}/cgroup") as fh:
                    cgroup = fh.read().strip()
            except (FileNotFoundError, PermissionError):
                cgroup = ""  # unreadable -> treated as unmanaged, i.e. reapable
            swap_kb = rss_kb = 0
            uid = -1
            with open(f"/proc/{pid}/status") as fh:
                for line in fh:
                    if line.startswith("VmSwap:"):
                        swap_kb = int(line.split()[1])
                    elif line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                    elif line.startswith("Uid:"):
                        uid = int(line.split()[1])
        except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
            continue  # raced a process exit or unreadable entry — skip
        found.append(SerenaProc(pid, ppid, uid, age_s, swap_kb, rss_kb, cmdline, cgroup))
    return found


def classify(p: SerenaProc, *, swap_mb: int, grace_min: int) -> str | None:
    """Return a reap reason, or None to keep the process."""
    if SHARED_SERVICE_MARKER in p.cgroup:
        return None  # the shared server — systemd owns it; see SHARED_SERVICE_MARKER
    if p.age_s < grace_min * 60:
        return None  # fresh session still indexing — never touch
    if p.ppid == 1:
        return "orphan (parent exited)"
    if p.swap_kb > swap_mb * 1024:
        return f"cold ({p.swap_kb // 1024} MB swapped > {swap_mb} MB limit)"
    return None


def is_shared(p: SerenaProc) -> bool:
    """True for the systemd-owned shared server (cgroup match, unspoofable)."""
    return SHARED_SERVICE_MARKER in p.cgroup


def classify_shared(p: SerenaProc, *, shared_rss_mb: int) -> str | None:
    """Return a RECYCLE reason for the shared server, or None to leave it.

    Deliberately separate from classify(): that function's job is to decide
    what to KILL, and its shared-server exemption must stay absolute. This one
    only ever leads to a systemctl restart.
    """
    if not is_shared(p):
        return None  # only the shared unit is recyclable; strays get reaped
    if p.rss_kb > shared_rss_mb * 1024:
        return f"bloated ({p.rss_kb // 1024} MB RSS > {shared_rss_mb} MB limit)"
    return None


def recycle_shared(execute: bool) -> str:
    """Restart the shared unit via systemd. Never signals the process."""
    if not execute:
        return "would restart (dry run)"
    proc = subprocess.run(
        ["systemctl", "--user", "restart", SHARED_UNIT],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return f"restart FAILED rc={proc.returncode}: {(proc.stderr or '').strip()[:120]}"
    return "restarted"


def _still_serena(pid: int) -> bool:
    """Re-confirm the pid is still a serena server, just before signalling."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return SERENA_MARKER in fh.read().replace(b"\0", b" ").decode(errors="replace")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False


def reap(pid: int, term_wait: float) -> str:
    # Guard the scan()->reap() window: the target may have exited and its pid
    # been recycled by an unrelated same-uid process (PermissionError only
    # catches cross-uid reuse). Re-read the cmdline and bail if the marker is
    # gone, so we never signal an innocent recycled pid.
    if not _still_serena(pid):
        return "PID reused/exited (cmdline no longer serena)"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already gone"
    except PermissionError:
        return "SKIPPED (owned by another user)"
    deadline = time.time() + term_wait
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "terminated"
        time.sleep(0.2)
    # Re-verify before the harder SIGKILL: the pid could have been recycled
    # during term_wait after the original exited.
    if not _still_serena(pid):
        return "terminated"
    try:
        os.kill(pid, signal.SIGKILL)
        return "killed (ignored SIGTERM)"
    except ProcessLookupError:
        return "terminated"
    except PermissionError:
        return "SKIPPED (owned by another user)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--swap-mb", type=int, default=1500,
                    help="reap when VmSwap exceeds this many MB (default 1500)")
    ap.add_argument("--grace-min", type=int, default=30,
                    help="never touch processes younger than this (default 30 min)")
    ap.add_argument("--term-wait", type=float, default=10.0,
                    help="seconds to wait after SIGTERM before SIGKILL")
    ap.add_argument("--shared-rss-mb", type=int, default=SHARED_RECYCLE_RSS_MB,
                    help=f"restart the shared unit above this RSS "
                         f"(default {SHARED_RECYCLE_RSS_MB} MB)")
    ap.add_argument("--no-recycle", action="store_true",
                    help="never restart the shared unit (kill rules unchanged)")
    ap.add_argument("--execute", action="store_true",
                    help="actually kill/restart; without this flag it is a dry run")
    args = ap.parse_args()

    my_uid = os.getuid()
    procs = scan()
    reaped = kept = skipped = 0
    recycled = 0
    for p in sorted(procs, key=lambda x: -x.swap_kb):
        reason = classify(p, swap_mb=args.swap_mb, grace_min=args.grace_min)
        ident = (f"pid={p.pid} age={p.age_s/3600:.1f}h swap={p.swap_kb//1024}MB "
                 f"rss={p.rss_kb//1024}MB ppid={p.ppid}")
        # The shared unit is exempt from every kill rule, but may be RECYCLED
        # (systemctl restart) when its never-evicted caches have bloated it.
        if is_shared(p):
            bloat = None if args.no_recycle else classify_shared(
                p, shared_rss_mb=args.shared_rss_mb)
            if bloat is None:
                kept += 1
                print(f"KEEP  {ident} — shared unit")
            else:
                outcome = recycle_shared(args.execute)
                recycled += 1
                print(f"RECYCLE {ident} — {bloat} -> {outcome}")
            continue
        if reason is None:
            kept += 1
            print(f"KEEP  {ident}")
            continue
        if p.uid != my_uid:
            skipped += 1
            print(f"SKIP  {ident} — {reason}, but owned by uid {p.uid}")
            continue
        if args.execute:
            outcome = reap(p.pid, args.term_wait)
            print(f"REAP  {ident} — {reason} -> {outcome}")
            if "SKIPPED" in outcome:
                skipped += 1
            else:
                reaped += 1
        else:
            print(f"WOULD-REAP  {ident} — {reason} (dry run; pass --execute)")
            reaped += 1

    mode = "reaped" if args.execute else "would reap"
    # reaped + kept + skipped + recycled == len(procs) always (every process
    # hits exactly one bucket), so the operator can verify the run at a glance.
    print(f"\n{len(procs)} serena server(s): {mode} {reaped}, kept {kept}, "
          f"skipped {skipped}, recycled {recycled}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
