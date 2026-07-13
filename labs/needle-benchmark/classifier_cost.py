#!/usr/bin/env python3
"""Q3 — what does Zoe's current pre-brain classifier chain actually COST per turn?

LAB-ONLY measurement, run by hand with the system python3. It times the three
classification stages that can run in front of the Gemma brain on a chat turn:

  1. Tier-0   intent_router.detect_intent      — pure regex, in-process
  2. Tier-1   semantic_router.route            — bge-small ONNX embed, in-process
  3. Tier-0.5 intent_classifier_llm.classify   — a JSON completion against the
     LIVE llama-server (:11434, the voice brain). This stage only fires for
     short utterances the regex missed, and its cost IS the live brain doing a
     classification prefill — that measurement is the subject of this spike.
     We send a handful (default 8) of one-line prompts; each is a few hundred
     prefill tokens + ~30 decode tokens, far lighter than one real brain turn.
     Skip it entirely with --no-llm (e.g. during a quiet-window freeze).

pi_intent_classifier (the Pi-subprocess governor) is NOT timed live — spawning
a second Pi runtime on this box risks the RAM budget. Its code-declared budget
is reported instead (PI_INTENT_DEFAULT_TIMEOUT_S = 4.0s, subprocess spawn).
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
ZOE_DATA = HERE.parents[1] / "services" / "zoe-data"

UTTERANCES = [
    "what time is it",
    "add milk to my shopping list",
    "do I need a jacket if I go out later",
    "chuck on something chill while I cook",
    "we're out of laundry powder again",
    "turn off the kitchen lights",
    "give us a nudge about the dentist tomorrow",
    "tell me something interesting about octopuses",
]


def pct(v, q):
    return round(sorted(v)[min(len(v) - 1, int(q * len(v)))], 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true",
                    help="skip the live llama-server Tier-0.5 timing")
    ap.add_argument("--llm-n", type=int, default=8)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sys.path.insert(0, str(ZOE_DATA))
    from intent_router import detect_intent
    import semantic_router

    out: dict = {}

    # 1. Tier-0 regex
    detect_intent("warmup", log_miss=False)
    t = []
    for u in UTTERANCES * 5:
        t0 = time.perf_counter()
        detect_intent(u, log_miss=False)
        t.append((time.perf_counter() - t0) * 1000)
    out["tier0_regex_ms"] = {"p50": pct(t, 0.5), "p90": pct(t, 0.9)}

    # 2. Tier-1 embedding router
    semantic_router.warm()
    t = []
    for u in UTTERANCES * 5:
        t0 = time.perf_counter()
        semantic_router.route(u)
        t.append((time.perf_counter() - t0) * 1000)
    out["tier1_embed_ms"] = {"p50": pct(t, 0.5), "p90": pct(t, 0.9)}

    # 3. Tier-0.5 LLM classifier (live llama-server) — the expensive stage.
    if not args.no_llm:
        from intent_classifier_llm import classify_intent_with_context

        async def run_llm():
            times, oks = [], 0
            for u in UTTERANCES[: args.llm_n]:
                t0 = time.perf_counter()
                try:
                    r = await classify_intent_with_context(u, context=None)
                    oks += r is not None
                except Exception as e:  # noqa: BLE001
                    print(f"tier0.5 error: {e}", file=sys.stderr)
                times.append((time.perf_counter() - t0) * 1000)
            return times, oks

        t, oks = asyncio.run(run_llm())
        out["tier05_llm_ms"] = {"p50": pct(t, 0.5), "p90": pct(t, 0.9),
                                "n": len(t), "non_null": oks,
                                "note": "live llama-server :11434 JSON completion"}

    out["pi_governor_declared"] = {
        "timeout_s": 4.0, "max_words": 32,
        "note": "pi_intent_classifier subprocess governor — NOT timed live "
                "(RAM); values read from code (PI_INTENT_DEFAULT_TIMEOUT_S)",
    }
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
