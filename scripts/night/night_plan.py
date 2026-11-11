"""The night session's TIME PLANNER (pure; docs/knowledge/night-window.md "One session"): given the household's awake-by deadline and MEASURED history, how many seconds each stage
may take - no fixed reserves, no fixed clock slices.

Order of claims on the time between "now" and ``awake_by``:
  1. WAKE (restore + health checks) and POST (voice probe, Serena restart): their rolling-median wall time + a margin. Always protected - Zoe is back by the deadline.
  2. PRE (backup, memory export) and SLEEP (stops, compaction, 12B load): measured too; they happen before anything optional.
  3. THINK essentials - digest, self-review, the day review (it reads the clean outputs, so it goes last): their estimated time, protected.
  4. LISTEN (the Omi re-pass): everything left after the optional THINK jobs have taken THEIR estimates, down to a floor; the re-pass's own voters fill what it is given.
  5. THINK optionals (dreaming, night mind, struggle proposals, VAGO extract, conversation notes, clean read): in priority order, each only if its estimate still fits; what does not fit
     carries to tomorrow and is REPORTED, never dropped silently.
``history`` is ``{stage_or_job: [wall seconds, newest last]}`` (``median`` of the last ``NIGHTS``); a stage with no history uses ``DEFAULT_S``.
"""
from __future__ import annotations

import statistics
from typing import Iterable, Optional

NIGHTS = 7
MARGIN = 1.25
LISTEN_FLOOR_S = 600.0
ESSENTIAL = ("digest", "self_review", "omi_day_review")
OPTIONAL_ORDER = ("dreaming", "night_mind", "struggle_proposals", "vago_extract", "conversation_notes", "omi_cleanread")
FIXED_STAGES = ("pre", "sleep", "wake", "post")
DEFAULT_S = {"pre": 1200.0, "sleep": 420.0, "wake": 420.0, "post": 900.0, "digest": 300.0, "self_review": 300.0, "omi_day_review": 600.0, "dreaming": 300.0, "night_mind": 300.0,
             "struggle_proposals": 120.0, "vago_extract": 300.0, "conversation_notes": 240.0, "omi_cleanread": 300.0}


def est(history: dict, name: str) -> float:
    """Rolling median of the last ``NIGHTS`` wall times of ``name`` x ``MARGIN``; the default when there is no history."""
    xs = [float(x) for x in (history.get(name) or [])][-NIGHTS:]
    return (statistics.median(xs) if xs else DEFAULT_S.get(name, 300.0)) * MARGIN


def plan(available_s: float, history: dict, *, present: Optional[Iterable[str]] = None) -> dict:
    """-> {"stage": {name: seconds}, "listen_s": float, "carried": [job names], "fits": bool, "short_s": float}.
    ``available_s`` = seconds from the session start to ``awake_by``. ``present``: the optional jobs that exist this night (default: all)."""
    have = set(present) if present is not None else set(OPTIONAL_ORDER)
    stage = {n: est(history, n) for n in FIXED_STAGES}
    protected = sum(stage.values())
    ess = {n: est(history, n) for n in ESSENTIAL}
    stage.update(ess)
    protected += sum(ess.values())
    left = available_s - protected
    carried: list = []
    for n in OPTIONAL_ORDER:
        if n not in have:
            continue
        e = est(history, n)
        if left - e >= LISTEN_FLOOR_S:
            stage[n] = e
            left -= e
        else:
            carried.append(n)
    listen = max(0.0, left)
    fits = left >= LISTEN_FLOOR_S or (left >= 0 and not carried)
    return {"stage": stage, "listen_s": round(listen, 1), "carried": carried, "fits": fits, "short_s": round(max(0.0, -left), 1)}
