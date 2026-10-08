#!/usr/bin/env python3
"""A BOUNDED live check of the structural floors' off-path yes/no verifier (<= 40 calls, synthetic rows only).

``services/zoe-data/structural_verifier.py`` asks the live 4B (llama-server, ONE slot) a constrained ``root ::= "yes" | "no"``
question. This script measures it on rows of the labelled set (``tests/fixtures/structural_floors_labelled_set.json``, synthetic names):
latency p50 / p95, balanced accuracy against the labels, and how often it agrees with the structural floor.

LIVE-BRAIN RULE (the operator's): requests go to the live brain ONLY while this process holds ``flock /tmp/zoe-voice-harness.lock``
and never more than ``MAX_CALLS`` (40) - the cap is a constant, ``--calls`` can only lower it. Calls are serial. Nothing is written to any
store, the environment is never printed, no row of a real user is read.

    python3 scripts/perf/structural_verifier_live_check.py [--calls 40] [--url http://127.0.0.1:11434]

Prints one JSON object (counts, latencies, accuracy); exit 0 on a completed run, 2 when the lock cannot be taken, 3 on a dead brain.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

LOCK = "/tmp/zoe-voice-harness.lock"
MAX_CALLS = 40
FIXTURE = ROOT / "services" / "zoe-data" / "tests" / "fixtures" / "structural_floors_labelled_set.json"
#: (group, how many rows): a stratified, DETERMINISTIC draw - half label-True, half label-False inside each group where it can
PLAN = (("ledger", 8), ("heldout_en", 12), ("es", 4), ("fr", 4), ("de", 4), ("zh", 4), ("ja", 4))


def _group(item: dict) -> str:
    return item["lang"] if item["split"] == "xling" else item["split"]


def select_items(items: list[dict] | None = None, limit: int = MAX_CALLS) -> list[dict]:
    """The rows to ask about: at most ``min(limit, MAX_CALLS)``, support rows only, a fixed draw (sorted by id, alternating label)."""
    if items is None:
        items = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]
    cap = min(int(limit), MAX_CALLS)
    out: list[dict] = []
    for group, n in PLAN:
        rows = sorted((i for i in items if i["task"] == "support" and _group(i) == group), key=lambda i: i["id"])
        pos, neg = [r for r in rows if r["label"]], [r for r in rows if not r["label"]]
        pick: list[dict] = []
        while len(pick) < n and (pos or neg):
            for pool in (pos, neg):
                if pool and len(pick) < n:
                    pick.append(pool.pop(0))
        out += pick
    return out[:cap]


def take_lock(timeout_s: float = 60.0):
    fd = open(LOCK, "a")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() > deadline:
                fd.close()
                return None
            time.sleep(1.0)


async def run(rows: list[dict], url: str) -> dict:
    import structural_verifier as sv

    lat, verdicts = [], []
    for r in rows:
        t0 = time.monotonic()
        try:
            v = await sv.judge(r["fact"], r["said"], url=url, timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            v = None
            print(f"call failed: {type(exc).__name__}", file=sys.stderr)
        lat.append((time.monotonic() - t0) * 1000)
        verdicts.append(v)
    return {"verdicts": verdicts, "lat_ms": lat}


def summarise(rows: list[dict], got: dict) -> dict:
    verdicts, lat = got["verdicts"], got["lat_ms"]
    per: dict = {}
    tp = tn = fp = fn = unparsed = 0
    for r, v in zip(rows, verdicts):
        g = per.setdefault(_group(r), {"n": 0, "fp": 0, "fn": 0, "unparsed": 0})
        g["n"] += 1
        if v is None:
            unparsed += 1
            g["unparsed"] += 1
            continue
        said_yes = v == "yes"
        if r["label"] and said_yes:
            tp += 1
        elif r["label"] and not said_yes:
            fn += 1
            g["fn"] += 1
        elif not r["label"] and said_yes:
            fp += 1
            g["fp"] += 1
        else:
            tn += 1
    pos, neg = tp + fn, tn + fp
    ba = ((tp / pos if pos else 1.0) + (tn / neg if neg else 1.0)) / 2
    lat_sorted = sorted(lat)
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(0.95 * len(lat_sorted)))] if lat_sorted else None
    return {"calls": len(rows), "unparsed": unparsed, "tp": tp, "tn": tn, "fp": fp, "fn": fn, "balanced_accuracy": round(ba, 3),
            "latency_ms": {"p50": round(statistics.median(lat), 1) if lat else None, "p95": round(p95, 1) if p95 is not None else None,
                           "max": round(max(lat), 1) if lat else None},
            "by_group": per}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--calls", type=int, default=MAX_CALLS, help=f"at most {MAX_CALLS}; a larger value is clamped")
    ap.add_argument("--url", default=None, help="the brain's base URL (default: memory_digest's GEMMA_SERVER_URL resolution)")
    args = ap.parse_args()
    rows = select_items(limit=max(1, args.calls))
    url = args.url
    if url is None:
        from memory_digest import _GEMMA_URL

        url = _GEMMA_URL
    lock = take_lock()
    if lock is None:
        print(json.dumps({"error": "could not take " + LOCK}))
        return 2
    try:
        got = asyncio.run(run(rows, url))
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    if all(v is None for v in got["verdicts"]):
        print(json.dumps({"error": "no usable answer from the brain", "calls": len(rows)}))
        return 3
    print(json.dumps(summarise(rows, got), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
