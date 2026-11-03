#!/usr/bin/env python3
"""Wait, bounded, until the box is quiet - then return 0 so a speed-sensitive probe can start.

Why (2026-10-27): the nightly voice regression (``zoe-voice-regression.timer``, 04:30) failed on SPEED only, STT over 1.5x its baseline, on
about three nights in four since 2026-10-15, while the same probe passed at other hours. The cause is the 04:15 ``zoe-serena-pregate-restart``:
a restarted shared Serena walks every ``.gitignore`` under the repo - including ~300 ``.claude/worktrees`` checkouts, which its own ``ignored_paths``
and the root ``.gitignore`` do NOT prune (its gitignore finder prunes only on the .gitignore files it has loaded so far) - for about 40 minutes
(journal ``serena-mcp`` 04:15 to 04:55), then initialises its language server. That is CPU + disk-metadata load exactly where the probe measures
Moonshine's latency. The restart itself is wanted (it frees the leak the gate needs RAM back from), so the probe waits for the box instead of
widening the speed threshold: a threshold widened to hide contention also hides a real STT regression.

"Quiet" = ``--consecutive`` samples in a row (``--poll-s`` apart) where ALL of:
  * system CPU busy (everything but idle; iowait counts, a directory walk is iowait) <= ``--max-busy``;
  * the shared Serena process tree (``--serena``, matched on ``serena start-mcp-server`` in its command line) uses <= ``--max-serena-cores`` of one core;
  * the 1-minute load average <= ``--max-load1`` (the same 3.0 the voice lander waits for).

Bounded by ``--max-wait-s``: on timeout it SAYS so and exits 0 anyway - a gate that blocks forever on a busy box is worse than one that
measures a busy box and says so. Counts and numbers only; never prints a command line or an environment.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
SERENA_MARK = "serena start-mcp-server"


@dataclass(frozen=True)
class Sample:
    t: float                  # monotonic seconds
    busy: int                 # CPU ticks that were not idle (iowait counts as busy)
    total: int                # all CPU ticks
    serena_ticks: int         # utime + stime of the Serena tree
    load1: float


@dataclass(frozen=True)
class Limits:
    max_busy: float = 0.20
    max_serena_cores: float = 0.15
    max_load1: float = 3.0


def judge(prev: Sample, cur: Sample, lim: Limits) -> "tuple[bool, str]":
    """(quiet, why-not) between two samples. Pure."""
    dt = cur.t - prev.t
    dtotal = cur.total - prev.total
    busy = (cur.busy - prev.busy) / dtotal if dtotal > 0 else 0.0
    serena = (cur.serena_ticks - prev.serena_ticks) / (CLK_TCK * dt) if dt > 0 else 0.0
    why = []
    if busy > lim.max_busy:
        why.append(f"CPU busy {busy * 100:.0f}% (> {lim.max_busy * 100:.0f}%)")
    if serena > lim.max_serena_cores:
        why.append(f"serena {serena:.2f} cores (> {lim.max_serena_cores:.2f})")
    if cur.load1 > lim.max_load1:
        why.append(f"load1 {cur.load1:.1f} (> {lim.max_load1:.1f})")
    return not why, "; ".join(why)


def wait_until_quiet(read: "Callable[[], Sample]", sleep: "Callable[[float], None]", mono: "Callable[[], float]", lim: Limits, *, max_wait_s: float,
                     poll_s: float, consecutive: int, say: "Callable[[str], None]" = print) -> bool:
    """Block until ``consecutive`` quiet samples in a row, or ``max_wait_s``. True = quiet, False = gave up (the caller proceeds anyway). The sampler is injected so a test drives time."""
    start = mono()
    prev = read()
    streak, last_msg = 0, 0.0
    while True:
        sleep(max(0.0, min(poll_s, max_wait_s - (mono() - start))))      # never sleep past the (clock-capped) bound
        cur = read()
        ok, why = judge(prev, cur, lim)
        prev = cur
        streak = streak + 1 if ok else 0
        if streak >= consecutive:
            say(f"quiet box after {mono() - start:.0f}s ({streak} quiet samples)")
            return True
        if mono() - start >= max_wait_s:
            say(f"box still busy after {max_wait_s:.0f}s ({why or 'not enough quiet samples'}): proceeding anyway - the probe will measure a busy box")
            return False
        if not ok and mono() - last_msg >= 300.0:       # one progress line per five minutes, not per poll
            say(f"waiting for a quiet box ({why}) {mono() - start:.0f}s so far")
            last_msg = mono()


# ── the real sampler (reads /proc; nothing is executed) ──

def cap_by_clock(max_wait_s: float, now: "datetime", not_after: str) -> "tuple[float, str]":
    """(max_wait_s, note) with the wait cut so that it never runs past the local clock time ``not_after`` (HH:MM) - but only for a run that STARTS within the hour before it.
    A late catch-up fire (Persistent=true at noon, say) is unaffected. Why: the 04:30 nightly replay writes the single voice-gate artifact; it must be over before landings
    resume at 04:55, so it may not start after 04:50."""
    hh, mm = (int(x) for x in not_after.split(":"))
    limit = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    left = (limit - now).total_seconds()
    if 0 <= left <= 3600.0 and left < max_wait_s:
        return left, f"capped at {not_after} ({left:.0f}s): the replay must not start after it"
    return max_wait_s, ""


def _read(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def cpu_ticks(stat_text: str) -> "tuple[int, int]":
    """(busy, total) from the first ``cpu`` line of /proc/stat. idle = idle only (iowait is busy for our purpose)."""
    line = next((ln for ln in stat_text.splitlines() if ln.startswith("cpu ")), "")
    f = [int(x) for x in line.split()[1:]] if line else []
    if len(f) < 4:
        return 0, 0
    total = sum(f[:8])                        # user nice system idle iowait irq softirq steal (guest is already inside user)
    return total - f[3], total


def _ppid_and_ticks(stat_text: str) -> "Optional[tuple[int, int]]":
    """(ppid, utime+stime) from a /proc/<pid>/stat line - the comm field may contain spaces and parens, so split after the LAST ')'."""
    i = stat_text.rfind(")")
    if i < 0:
        return None
    rest = stat_text[i + 2:].split()
    try:
        return int(rest[1]), int(rest[11]) + int(rest[12])
    except (IndexError, ValueError):
        return None


def tree_ticks(match: str, proc: str = "/proc") -> int:
    """utime+stime of every process whose command line contains ``match`` and of all their descendants (the language servers Serena spawns)."""
    info: "dict[int, tuple[int, int]]" = {}
    roots = set()
    try:
        names = [n for n in os.listdir(proc) if n.isdigit()]
    except OSError:
        return 0
    for n in names:
        st = _ppid_and_ticks(_read(f"{proc}/{n}/stat"))
        if st is None:
            continue
        info[int(n)] = st
        if match in _read(f"{proc}/{n}/cmdline").replace("\0", " "):
            roots.add(int(n))
    members = set(roots)
    grew = True
    while grew:
        grew = False
        for pid, (ppid, _t) in info.items():
            if pid not in members and ppid in members:
                members.add(pid)
                grew = True
    return sum(info[p][1] for p in members if p in info)


def real_sample(watch_serena: bool) -> Sample:
    busy, total = cpu_ticks(_read("/proc/stat"))
    try:
        load1 = float(_read("/proc/loadavg").split()[0])
    except (IndexError, ValueError):
        load1 = 0.0
    return Sample(time.monotonic(), busy, total, tree_ticks(SERENA_MARK) if watch_serena else 0, load1)


def main(argv: "Optional[list[str]]" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--max-wait-s", type=float, default=2700.0, help="give up (and proceed) after this long; default 45 min = longer than Serena's ~40 min start-up scan")
    ap.add_argument("--poll-s", type=float, default=20.0)
    ap.add_argument("--consecutive", type=int, default=3, help="quiet samples in a row required")
    ap.add_argument("--max-busy", type=float, default=Limits.max_busy)
    ap.add_argument("--max-serena-cores", type=float, default=Limits.max_serena_cores)
    ap.add_argument("--max-load1", type=float, default=Limits.max_load1)
    ap.add_argument("--not-after", default="", metavar="HH:MM", help="a run starting in the hour before this local time never waits past it (proceeds and says so)")
    ap.add_argument("--no-serena", action="store_true", help="do not watch the Serena process tree")
    args = ap.parse_args(argv)
    lim = Limits(args.max_busy, args.max_serena_cores, args.max_load1)
    say = lambda m: print(f"wait_for_quiet_box: {m}", flush=True)  # noqa: E731
    max_wait_s = args.max_wait_s
    if args.not_after:
        max_wait_s, note = cap_by_clock(max_wait_s, datetime.now(), args.not_after)
        if note:
            say(note)
    wait_until_quiet(lambda: real_sample(not args.no_serena), time.sleep, time.monotonic, lim, max_wait_s=max_wait_s, poll_s=args.poll_s,
                     consecutive=args.consecutive, say=say)
    return 0


if __name__ == "__main__":
    sys.exit(main())
