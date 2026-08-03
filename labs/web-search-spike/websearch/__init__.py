"""Key-free web search + structured scrapers + a token-budgeted packet.

LAB SPIKE — not wired to zoe-data, systemd, Docker or CI. See README.md.

Two entrypoints, matching the two shapes of question a voice assistant gets:

    look_up("tickets to Bali")        -> open lookup
    check_claim("Canberra is the ...") -> claim-backing ("are you sure?")

Both return a `Lookup` whose `.packet` is already budget-capped for the
Gemma 4 E4B brain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

import httpx

from .claim import build_check_queries, is_challenge
from .engines import DEFAULT_TIMEOUT_S, Result, search_ddg_html, search_ddg_lite
from .merge import consensus_merge, fan_out
from .packet import DEFAULT_TOKEN_BUDGET, estimate_tokens, format_packet
from .scrapers import Extract, scrape

__all__ = [
    "Lookup", "look_up", "check_claim", "is_challenge",
    "Result", "Extract", "format_packet", "estimate_tokens",
]


@dataclass(slots=True)
class Lookup:
    """The result of one lookup: the packet plus everything needed to audit it."""

    query: str
    packet: str
    results: list[Result] = field(default_factory=list)
    extract: Extract | None = None
    failures: dict[str, str] = field(default_factory=dict)
    elapsed_s: float = 0.0
    tokens: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.packet)


def _client() -> httpx.Client:
    # trust_env=False deliberately: proxy env vars must not silently redirect
    # Zoe's outbound lookups (the convention `web_search_provider.py` already sets).
    return httpx.Client(timeout=DEFAULT_TIMEOUT_S, follow_redirects=True, trust_env=False)


def _search(query: str, client: httpx.Client, limit: int) -> tuple[list[Result], dict[str, str]]:
    batches, failures = fan_out(
        {
            "ddg-html": partial(search_ddg_html, query, client, limit),
            "ddg-lite": partial(search_ddg_lite, query, client, limit),
        }
    )
    return consensus_merge(batches, limit=limit), failures


def look_up(
    query: str,
    *,
    limit: int = 6,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    enrich: bool = True,
    client: httpx.Client | None = None,
) -> Lookup:
    """Open lookup: search, merge by consensus, optionally enrich the top hit."""
    import time

    started = time.monotonic()
    owned = client or _client()
    try:
        results, failures = _search(query, owned, limit)
        extract = None
        if enrich and results:
            extract = scrape(results[0].url, owned)
        packet = format_packet(results, extract=extract, token_budget=token_budget)
        return Lookup(
            query=query, packet=packet, results=results, extract=extract,
            failures=failures, elapsed_s=time.monotonic() - started,
            tokens=estimate_tokens(packet),
        )
    finally:
        if client is None:
            owned.close()


def check_claim(
    claim: str,
    *,
    limit: int = 5,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    client: httpx.Client | None = None,
) -> Lookup:
    """Claim-backing: neutral query + contradiction query, evidence not verdict.

    Results from both queries are merged, so a page that answers the neutral
    question AND ranks for the contradiction gains consensus weight — exactly
    the page that settles the question either way.
    """
    import time

    started = time.monotonic()
    owned = client or _client()
    try:
        queries = build_check_queries(claim)
        batches: list[list[Result]] = []
        failures: dict[str, str] = {}
        for query in queries:
            results, failed = _search(query, owned, limit)
            if results:
                batches.append(results)
            failures.update({f"{query[:24]}:{k}": v for k, v in failed.items()})

        merged = consensus_merge(batches, limit=limit)
        extract = scrape(merged[0].url, owned) if merged else None
        packet = format_packet(merged, extract=extract, token_budget=token_budget)
        return Lookup(
            query=claim, packet=packet, results=merged, extract=extract,
            failures=failures, elapsed_s=time.monotonic() - started,
            tokens=estimate_tokens(packet),
        )
    finally:
        if client is None:
            owned.close()
