#!/usr/bin/env python3
"""turn_taking_metrics.py - cut-offs and over-waits per 100 turns, from the panel daemon log (Initiative section 9, lane E 6.2).

Reads ``voice.log`` (the zoe-voice daemon's own file on the panel Pi; one line per event, format
``%(asctime)s [voice-daemon] %(levelname)s %(message)s``, see ``scripts/setup/zoe_voice_daemon.py``) and COUNTS a fixed set of
event lines. It reads no transcript, no audio and no speaker data: the lines it matches carry durations and rule names only, and the
output is integers and rates. Pass a file path (or ``-`` for stdin); the script never opens the panel or the network itself.

  turns       "Recorded command:" and "Recorded follow-up:" lines, minus "Command too short" / "Follow-up too short" (a turn that
              never reached the brain) and minus a "Recorded follow-up:" that is a CONTINUATION clip (it follows a "Turn held:" or a
              hard-cap line and is merged into the turn already counted)
  cut-offs    the member was still talking when the turn closed on them:
                "Continuation merged:"                          they resumed inside the hold window (caught, but the endpoint was early)
                "Recording ended on the hard cap mid-speech"    the recorder cap closed a speaker mid-sentence
  over-waits  the panel waited and nothing came:
                "No continuation - sending the held clip"       held the turn for a continuation window the member did not use

These are PROXIES counted from daemon events, not labelled truth (a held turn that did get a continuation is both a cut-off and a
good save). The target is < 5 cut-offs and < 5 over-waits per 100 turns; the Smart Turn offline corpus test (lane E E1) is the labelled
measure. A rate is ``null`` with zero turns - never 0, which would read as "measured: none".

    turn_taking_metrics.py voice.log [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--json]

Exit 0 always on a readable file (it reports); 2 on an unreadable one. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Iterable

_LINE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}(?:[,.]\d+)? \[voice-daemon\] (?P<lvl>[A-Z]+) (?P<msg>.*)$")

TURN_PATTERNS = ("Recorded command:", "Recorded follow-up:")
NOT_A_TURN = ("Command too short", "Follow-up too short")
CUTOFF_PATTERNS = ("Continuation merged:", "Recording ended on the hard cap mid-speech")
OVERWAIT_PATTERNS = ("No continuation - sending the held clip",)
# The daemon logs one of these, THEN listens for the rest of the turn: a "Recorded follow-up:" that follows is the CONTINUATION clip
# of the turn already counted (it is merged into it), never a turn of its own.
CONTINUATION_LISTEN = ("Turn held:", "Recording ended on the hard cap mid-speech")

TARGETS = {"cutoffs_per_100_max": 5.0, "overwaits_per_100_max": 5.0}


def count_events(lines: Iterable[str], since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Count the event lines. Lines that do not match the daemon format (tracebacks, journal noise) are ignored and counted."""
    turns = cutoffs = overwaits = unparsed = 0
    pending = False   # a counted "Recorded ..." line whose "too short" verdict has not been seen yet
    awaiting_continuation = False   # the daemon is listening for the rest of a turn: the next "Recorded follow-up:" is that clip
    days: set[str] = set()
    for raw in lines:
        m = _LINE.match(raw.rstrip("\n"))
        if not m:
            if raw.strip():
                unparsed += 1
            continue
        day, msg = m["day"], m["msg"]
        if (since and day < since) or (until and day > until):
            continue
        if msg.startswith("Turn held:"):
            awaiting_continuation = True
            continue
        if awaiting_continuation and msg.startswith("Recorded follow-up:"):
            awaiting_continuation = False       # a merged continuation clip: not a second turn
            pending = False
            days.add(day)
            continue
        if msg.startswith(NOT_A_TURN):
            awaiting_continuation = False
            # Only cancels the "Recorded ..." line just counted. An unmatched one (rotated log, --since boundary) cancels nothing.
            if pending:
                turns -= 1
            pending = False
            continue
        if msg.startswith(TURN_PATTERNS):
            awaiting_continuation = False
            turns += 1
            pending = True
            days.add(day)
        elif msg.startswith(CUTOFF_PATTERNS):
            cutoffs += 1
            if msg.startswith(CONTINUATION_LISTEN[1]):
                awaiting_continuation = True
        elif msg.startswith(OVERWAIT_PATTERNS):
            overwaits += 1
            awaiting_continuation = False
    turns = max(turns, 0)

    def per100(n: int) -> float | None:
        return round(100.0 * n / turns, 2) if turns else None

    c, o = per100(cutoffs), per100(overwaits)
    return {"turns": turns, "days": len(days), "cutoffs": cutoffs, "overwaits": overwaits,
            "cutoffs_per_100": c, "overwaits_per_100": o, "unparsed_lines": unparsed, "targets": TARGETS,
            "meets_target": (None if c is None or o is None else
                             c < TARGETS["cutoffs_per_100_max"] and o < TARGETS["overwaits_per_100_max"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", help="voice.log path, or - for stdin")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.log == "-":
            res = count_events(sys.stdin, args.since, args.until)
        else:
            with open(args.log, encoding="utf-8", errors="replace") as fh:
                res = count_events(fh, args.since, args.until)
    except OSError as exc:
        print(f"unreadable: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"turns={res['turns']} days={res['days']} cutoffs={res['cutoffs']} ({res['cutoffs_per_100']}/100) "
              f"overwaits={res['overwaits']} ({res['overwaits_per_100']}/100) unparsed={res['unparsed_lines']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
