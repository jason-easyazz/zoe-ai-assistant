#!/usr/bin/env python3
"""Zoe's CURRENT routing (Tier-0 regex intent_router + Tier-1 embedding
semantic_router) against the same corpus Needle runs — the comparison baseline.

LAB-ONLY, read-only: imports the routing modules from services/zoe-data and
calls their pure classification functions (detect_intent / semantic_router.route)
in-process. No intent is EXECUTED, no service is called, prod is untouched.

Run with the system python3 (the zoe-data runtime env — fastembed + bge-small
are already installed there):
    python3 labs/needle-benchmark/baseline_bench.py --out results/baseline.json

Scoring model (generous to the baseline, documented in README):
  1. Tier-0: detect_intent() regex. ANY intent hit that maps to the expected
     tool counts as a correct route (the live path only short-circuits read
     intents, so this overstates what Tier-0 really rescues).
  2. Tier-1: semantic_router.route() with the live threshold (0.62) + margin
     (0.05). Credited at DOMAIN level: correct if the routed domain matches the
     case's domain label (the real Tier-1.5 dispatch still has to pick the
     operation inside the domain).
  3. Neither → "brain" (the ~4.8s Gemma lane). For a chat-style case, brain is
     the CORRECT outcome; for a tool case it's a coverage miss.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZOE_DATA = HERE.parents[1] / "services" / "zoe-data"

# intent name (intent_router) → flue tool name(s) it fulfils.
INTENT_TO_TOOL = {
    "time_query": ["get_time"], "date_query": ["get_time"],
    "weather": ["get_weather"],
    "list_show": ["show_list"], "list_add": ["add_to_list", "shopping_list_add"],
    "list_remove": ["list_remove"],
    "calendar_show": ["show_calendar"],
    "calendar_create": ["add_calendar_event"], "calendar_add": ["add_calendar_event"],
    "reminder_list": ["list_reminders"], "reminder_create": ["add_reminder"],
    "timer_create": ["set_timer"], "timer_set": ["set_timer"],
    "timer_status": ["set_timer"],
    "note_create": ["create_note"], "note_search": ["note_search"],
    "people_search": ["people"], "people_create": ["people"],
    "music_play": ["media"], "music_control": ["media"],
    "music_volume": ["media"], "set_volume": ["media"], "music_stop": ["media"],
    "smart_home": ["home"], "smart_home_control": ["home"],
    "memory_remember": ["remember_fact"], "memory_recall": ["recall_memory"],
    "journal_create": ["journal"], "people_introduce": ["people"],
    "greeting": ["general_chat"], "acknowledgement": ["general_chat"],
    "thanks": ["general_chat"],
}

# semantic_router domain → the corpus `domain` labels it covers.
ROUTER_DOMAIN_OK = {
    "calendar": {"calendar"}, "lists": {"lists"}, "reminders": {"reminders"},
    "timers": {"timers"}, "weather": {"weather"}, "time": {"time"},
    "people": {"people"}, "memory": {"memory"}, "music": {"music"},
    "smart_home": {"smart_home"}, "chat": {"chat"},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(HERE / "corpus.jsonl"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sys.path.insert(0, str(ZOE_DATA))
    from intent_router import detect_intent
    import semantic_router

    semantic_router.warm()
    cases = [json.loads(l) for l in Path(args.corpus).read_text().splitlines() if l.strip()]

    results, lat = [], []
    for c in cases:
        t0 = time.perf_counter()
        intent = None
        try:
            intent = detect_intent(c["text"], log_miss=False)
        except Exception as e:  # noqa: BLE001
            print(f"detect_intent error on {c['id']}: {e}", file=sys.stderr)
        decision, source, detail = "brain", "none", ""
        if intent is not None and intent.name in INTENT_TO_TOOL:
            source = "tier0-regex"
            detail = intent.name
            mapped = INTENT_TO_TOOL[intent.name]
            decision = mapped[0]
            ok = bool(set(mapped) & set(c["expected"]))
        else:
            rr = semantic_router.route(c["text"])
            scores = rr.get("scores") or {}
            top2 = sorted(scores.values(), reverse=True)[:2]
            ambiguous = len(top2) == 2 and (top2[0] - top2[1]) < 0.05
            routed = rr.get("routed")
            if routed and routed != "chat" and not ambiguous:
                source = "tier1-embed"
                decision = f"domain:{routed}"
                detail = f"score={rr.get('score')}"
                ok = c["domain"] in ROUTER_DOMAIN_OK.get(routed, set())
            else:
                source = "brain"
                decision = "brain"
                ok = c["domain"] == "chat"  # brain is only "right" for chat
        ms = (time.perf_counter() - t0) * 1000
        results.append({**c, "pred": decision, "source": source, "detail": detail,
                        "ok": ok, "ms": round(ms, 2)})
        lat.append(ms)
        print(f"[{'OK  ' if ok else 'MISS'}] {c['id']:14s} {source:12s} -> {decision:22s} "
              f"{ms:.1f}ms", file=sys.stderr)

    def acc(style):
        rows = [r for r in results if style in (None, r["style"])]
        return round(100 * sum(r["ok"] for r in rows) / max(len(rows), 1), 1)

    tool_rows = [r for r in results if r["style"] != "chat"]
    summary = {
        "mode": "baseline-tier0+tier1", "n": len(results),
        "acc_overall": acc(None), "acc_canonical": acc("canonical"),
        "acc_paraphrase": acc("paraphrase"), "acc_chat": acc("chat"),
        "tool_cases_fell_to_brain_pct": round(
            100 * sum(r["source"] == "brain" for r in tool_rows) / max(len(tool_rows), 1), 1),
        "lat_p50_ms": round(statistics.median(lat), 2),
        "lat_p90_ms": round(sorted(lat)[int(0.9 * len(lat))], 2),
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
