#!/usr/bin/env python3
"""recall_every_turn_replay.py - OFFLINE measurement of ZOE_RECALL_EVERY_TURN (recall_every_turn.py): what it adds, what it holds back,
and what it costs.

Questions: (1) on household-shaped turns the trigger-gated recall floor does NOT claim, does the every-turn gate bring the row the
turn is about, and does it stay silent on turns no row bears on? (2) on the Samantha-bar / day-sim asks, what would ride beside the
floor's packet? (3) the per-turn wall time (cached-index score + the by-id recheck of the picks) at store sizes up to 1,000 rows, and the cold
index build (the voice-path budget is <= ~50 ms p50 per turn).

HERMETIC, like recall_gate_replay.py (whose world, rows and store it reuses): a throwaway MemPalace directory, the real
``MemoryService``, real ONNX embeddings on ingest, synthetic ``demo_bar_<8 hex>`` users only, no brain, no network, no live service or
database. The one liberty: the synthetic ids are TREATED as household members here (``traffic_class.class_for_user`` is patched in
this process only), because the module refuses every synthetic id by design.

Usage:
    /home/zoe/.zoe/venvs/zoe-data-py312/bin/python scripts/perf/recall_every_turn_replay.py [--sizes 100,400,1000] [--reps 40]
Exit: 0 ran | 2 error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import recall_gate_replay as rgr  # noqa: E402  (pins nothing at import; sets sys.path to zoe-data)

# Household-shaped turns the trigger floor does not claim: commands and chit-chat that NAME something a row holds (needle), and the
# controls no stored row bears on (needle None: nothing may ride).
AMBIENT = [
    ("Add dog food for Juniper to the shopping list", "juniper"),
    ("Set a reminder for the Kestrel standup at nine", "kestrel"),
    ("Play something upbeat for the City to Surf training", "city to surf"),
    ("Remind me to ring Ingrid tonight", None),              # the mum row is a health row: held back by design
    ("Set a timer for ten minutes", None),
    ("What's the weather like tomorrow?", None),
    ("Turn on the kitchen lights", None),
    ("Good morning", None),
    ("Play some jazz", None),
    ("What's the capital of Peru?", None),
    ("Add milk to the shopping list", None),
    ("How long do I boil an egg?", None),
    ("Thanks, that's all", None),
    ("Tell me a joke", None),
]
FAMILY = [
    ("Text Marisol that I'll be at the airport", "marisol"),
    ("What time does the Lisbon flight usually land?", "lisbon"),
    ("Teodor's coming over for lunch on Sunday", "teodor"),
    ("Set a timer for the pasta", None),
]


def _patch_world():
    import memory_forgotten
    import restraint
    import traffic_class

    real = traffic_class.class_for_user
    traffic_class.class_for_user = lambda uid: None if rgr.DEMO.match(str(uid or "")) else real(uid)

    async def no_mutes(_uid):
        return []

    restraint.list_mutes = no_mutes

    async def ledger_empty(_uid, _text, **_k):       # no forget ledger in a temp world (its table is Postgres)
        return False

    memory_forgotten.matches = ledger_empty


async def _turns(svc, user, turns, rt, zc):
    out = []
    for msg, needle in turns:
        t0 = time.perf_counter()
        sel = await rt.select(user, msg, svc=svc)
        ms = (time.perf_counter() - t0) * 1000.0
        text = " ".join(sel.texts).lower()
        out.append({"ask": msg, "floor_shape": zc._recall_floor_shape(msg) or "-", "rows": len(sel.ids), "tokens": sel.tokens,
                    "ms": round(ms, 1), "skipped": sel.skipped or "-", "needle": needle,
                    "ok": (needle in text) if needle else (not sel.ids)})
    return out


async def accuracy():
    import recall_every_turn as rt
    import zoe_flue_client as zc

    cases = {c["id"]: c for c in rgr.build_cases()}
    week = cases["D-mum"]["rows"]
    results = []
    user = rgr.demo_user("ET-ambient", rgr.A)
    svc, _ = await rgr.build_store(week, user)
    results += [dict(r, set="ambient") for r in await _turns(svc, user, AMBIENT, rt, zc)]
    user = rgr.demo_user("ET-family", rgr.A)
    svc, _ = await rgr.build_store(cases["S8"]["rows"], user)
    results += [dict(r, set="family") for r in await _turns(svc, user, FAMILY, rt, zc)]
    for cid, case in cases.items():           # the bar asks: what would ride (the floor claims most of them; then nothing rides)
        if case["surface"] != "packet" or case.get("rows_user"):
            continue
        user = rgr.demo_user("ET-" + cid, case["user"])
        svc, _ = await rgr.build_store(case["rows"], user)
        for msg in case["turns"]:
            r = (await _turns(svc, user, [(msg, None)], rt, zc))[0]
            r.update(set="bar:" + cid, needle="-", ok=None)
            results.append(r)
    return results


async def latency(sizes, reps):
    import recall_every_turn as rt

    cases = {c["id"]: c for c in rgr.build_cases()}
    week = cases["D-mum"]["rows"]
    words = ("garden", "bike", "pantry", "garage", "library", "choir", "bakery", "ferry", "museum", "orchard", "market", "studio")
    out = []
    for n in sizes:
        filler = [(f"User mentioned the {words[i % len(words)]} visit number {i} with a neighbour on a weekday.", "approved", "chat_regex")
                  for i in range(max(0, n - len(week)))]
        user = rgr.demo_user(f"ET-lat-{n}", rgr.A)
        svc, _ = await rgr.build_store(week + filler, user)
        pool = len(await svc.load_durable_for_hop(user))
        rt._reset_state()
        t0 = time.perf_counter()
        await rt._build(user, svc)          # the cold index build (off the hot path after the first turn; bounded on it)
        out.append({"rows": n, "pool": pool, "turn": "index build (cold)", "p50_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                    "p95_ms": "-", "max_ms": "-"})
        for label, msg in (("match", "Add dog food for Juniper to the shopping list"), ("no-match", "Set a timer for ten minutes")):
            ts = []
            for _ in range(reps):
                t0 = time.perf_counter()
                await rt.select(user, msg, svc=svc)
                ts.append((time.perf_counter() - t0) * 1000.0)
            ts.sort()
            out.append({"rows": n, "pool": pool, "turn": label, "p50_ms": round(statistics.median(ts), 1),
                        "p95_ms": round(ts[int(len(ts) * 0.95) - 1], 1), "max_ms": round(ts[-1], 1)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sizes", default="100,400,1000")
    ap.add_argument("--reps", type=int, default=40)
    ap.add_argument("--json", default=str(Path.home() / ".cache" / "zoe" / "recall_every_turn_replay_last.json"))
    args = ap.parse_args(argv)
    try:
        with rgr.pinned_env(), rgr.scoped_env(ZOE_RECALL_EVERY_TURN="on", ZOE_RECALL_PRESENT_STATE_SHAPES="1",
                                              ZOE_OWN_FACT_PRECEDENCE="1", ZOE_SEAM_RECALL_INJECT="1"):
            import exact_words
            import night_store
            import struggle_detector

            exact_words.set_backend(exact_words.MemoryBackend())
            night_store.set_backend(night_store.MemoryBackend())
            struggle_detector.set_backend(struggle_detector.MemoryBackend())
            _patch_world()
            acc = asyncio.run(accuracy())
            lat = asyncio.run(latency([int(x) for x in args.sizes.split(",") if x.strip()], args.reps))
    except Exception as exc:  # noqa: BLE001
        print(f"replay failed: {exc!r}", file=sys.stderr)
        return 2
    print("| set | ask | floor claims | every-turn rows | tokens | ms | expected | ok |")
    print("|---|---|---|---|---|---|---|---|")
    for r in acc:
        ok = "-" if r["ok"] is None else ("yes" if r["ok"] else "NO")
        print(f"| {r['set']} | {r['ask'][:52]} | {r['floor_shape']} | {r['rows']} | {r['tokens']} | {r['ms']} | {r['needle'] or 'nothing'} | {ok} |")
    print()
    print("| store rows | durable pool | turn | p50 ms | p95 ms | max ms |")
    print("|---|---|---|---|---|---|")
    for r in lat:
        print(f"| {r['rows']} | {r['pool']} | {r['turn']} | {r['p50_ms']} | {r['p95_ms']} | {r['max_ms']} |")
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps({"accuracy": acc, "latency": lat}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
