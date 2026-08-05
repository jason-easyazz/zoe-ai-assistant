#!/usr/bin/env python3
"""LIVE demo of the free-only spike. Hand-run; read-only outbound requests.

    python3 labs/web-search-spike/demo.py                  # scripted set
    python3 labs/web-search-spike/demo.py "your query"     # one open lookup
    python3 labs/web-search-spike/demo.py --claim "X is Y" # one claim check
    python3 labs/web-search-spike/demo.py --tiers          # tier health only

For a COMPARISON between free combinations, use the harness instead — that is
the instrument this demo is only a sanity check for:

    python3 labs/web-search-spike/eval/run_eval.py --all

Engines may be blocked when you run this; that is documented steady state for a
home IP (README "Findings" #4). A blocked tier is REPORTED by name, never
silently folded into "fewer results".
"""

from __future__ import annotations

import sys

import websearch as ws
from websearch.claim import build_check_queries
from websearch.scrapers import scrape

OPEN_QUERIES = ["capital of Australia", "cheap flights Perth to Bali"]
CLAIMS = ["Canberra is the capital of Australia", "Bali is in Thailand"]


def show_tiers() -> None:
    print("--- free tier health ---")
    for tier, state in ws.tier_status().items():
        print(f"  {tier:<14} {state}")


def show(lookup: ws.Lookup, label: str) -> None:
    print(f"\n=== {label}: {lookup.query!r}")
    print(f"    {lookup.elapsed_s:.2f}s · ~{lookup.tokens} tokens · "
          f"{len(lookup.results)} results · tiers={lookup.tiers_used or '(none)'}")
    for tier, why in lookup.failures.items():
        print(f"    REFUSED {tier}: {why}")
    print(lookup.packet or "    (no packet — every tier refused)")


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--tiers":
        show_tiers()
        return 0
    if args and args[0] == "--claim":
        show(ws.check_claim(" ".join(args[1:])), "claim check")
        return 0
    if args:
        show(ws.look_up(" ".join(args)), "open lookup")
        return 0

    show_tiers()

    # Tier 0 alone — the never-blocked path.
    print("\n--- tier 0: structured scrapers (no engine) ---")
    for url in ("https://en.wikipedia.org/wiki/Canberra", "https://en.wikipedia.org/wiki/Bali"):
        extract = scrape(url)
        print(f"  {extract.source}: {extract.title} :: {extract.text[:80]}…" if extract else f"  MISS {url}")

    for query in OPEN_QUERIES:
        show(ws.look_up(query), "open lookup")

    for claim in CLAIMS:
        print(f"\n    shaped queries: {build_check_queries(claim)}")
        show(ws.check_claim(claim), "claim check")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
