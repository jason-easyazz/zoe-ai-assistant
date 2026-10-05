#!/usr/bin/env python3
"""Summarise barge-in phase-1 lab runs from the voice daemon's log (aggregates only).

    python barge_lab_summary.py ~/.zoe-virtual-panel/voice.log [--tail 2000]

Counts the `BARGE_DECIDE outcome=... ms=... speech_ms=... duck_db=... heard_chunks=...
heard_ms=... (source)` lines and the `Barge-in detected ...` / `duck unavailable` lines
(docs/knowledge/voice-pipeline.md -> "Panel barge-in"). It prints counts and medians,
never transcripts or audio. Run the lab as written in docs/knowledge/mac-virtual-panel.md.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict

_DECIDE = re.compile(
    r"BARGE_DECIDE outcome=(?P<outcome>\w+) ms=(?P<ms>-?\d+) speech_ms=(?P<speech>-?\d+) "
    r"duck_db=(?P<db>-?[\d.]+) heard_chunks=(?P<hc>-?\d+) heard_ms=(?P<hm>-?\d+) \((?P<src>\w+)\)")
_DETECTED = re.compile(r"Barge-in detected during playback")
_UNAVAILABLE = re.compile(r"Barge-in duck unavailable")


def summarise(lines) -> dict:
    by_outcome: dict = defaultdict(list)
    detected = unavailable = 0
    for line in lines:
        m = _DECIDE.search(line)
        if m:
            by_outcome[m["outcome"]].append((int(m["ms"]), int(m["speech"]), int(m["hm"])))
            continue
        if _DETECTED.search(line):
            detected += 1
        elif _UNAVAILABLE.search(line):
            unavailable += 1
    out = {"detected": detected, "duck_unavailable": unavailable, "decisions": sum(len(v) for v in by_outcome.values()),
           "outcomes": {}}
    for name, rows in sorted(by_outcome.items()):
        out["outcomes"][name] = {
            "n": len(rows),
            "median_ms_from_onset": statistics.median(r[0] for r in rows),
            "median_speech_ms": statistics.median(r[1] for r in rows),
            "median_heard_ms": statistics.median(r[2] for r in rows) if name == "commit" else None,
        }
    return out


def format_summary(s: dict) -> str:
    lines = [f"barge detections: {s['detected']}   decisions: {s['decisions']}   "
             f"duck unavailable (hard stop): {s['duck_unavailable']}"]
    for name, o in s["outcomes"].items():
        extra = f"  median heard_ms={o['median_heard_ms']:.0f}" if o["median_heard_ms"] is not None else ""
        lines.append(f"  {name:8s} n={o['n']:<3d} median onset->outcome={o['median_ms_from_onset']:.0f}ms  "
                     f"median speech={o['median_speech_ms']:.0f}ms{extra}")
    if not s["outcomes"]:
        lines.append("  no BARGE_DECIDE lines: is BARGE_DUCK_ENABLED=true in .env.voice, and did a reply play while you spoke?")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--tail", type=int, default=0, help="only the last N lines")
    args = ap.parse_args(argv)
    with open(args.log, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    if args.tail > 0:
        lines = lines[-args.tail:]
    print(format_summary(summarise(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
