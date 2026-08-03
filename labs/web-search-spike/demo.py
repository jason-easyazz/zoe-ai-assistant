#!/usr/bin/env python3
"""LIVE demo of the spike. Hand-run only; makes read-only outbound requests.

    python3 labs/web-search-spike/demo.py                 # the scripted set
    python3 labs/web-search-spike/demo.py "your query"    # one open lookup
    python3 labs/web-search-spike/demo.py --claim "X is Y" # one claim check

Expect key-free engines to be blocked some of the time — DuckDuckGo throttles
automated HTML search aggressively (measured: ~12 requests in a few minutes
triggered a sustained 202 + anomaly-modal from this box). That is the finding,
not a bug: the demo prints the failure rather than pretending it found nothing.
Structured scrapers (Wikipedia, HN) are unaffected.
"""

from __future__ import annotations

import sys
import time

import websearch as ws
from websearch.scrapers import scrape

OPEN_QUERIES = ["capital of Australia", "cheap flights to Bali"]
CLAIMS = ["Canberra is the capital of Australia", "Bali is in Thailand"]


def show(lookup: ws.Lookup, label: str) -> None:
    print(f"\n=== {label}: {lookup.query!r}")
    print(f"    {lookup.elapsed_s:.2f}s · ~{lookup.tokens} tokens · {len(lookup.results)} results")
    if lookup.failures:
        print(f"    engine failures: {lookup.failures}")
    print(lookup.packet or "    (no packet — every engine failed)")


def replay() -> None:
    """Show the full pipeline on RECORDED responses — no network at all.

    Worth having because the live engines block (see module docstring): this is
    how a reviewer sees a real packet without waiting out a bot challenge.
    """
    import json
    import pathlib

    from websearch.engines import parse_ddg_html, parse_ddg_lite
    from websearch.merge import consensus_merge
    from websearch.packet import estimate_tokens, format_packet
    from websearch.scrapers import parse_wikipedia

    fixtures = pathlib.Path(__file__).parent / "tests" / "fixtures"
    read = lambda name: (fixtures / name).read_text(encoding="utf-8")  # noqa: E731

    merged = consensus_merge(
        [parse_ddg_html(read("ddg_html.html")), parse_ddg_lite(read("ddg_lite.html"))], limit=6
    )
    extract = parse_wikipedia(json.loads(read("wikipedia_extract.json")), merged[0].url)
    packet = format_packet(merged, extract=extract, token_budget=350)
    print("--- replayed from fixtures (no network) ---")
    for result in merged:
        print(f"  engines={result.engines} rank={result.rank}  {result.url}")
    print(f"\n{packet}\n\n~{estimate_tokens(packet)} tokens")


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--replay":
        replay()
        return 0
    if args and args[0] == "--claim":
        show(ws.check_claim(" ".join(args[1:])), "claim")
        return 0
    if args:
        show(ws.look_up(" ".join(args)), "lookup")
        return 0

    # 1. Structured scrapers — the reliable tier, no engine involved.
    print("--- structured scrapers (no search engine) ---")
    for url in ("https://en.wikipedia.org/wiki/Canberra", "https://en.wikipedia.org/wiki/Bali"):
        started = time.monotonic()
        extract = scrape(url)
        elapsed = time.monotonic() - started
        print(f"  {elapsed:.2f}s  {extract.source}: {extract.title} :: {extract.text[:80]}…"
              if extract else f"  {elapsed:.2f}s  MISS {url}")

    # 2. Open lookups.
    for query in OPEN_QUERIES:
        show(ws.look_up(query), "open lookup")

    # 3. Claim backing — note the two shaped queries.
    from websearch.claim import build_check_queries

    for claim in CLAIMS:
        print(f"\n    queries for {claim!r}: {build_check_queries(claim)}")
        show(ws.check_claim(claim), "claim check")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
