"""Free-only web lookups + claim-backing for Zoe.

LAB SPIKE — not wired to zoe-data, systemd, Docker or CI. See README.md.

**Operator decision 2026-08-03: FREE-ONLY.** No Tavily PAYGO, no Brave, no
paid tiers. The tier order below follows from that plus what was measured:

    tier 0  structured scrapers   Wikipedia / HN JSON APIs. Never blocked,
                                  fastest (0.46-0.76 s), most precise. Tried
                                  FIRST for claim checks.
    tier 1  Tavily FREE           1,000 credits/month ~= 33 searches/day, hard
                                  local cap. Primary open-lookup engine WHEN
                                  CONFIGURED (it is not, on this box).
    tier 2  ddgs metasearch       18 engines, already a zoe-data dependency.
                                  Opportunistic: home-IP blocks are documented
                                  steady state, so availability is a BONUS,
                                  never load-bearing.
    extract Jina Reader           Keyless page->markdown, ~20 RPM. Enrichment
                                  only: 8.5 s measured, domain-restricted.

Two entrypoints, matching the two shapes of question a voice assistant gets:

    look_up("tickets to Bali")          -> open lookup
    check_claim("Canberra is the ...")  -> claim-backing ("are you sure?")

Both return a `Lookup` whose `.packet` is already budget-capped for the Gemma
4 E4B brain, and whose `.failures` names every tier that refused — a blocked
tier is always reported, never silently folded into "fewer results".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import partial

from . import tavily as _tavily
from .chain import CONTENT_FLOOR_CHARS, FetchResult, Hop, fetch_url, fetch_urls
from .claim import build_check_queries, is_challenge
from .engines import EnginesBlocked, Result
from .engines import search as ddgs_search
from .extract import ExtractUnavailable, jina_reader
from .merge import consensus_merge, fan_out
from .packet import DEFAULT_TOKEN_BUDGET, estimate_tokens, format_packet
from .scrapers import Extract, scrape

__all__ = [
    "Lookup", "look_up", "check_claim", "read_page", "research",
    "is_challenge", "tier_status",
    "Result", "Extract", "format_packet", "estimate_tokens",
    "EnginesBlocked", "ExtractUnavailable",
    "FetchResult", "Hop", "fetch_url", "fetch_urls", "CONTENT_FLOOR_CHARS",
]


@dataclass(slots=True)
class Lookup:
    """One lookup: the packet plus everything needed to audit it."""

    query: str
    packet: str
    results: list[Result] = field(default_factory=list)
    extract: Extract | None = None
    #: tier name -> why it produced nothing. NEVER silently empty.
    failures: dict[str, str] = field(default_factory=dict)
    tiers_used: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    tokens: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.packet)


def _tavily_status() -> str:
    """Three states, three strings. `ready (0/33 left)` was a CONTRADICTION.

    Measured 2026-08-03: with the budget fully spent this reported
    `ready (0/33 left today)` while `_search_tiers` was — correctly — refusing
    to dispatch the tier at all. A status line that says "ready" about a tier
    the code will not call is exactly the kind of instrument that makes a
    reader trust the wrong thing.
    """
    if not _tavily.configured():
        return "unconfigured (TAVILY_API_KEY unset)"
    budget = _tavily.budget_state()
    if budget.remaining <= 0:
        return f"budget-exhausted ({budget.used}/{budget.limit} spent today) — chain degrades past it"
    return f"ready ({budget.remaining}/{budget.limit} left today)"


def tier_status() -> dict[str, str]:
    """What each free tier can do right now — for the harness and the demo."""
    return {
        "scrapers": "ready",
        "tavily-free": _tavily_status(),
        "ddgs": "ready (opportunistic — blocks are steady state)",
        "jina": "ready (enrichment only — ~20 RPM, domain-restricted)",
        "cloakbrowser": "fallback floor — launched ONLY when a cheaper tier is refused",
    }


def _search_tiers(query: str, limit: int) -> tuple[list[list[Result]], dict[str, str], list[str]]:
    """Run the free search tiers in parallel; report every refusal by name.

    Tavily has THREE distinct non-answers and they must not collapse into one
    string. `unconfigured` means the tier was never asked; `budget-exhausted`
    means the 33/day free ceiling is spent and the chain must degrade past it
    (an expected, planned-for state, not an incident); anything else is a real
    failure. `fan_out` would render all three as `"TavilyX: ..."`, so the
    exhausted case is pre-checked here — it is the one the chain is DESIGNED
    to survive, and a design has to be observable to be testable.
    """
    tasks: dict[str, object] = {"ddgs": partial(ddgs_search, query, limit=limit)}
    failures: dict[str, str] = {}

    if not _tavily.configured():
        failures["tavily-free"] = "unconfigured (TAVILY_API_KEY unset)"
    else:
        budget = _tavily.budget_state()
        if budget.remaining <= 0:
            failures["tavily-free"] = (
                f"budget-exhausted (local daily cap {budget.used}/{budget.limit} spent) "
                "— degrading to the free tiers, as designed"
            )
        else:
            tasks["tavily-free"] = partial(_tavily.search, query, limit=limit)

    batches, tier_failures = fan_out(tasks)
    failures.update(tier_failures)
    used = [batch[0].engine for batch in batches if batch]
    return batches, failures, used


def look_up(
    query: str, *, limit: int = 6, token_budget: int = DEFAULT_TOKEN_BUDGET, enrich: bool = True
) -> Lookup:
    """Open lookup: free search tiers, consensus merge, optional enrichment."""
    started = time.monotonic()
    batches, failures, used = _search_tiers(query, limit)
    results = consensus_merge(batches, limit=limit)

    extract = None
    if enrich and results:
        extract = scrape(results[0].url)
        if extract is not None:
            used.append("scrapers")
        else:
            failures["scrapers"] = "no handler owned the top result URL"

    packet = format_packet(results, extract=extract, token_budget=token_budget)
    return Lookup(
        query=query, packet=packet, results=results, extract=extract, failures=failures,
        tiers_used=used, elapsed_s=time.monotonic() - started, tokens=estimate_tokens(packet),
    )


def check_claim(claim: str, *, limit: int = 5, token_budget: int = DEFAULT_TOKEN_BUDGET) -> Lookup:
    """Claim-backing: structured evidence FIRST, then engines. Never a verdict.

    Tier order is inverted relative to `look_up` on purpose: the measured
    finding is that structured scrapers are never blocked and ~2x faster, and
    most household "are you sure?" claims are Wikipedia-shaped. So a claim
    check that can be settled from a structured extract should not spend an
    engine call — which also protects the 33/day Tavily budget.
    """
    started = time.monotonic()
    queries = build_check_queries(claim)
    batches: list[list[Result]] = []
    failures: dict[str, str] = {}
    used: list[str] = []

    for query in queries:
        tier_batches, tier_failures, tier_used = _search_tiers(query, limit)
        batches.extend(tier_batches)
        used.extend(tier_used)
        for tier, why in tier_failures.items():
            failures.setdefault(f"{tier} [{query[:28]}]", why)

    merged = consensus_merge(batches, limit=limit)
    extract = scrape(merged[0].url) if merged else None
    if extract is not None:
        used.append("scrapers")

    packet = format_packet(merged, extract=extract, token_budget=token_budget)
    return Lookup(
        query=claim, packet=packet, results=merged, extract=extract, failures=failures,
        tiers_used=sorted(set(used)), elapsed_s=time.monotonic() - started,
        tokens=estimate_tokens(packet),
    )


def read_page(url: str) -> str:
    """Enrichment: page -> text via Jina Reader. Raises `ExtractUnavailable`."""
    return jina_reader(url).text


@dataclass(slots=True)
class Research:
    """A lookup PLUS the pages actually read, each with its own tier trail."""

    lookup: Lookup
    pages: list[FetchResult] = field(default_factory=list)

    @property
    def fallbacks(self) -> list[FetchResult]:
        """The URLs where a cheap tier was refused and something else served."""
        return [p for p in self.pages if p.fell_back]

    @property
    def unreadable(self) -> list[FetchResult]:
        """URLs EVERY tier refused. A finding, not an omission."""
        return [p for p in self.pages if not p.ok]


def research(
    query: str,
    *,
    limit: int = 8,
    read_top: int = 3,
    floor: int = CONTENT_FLOOR_CHARS,
    use_jina: bool = True,
    url_filter=None,
) -> Research:
    """Full chain: search discovery -> scrapers -> per-URL read with fallback.

    This is the end-to-end shape `look_up` deliberately does not have.
    `look_up` returns SNIPPETS, which is right for a spoken answer; but a
    question like "what does this cost" is not answerable from a snippet — the
    number lives on the page. So `research` reads the pages, and each read
    carries its own `provenance` naming the tier that served it.

    Reads are SEQUENTIAL (`fetch_urls`): never two Chromiums at once.
    """
    lookup = look_up(query, limit=limit)
    urls = [r.url for r in lookup.results]
    if url_filter is not None:
        urls = [u for u in urls if url_filter(u)]
    pages = fetch_urls(urls[:read_top], floor=floor, use_jina=use_jina)
    return Research(lookup=lookup, pages=pages)
