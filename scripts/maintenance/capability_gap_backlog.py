#!/usr/bin/env python3
"""Rank Zoe's "I can't do that" moments into a capability backlog (Samantha initiative item 14, lane B E3).

    capability_gap_backlog.py [--days 14] [--out PATH] [--public-out PATH] [--top 30]

Reads LOGS ONLY - never the database: the service log (+ rotated segments), the router shadow log, ``intent-misses.jsonl``,
the nightly self-review's counts-only summaries (``~/.zoe/self-review/``, mode ``on`` nights only), and (to read hash-only log
lines back into words, privately) the local STT transcript log. Writes:

  * the PRIVATE ranked report (examples and member ids; mode 0600) to ``--out``
    (default ``~/.zoe/capability-gap/backlog-<date>.md``) - never commit it;
  * with ``--public-out``, an AGGREGATE-ONLY summary (fixed labels + counts) that is safe for the public repo.

Read-only on every source; safe to run at any time of day (one pass over ~100 MB of logs, no network, no model). It is NOT
scheduled - an owner step. Clustering and ranking live in ``services/zoe-data/capability_gap.py`` (stdlib, tested).
Exit 2 = no source could be read.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

import capability_gap as cg  # noqa: E402

HOME = Path.home()
DEFAULT_APP_LOG = HOME / ".zoe-logs" / "zoe-data.app.log"
DEFAULT_SHADOW = REPO / "services" / "zoe-data" / "data" / "router_head_shadow.jsonl"
DEFAULT_MISSES = HOME / "training" / "data" / "intent-misses.jsonl"
DEFAULT_STT = HOME / ".zoe-voice" / "voice_stt.jsonl"
DEFAULT_SELF_REVIEW = HOME / ".zoe" / "self-review"


def _lines(path: str):
    """The file's lines, or ``None`` when it could not be opened/read (a directory, a permission error): "exists" is not "read"."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return None


def _segments(base: Path) -> list:
    """``base`` plus its numeric rotations (``.1`` ... ``.N``); plain text only."""
    out = [str(base)] if base.exists() else []
    out += sorted(p for p in glob.glob(str(base) + ".*") if p.rsplit(".", 1)[-1].isdigit())
    return out


def collect(app_log: Path, shadow: Path, misses: Path, stt: Path, self_review: Path = DEFAULT_SELF_REVIEW) -> tuple:
    """(events, resolver, notes, readable). Missing sources are reported in notes, not fatal. ``readable`` counts sources
    actually OPENED and read - an existing but unreadable file is a failure note, not a source."""
    events, notes, readable = [], [], 0

    def read(path: str, label: str, counts: bool = True):
        nonlocal readable
        lines = _lines(path)
        if lines is None:
            notes.append(f"{label}: could not be read (unreadable) - skipped")
            return []
        readable += 1 if counts else 0          # the STT log only resolves hashes: it is not an event source
        return lines

    for seg in _segments(app_log):
        n0 = len(events)
        for line in read(seg, f"service log {os.path.basename(seg)}"):
            if "intent_miss" in line or "STRUGGLE " in line or "WRITE_CLAIM" in line or "EXPERT_" in line or "no extractor" in line:
                ev = cg.parse_log_line(line)
                if ev:
                    events.append(ev)
        notes.append(f"service log {os.path.basename(seg)}: {len(events) - n0} events")
    for seg in _segments(shadow):
        n0 = len(events)
        for line in read(seg, f"router shadow {os.path.basename(seg)}"):
            try:
                ev = cg.parse_shadow_record(json.loads(line))
            except ValueError:
                continue
            if ev:
                events.append(ev)
        notes.append(f"router shadow {os.path.basename(seg)}: {len(events) - n0} events")
    n0 = len(events)
    for line in (read(str(misses), "intent-misses.jsonl") if misses.exists() else []):
        try:
            ev = cg.parse_miss_record(json.loads(line))
        except ValueError:
            continue
        if ev:
            events.append(ev)
    notes.append(f"intent-misses.jsonl: {len(events) - n0} events")
    n0 = len(events)
    for p in sorted(glob.glob(str(self_review / "*.self-review-summary.json"))):
        lines = read(p, f"self-review {os.path.basename(p)}")
        try:
            events.extend(cg.parse_self_review_summary(json.loads("".join(lines))))
        except ValueError:
            notes.append(f"self-review {os.path.basename(p)}: not JSON - skipped")
    notes.append(f"self-review summaries: {len(events) - n0} events")
    transcripts = []
    for line in (read(str(stt), "voice_stt.jsonl", counts=False) if stt.exists() else []):
        try:
            t = json.loads(line).get("transcript")
        except (ValueError, AttributeError):
            continue
        if isinstance(t, str):
            transcripts.append(t)
    resolver = cg.build_resolver(transcripts)
    notes.append(f"hash resolver: {len(resolver)} transcript hashes from the local STT log")
    return events, resolver, notes, readable


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", type=Path, default=None, help="private report path (default ~/.zoe/capability-gap/backlog-<date>.md)")
    ap.add_argument("--public-out", type=Path, default=None, help="write the aggregate-only summary here")
    ap.add_argument("--app-log", type=Path, default=DEFAULT_APP_LOG)
    ap.add_argument("--shadow-log", type=Path, default=DEFAULT_SHADOW)
    ap.add_argument("--misses", type=Path, default=DEFAULT_MISSES)
    ap.add_argument("--stt-log", type=Path, default=DEFAULT_STT)
    ap.add_argument("--self-review-dir", type=Path, default=DEFAULT_SELF_REVIEW)
    a = ap.parse_args(argv)

    events, resolver, notes, readable = collect(a.app_log, a.shadow_log, a.misses, a.stt_log, a.self_review_dir)
    if not readable:
        print("capability_gap_backlog: no source could be read", file=sys.stderr)
        return 2
    now = time.time()
    ranked = cg.cluster_events(events, now=now, days=a.days, resolver=resolver)
    notes.append(f"events collected {len(events)}; in window after de-duplication {sum(c.count for c in ranked)}")

    out = a.out or (HOME / ".zoe" / "capability-gap" / f"backlog-{datetime.now():%Y-%m-%d}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)          # O_CREAT mode does not apply to a pre-existing (e.g. 0644) file
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(cg.private_report(ranked, now=now, days=a.days, top=a.top, notes=notes))
    summary = cg.public_summary(ranked, now=now, days=a.days)
    if a.public_out:
        a.public_out.parent.mkdir(parents=True, exist_ok=True)
        a.public_out.write_text(summary, encoding="utf-8")
    print(f"private report: {out}")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
