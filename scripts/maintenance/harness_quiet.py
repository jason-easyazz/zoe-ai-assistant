#!/usr/bin/env python3
"""Harness quiet hours: our own test traffic stays off the live brain while the household is awake (P-2).

Measured 2026-10-19 (deep dive 2): the harness was 97% of the brain's turns and shares its ONE llama slot,
so 29 of 100 household panel turns re-prefilled more than 500 tokens and the household's brain-TTFT p90 was
5.9 s. The rule, in one place for every harness:

* **Waking hours (default 07:00-22:00 local) refuse** the Samantha bar, the day-sim, the person bench, the
  user-model A/B, the recall-evidence probe and an ad-hoc voice-regression probe.
* **Outside them** a harness may run, but still **yields while the panel is in use** (a wake or follow-up in
  the panel's ``voice.log`` within the last 10 minutes - the SAME check ``land_voice_pr.sh``'s
  ``panel_quiet`` makes, ported here so both read one rule).
* **The landing loop's replay probe is EXEMPT from the hours** (it is how voice merges are gated), and only
  yields to the panel: ``ZOE_HARNESS_LANDING=1`` (set by the lander) or ``--landing``.
* **Forced** runs (``ZOE_HARNESS_FORCE=1`` or ``--force``) skip both checks and say so - an owner's
  deliberate choice, never a default.

``ZOE_HARNESS_QUIET_START`` / ``ZOE_HARNESS_QUIET_END`` (``HH:MM``) move the window; ``off`` in either
disables the hours (the panel yield still applies). The panel check is bounded (8 s ssh) and FAILS OPEN
exactly like ``panel_quiet``: no answer from the Pi = quiet (a dead panel is not in use, and the hours are
the primary guard).

CLI (exit 0 = may run, 3 = must not, printed reason):
    harness_quiet.py check NAME [--landing] [--force]
    harness_quiet.py brain-window NAME [--budget-min N]   # the 01:50-04:55 brain window (multi-step runs)
    harness_quiet.py panel-quiet            # 0 quiet / 1 in use (the land_voice_pr.sh check)
Stdlib only; importable from scripts/perf and services/zoe-data/tests/replay_samples.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

DEFAULT_START = "07:00"
DEFAULT_END = "22:00"
# The brain window (binding, scripts/AGENTS.md "Brain-window quiet hours"): the 12B night window (timer 02:50), the
# 04:10-04:52 voice gate and the 04:30 nightly voice-regression own the box 01:50-04:55. Nothing multi-step that
# holds the harness lock may START inside it, nor start so late that it is still alive at 01:50.
BRAIN_WINDOW_START = dt.time(1, 50)
BRAIN_WINDOW_END = dt.time(4, 55)
PANEL_WINDOW_S = 600
PANEL_HOST = "zoe-pi"
PANEL_LOG = "/home/pi/.zoe-voice/voice.log"
EXIT_REFUSED = 3

# land_voice_pr.sh panel_quiet, verbatim in intent: the last wake / follow-up line's 19-char timestamp.
_PANEL_CMD = (f"grep -a 'Wake word detected\\|Follow-up speech detected' {PANEL_LOG} | tail -1 | cut -c1-19")


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _parse_hm(raw: str) -> Optional[dt.time]:
    raw = (raw or "").strip()
    if not raw or raw.lower() == "off":
        return None
    hh, _, mm = raw.partition(":")
    return dt.time(int(hh), int(mm or 0))


def waking_window() -> Optional[tuple[dt.time, dt.time]]:
    """(start, end) local, or None when the hours are disabled. A bad value falls back to the default (fail
    closed: a typo must not open the box to the harness all day)."""
    try:
        start = _parse_hm(os.environ.get("ZOE_HARNESS_QUIET_START", DEFAULT_START))
        end = _parse_hm(os.environ.get("ZOE_HARNESS_QUIET_END", DEFAULT_END))
    except (TypeError, ValueError):
        start, end = _parse_hm(DEFAULT_START), _parse_hm(DEFAULT_END)
    if start is None or end is None:
        return None
    return start, end


def in_waking_hours(now: Optional[dt.datetime] = None) -> bool:
    win = waking_window()
    if win is None:
        return False
    t = (now or dt.datetime.now()).time()
    start, end = win
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # a window that wraps midnight


def last_panel_activity(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Optional[dt.datetime]:
    """The panel's last wake / follow-up (local time), or None when unknown (ssh failed, no line)."""
    try:
        out = runner(
            ["timeout", "8", "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", PANEL_HOST, _PANEL_CMD],
            capture_output=True, text=True, timeout=12, check=False,
        )
        stamp = (out.stdout or "").strip()[:19]
        if not stamp:
            return None
        return dt.datetime.strptime(stamp.replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001 - fail open like panel_quiet
        return None


def panel_quiet(now: Optional[dt.datetime] = None, *, window_s: int = PANEL_WINDOW_S,
                last: Optional[Callable[[], Optional[dt.datetime]]] = None) -> bool:
    """True when the panel has not been used for ``window_s`` (or its activity cannot be read)."""
    seen = (last or last_panel_activity)()
    if seen is None:
        return True
    age = ((now or dt.datetime.now()) - seen).total_seconds()
    return age >= window_s


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str
    kind: str = "ok"   # ok | forced | hours | panel


def check(name: str, *, landing: Optional[bool] = None, force: Optional[bool] = None,
          now: Optional[dt.datetime] = None,
          panel: Optional[Callable[[], bool]] = None) -> Verdict:
    """May harness ``name`` start NOW? (module docstring). ``panel`` overrides the panel check (tests)."""
    if force is None:
        force = _truthy(os.environ.get("ZOE_HARNESS_FORCE"))
    if landing is None:
        landing = _truthy(os.environ.get("ZOE_HARNESS_LANDING"))
    if force:
        return Verdict(True, f"{name}: FORCED (ZOE_HARNESS_FORCE / --force) - quiet hours and panel yield skipped",
                       "forced")
    if not landing and in_waking_hours(now):
        win = waking_window()
        span = f"{win[0]:%H:%M}-{win[1]:%H:%M}" if win else "?"
        return Verdict(False, f"{name}: harness quiet hours ({span} local) - the household is awake; run it "
                              "outside them, or force it deliberately with ZOE_HARNESS_FORCE=1", "hours")
    quiet = (panel or (lambda: panel_quiet(now)))()
    if not quiet:
        return Verdict(False, f"{name}: the panel was used in the last {PANEL_WINDOW_S // 60} min - yielding to "
                              "the household", "panel")
    return Verdict(True, f"{name}: {'landing gate' if landing else 'outside quiet hours'}, panel quiet")


def brain_window_verdict(name: str, *, budget_min: int = 45, now: Optional[dt.datetime] = None) -> Verdict:
    """May a run of up to ``budget_min`` minutes that starts NOW stay clear of the 01:50-04:55 brain window?

    Refuses a start inside the window, and a start so close to 01:50 that the run could still be alive when the
    window opens (``now + budget`` crosses 01:50). Deliberately NOT bypassed by ``ZOE_HARNESS_FORCE``: that
    override is for the quiet hours (a convenience), whereas colliding with the window loses the night's 12B run
    or clobbers the one voice-gate artifact slot."""
    t0 = now or dt.datetime.now()
    day = t0.replace(hour=0, minute=0, second=0, microsecond=0)
    end = t0 + dt.timedelta(minutes=max(0, int(budget_min)))
    for off in (-1, 0, 1):   # a run may cross midnight into the next day's window
        w_start = day + dt.timedelta(days=off, hours=BRAIN_WINDOW_START.hour, minutes=BRAIN_WINDOW_START.minute)
        w_end = day + dt.timedelta(days=off, hours=BRAIN_WINDOW_END.hour, minutes=BRAIN_WINDOW_END.minute)
        if t0 < w_end and end > w_start:
            what = ("inside" if w_start <= t0 < w_end else f"too close to (a {budget_min}-min run would still be alive at)")
            return Verdict(False, f"{name}: {what} the {BRAIN_WINDOW_START:%H:%M}-{BRAIN_WINDOW_END:%H:%M} brain window "
                                  "(12B night window + voice gate own the box) - start it after 04:55 or early enough "
                                  f"to finish before 01:50", "brain-window")
    return Verdict(True, f"{name}: clear of the {BRAIN_WINDOW_START:%H:%M}-{BRAIN_WINDOW_END:%H:%M} brain window "
                         f"for a {budget_min}-min run")


def lock_wait_s(budget_min: int = 20, *, cap_s: int = 1800, now: Optional[dt.datetime] = None) -> int:
    """How long may a step wait for the shared harness lock and still start in time to finish before 01:50?

    ``min(cap_s, seconds until the brain window opens - the step's budget)``, floored at 0 (0 = try once, never queue).
    Inside the window it is 0. Without this a start before the cutoff could sit in ``flock -w`` for ``cap_s`` and then
    run (or only then be refused) with the wait itself spent inside the window."""
    t0 = now or dt.datetime.now()
    day = t0.replace(hour=0, minute=0, second=0, microsecond=0)
    w_start = day + dt.timedelta(hours=BRAIN_WINDOW_START.hour, minutes=BRAIN_WINDOW_START.minute)
    w_end = day + dt.timedelta(hours=BRAIN_WINDOW_END.hour, minutes=BRAIN_WINDOW_END.minute)
    if w_start <= t0 < w_end:
        return 0
    if t0 >= w_end:
        w_start += dt.timedelta(days=1)
    return int(max(0, min(cap_s, (w_start - t0).total_seconds() - max(0, int(budget_min)) * 60)))


def wait_for_panel_quiet(max_wait_s: float, *, poll_s: float = 60.0, log: Callable[[str], None] = print,
                         panel: Optional[Callable[[], bool]] = None,
                         sleep: Callable[[float], None] = time.sleep) -> bool:
    """Block (bounded) until the panel is quiet. True when quiet, False on timeout. The landing probe's
    between-clip yield: a household turn during a gate run waits out the clip in flight, not the run."""
    check_quiet = panel or panel_quiet
    waited = 0.0
    while not check_quiet():
        if waited >= max_wait_s:
            return False
        log(f"harness_quiet: panel in use - yielding ({int(waited)}s / {int(max_wait_s)}s)")
        sleep(poll_s)
        waited += poll_s
    return True


def deadline_s(*, margin_s: int = 120, now: Optional[dt.datetime] = None) -> int:
    """Hard wall-clock budget, in seconds, for a step that is running NOW: the time until the brain window opens minus a
    teardown margin. 0 when inside the window or with no room left (the caller must refuse, never run unbounded).
    ``brain_window_verdict`` only checks a start against an ESTIMATE; this is the bound the step is actually killed at."""
    t0 = now or dt.datetime.now()
    day = t0.replace(hour=0, minute=0, second=0, microsecond=0)
    w_start = day + dt.timedelta(hours=BRAIN_WINDOW_START.hour, minutes=BRAIN_WINDOW_START.minute)
    w_end = day + dt.timedelta(hours=BRAIN_WINDOW_END.hour, minutes=BRAIN_WINDOW_END.minute)
    if w_start <= t0 < w_end:
        return 0
    if t0 >= w_end:
        w_start += dt.timedelta(days=1)
    return int(max(0, (w_start - t0).total_seconds() - max(0, int(margin_s))))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="may this harness start now? exit 0 yes, 3 no")
    c.add_argument("name")
    c.add_argument("--landing", action="store_true", help="the landing loop's replay probe (exempt from the hours)")
    c.add_argument("--force", action="store_true", help="skip both checks (deliberate)")
    b = sub.add_parser("brain-window", help="may a multi-step run of --budget-min start now? exit 0 yes, 3 no")
    b.add_argument("name")
    b.add_argument("--budget-min", type=int, default=45)
    b.add_argument("--now", help="ISO local time (tests)")
    w = sub.add_parser("brain-window-wait", help="print the max seconds a step may queue for the harness lock")
    w.add_argument("--budget-min", type=int, default=20)
    w.add_argument("--cap-s", type=int, default=1800)
    w.add_argument("--now", help="ISO local time (tests)")
    d = sub.add_parser("brain-window-deadline", help="print the seconds a RUNNING step may live (0 = refuse)")
    d.add_argument("--margin-s", type=int, default=120)
    d.add_argument("--now", help="ISO local time (tests)")
    sub.add_parser("panel-quiet", help="exit 0 when the panel has been quiet 10 min, 1 when in use")
    args = ap.parse_args(argv)
    if args.cmd == "panel-quiet":
        return 0 if panel_quiet() else 1
    if args.cmd == "brain-window-wait":
        print(lock_wait_s(args.budget_min, cap_s=args.cap_s,
                          now=dt.datetime.fromisoformat(args.now) if args.now else None))
        return 0
    if args.cmd == "brain-window-deadline":
        print(deadline_s(margin_s=args.margin_s, now=dt.datetime.fromisoformat(args.now) if args.now else None))
        return 0
    if args.cmd == "brain-window":
        v = brain_window_verdict(args.name, budget_min=args.budget_min,
                                 now=dt.datetime.fromisoformat(args.now) if args.now else None)
        print(v.reason)
        return 0 if v.allowed else EXIT_REFUSED
    v = check(args.name, landing=True if args.landing else None, force=True if args.force else None)
    print(v.reason)
    return 0 if v.allowed else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
