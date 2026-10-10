#!/usr/bin/env python3
"""Brain prefix-warm probe: would a cache_prompt warm request at speculative turn start save time on llama-server?

Arms (fresh random prefixes per rep, order rotated, server-side ``timings`` read back, no household text):
  hit  prime A; A + words.   swap  prime A, prime B (slot now B); A + words.   warm  swap, then warm A, wait, A + words.
  warm_late  the real request arrives while the warm one is still running (parallel 1 queues it: the downside).
Result and verdict: docs/knowledge/prefill-under-speech-2026-10-10.md.
    flock -w 1800 /tmp/zoe-voice-harness.lock python3 scripts/perf/measure_brain_prefix_warm.py --reps 8 --json out.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
import urllib.request

WORDS = ("table river window paper garden engine silver planet orange bridge candle forest letter mirror pocket "
         "ladder anchor basket castle desert feather island jacket kitchen lantern meadow needle orchard pillow "
         "quarter ribbon saddle tunnel valley wagon yellow zipper blanket cabinet dolphin elbow fountain glacier").split()
ARMS = ("hit", "swap", "warm", "warm_late")


def post(url: str, body: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    out["_wall_s"] = time.monotonic() - t
    return out


def prefix(rng: random.Random, n_words: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n_words)) + "\n"


def req(base: str, text: str) -> dict:
    return post(base + "/completion", {"prompt": text, "n_predict": 1, "cache_prompt": True, "temperature": 0.0})


def row(o: dict) -> dict:
    t = o.get("timings", {})
    return {"wall_s": round(o["_wall_s"], 3), "prompt_n": t.get("prompt_n"), "cache_n": t.get("cache_n"),
            "prompt_ms": round(t.get("prompt_ms", 0.0), 1)}


def run_arm(base: str, arm: str, rng: random.Random, n_words: int, speech_s: float) -> dict:
    a, b = prefix(rng, n_words), prefix(rng, n_words)
    words = " ".join(rng.choice(WORDS) for _ in range(12))
    req(base, a)                       # prime A
    if arm != "hit":
        req(base, b)                   # the slot now holds B
    out = {}
    if arm == "warm":
        out["warm"] = row(req(base, a))
        time.sleep(speech_s)
    elif arm == "warm_late":
        holder = {}
        th = threading.Thread(target=lambda: holder.update(w=req(base, a)))
        th.start()
        time.sleep(0.05)               # the real turn arrives while the warm request is mid-flight
        out["real"] = row(req(base, a + words))
        th.join()
        out["warm"] = row(holder["w"])
        return out
    out["real"] = row(req(base, a + words))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:11434")
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--words", type=int, default=900, help="prefix size in words (~1.3 tokens each)")
    ap.add_argument("--speech-s", type=float, default=2.5)
    ap.add_argument("--json")
    args = ap.parse_args()
    rng = random.Random(1010)
    health = json.loads(urllib.request.urlopen(args.base + "/health", timeout=5).read())
    if health.get("status") != "ok":
        print("brain not healthy", file=sys.stderr)
        return 2
    run_arm(args.base, "swap", rng, args.words, 0.0)  # warm-up, discarded
    rows = []
    for k in range(args.reps):
        order = ARMS[k % 4:] + ARMS[:k % 4]
        for arm in order:
            r = run_arm(args.base, arm, rng, args.words, args.speech_s)
            r["arm"], r["rep"] = arm, k
            rows.append(r)
            print(json.dumps(r), flush=True)
    res = {"reps": args.reps, "prefix_words": args.words, "speech_s": args.speech_s}
    for arm in ARMS:
        sel = [r for r in rows if r["arm"] == arm]
        w = [r["real"]["wall_s"] for r in sel]
        res[arm] = {"real_wall_s_median": round(statistics.median(w), 3), "min": min(w), "max": max(w),
                    "real_prompt_n_median": statistics.median(r["real"]["prompt_n"] for r in sel),
                    "real_cache_n_median": statistics.median(r["real"]["cache_n"] for r in sel)}
    print(json.dumps(res, indent=1))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"summary": res, "rows": rows}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
