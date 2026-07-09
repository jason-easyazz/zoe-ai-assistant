#!/usr/bin/env python3
"""Measure the per-turn cost of the person_extractor_llm Gemma pass and the
prefilter's skip/false-negative behavior.

Evidence tool for the ZOE_PERSON_LLM_PREFILTER gate (senior-review batch 4,
"the remaining ungated per-turn LLM cost"). DRY by construction: it performs
the same LLM call + JSON parse as ``process_text_llm`` but NEVER invokes
``apply_person_fact`` — nothing is written anywhere. Read-only inference
against the live llama-server (:11434), ~30 short calls total.

Run on the Jetson host:
    python3 scripts/perf/measure_person_extractor.py
    python3 scripts/perf/measure_person_extractor.py --turns-file my_turns.txt

``--turns-file`` (one turn per line) lets the skip-rate be measured on a REAL
turn distribution — e.g. an operator-approved export of recent user turns —
instead of the built-in constructed set (which is FN-focused, not
distribution-representative).

2026-07-06 baseline (built-in set, 30 turns): median 593 ms/call, p90 1.3 s;
prefilter skipped 50% with 0 false negatives.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "services", "zoe-data"))

import httpx  # noqa: E402

from person_extractor_llm import (  # noqa: E402
    _EXTRACTION_PROMPT,
    _EXTRACTION_PROMPT_CONF,
    _MODEL,
    confidence_gate_enabled,
    gemma_base,
    mentions_person,
)

# Measure whatever prompt production would send: the confidence-gated 4-field
# prompt when ZOE_PERSON_LLM_CONFIDENCE_GATE is on, else the 3-field prompt. So
# an operator enabling the dark flag gets latency/format numbers for the actual
# gated prompt, not a stale baseline.
_ACTIVE_PROMPT = _EXTRACTION_PROMPT_CONF if confidence_gate_enabled() else _EXTRACTION_PROMPT

# Constructed set: FN-focused (person turns the gate must never lose, incl.
# sentence-initial names and capitals-that-aren't-people), NOT a real
# distribution — use --turns-file for skip-rate claims.
DEFAULT_TURNS = [
    "what's the weather like today",
    "turn off the kitchen lights please",
    "add bread and milk to the shopping list",
    "set a timer for ten minutes",
    "what time is it",
    "play some relaxing music in the lounge",
    "remind me to water the plants tomorrow morning",
    "how long does chicken take to roast",
    "what's on my calendar this afternoon",
    "thanks that's all for now",
    "actually cancel that reminder",
    "make the volume a bit louder",
    "yes please do that",
    "no i meant the other one",
    "what did i ask you earlier about the oven",
    "my sister Sarah is coming over for dinner on Friday",
    "Tom got promoted to site manager last week",
    "remember that Jess is allergic to peanuts",
    "my dad's birthday is on the twelfth of August",
    "I had lunch with Bob from the Perth office today",
    "Karen and Mike just moved to Geraldton",
    "my daughter started at her new school this week",
    "the neighbour's name is Priya, she offered to feed the cat",
    "my boss wants the report by Monday",
    "Emma passed her driving test yesterday",
    "my mate from footy hurt his knee",
    "the kids want pizza tonight",
    "i told my wife i'd be home by six",
    "book a table at The Windsor for saturday",
    "is the August bank holiday a public holiday in Perth",
]


async def _llm_extract(text: str) -> tuple[float, int]:
    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": "Return ONLY valid JSON arrays."},
            {"role": "user", "content": _ACTIVE_PROMPT.format(text=text[:1200])},
        ],
        "max_tokens": 300,
        "temperature": 0.1,
        "stream": False,
    }
    t = time.monotonic()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{gemma_base()}/v1/chat/completions", json=payload)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
    ms = (time.monotonic() - t) * 1000
    try:
        s, e = raw.find("["), raw.rfind("]") + 1
        items = json.loads(raw[s:e]) if s != -1 and e > s else []
        if not isinstance(items, list):
            items = []
    except (json.JSONDecodeError, ValueError):
        items = []
    return ms, len(items)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns-file", help="one turn per line (real distribution)")
    args = ap.parse_args()

    turns = DEFAULT_TURNS
    if args.turns_file:
        with open(args.turns_file, encoding="utf-8") as f:
            turns = [ln.strip() for ln in f if ln.strip()]

    if not turns:
        print("No turns to measure (empty --turns-file?).")
        return 0

    rows = []
    for text in turns:
        ms, facts = await _llm_extract(text)
        pre = mentions_person(text)
        rows.append({"ms": ms, "facts": facts, "pre": pre, "text": text})
        print(f"[{ms:6.0f}ms] facts={facts} pre={'PASS' if pre else 'skip'}  {text[:60]}")

    lat = sorted(r["ms"] for r in rows)
    skipped = [r for r in rows if not r["pre"]]
    false_neg = [r for r in skipped if r["facts"] > 0]
    print("\n=== SUMMARY ===")
    print(f"calls={len(rows)}  median={statistics.median(lat):.0f}ms  "
          f"p90={lat[int(len(lat) * 0.9)]:.0f}ms  total={sum(lat) / 1000:.1f}s")
    print(f"prefilter skip-rate: {len(skipped)}/{len(rows)} ({100 * len(skipped) / len(rows):.0f}%)")
    print(f"FALSE NEGATIVES (skipped but LLM found facts): {len(false_neg)}")
    for r in false_neg:
        print(f"  !! {r['text']}  → {r['facts']} facts lost")
    return 1 if false_neg else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
