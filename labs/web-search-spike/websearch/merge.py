"""Cross-TIER dedup + consensus ranking + a soft/hard deadline fan-out.

Ported from oh-my-pi (MIT) `src/web/search/providers/public.ts`.

SCOPE NOTE (2026-08-03): this merges across **tiers** — Tavily-free, `ddgs`,
structured scrapers — not across search engines. `ddgs` already dedups and
ranks *within* its own 18-engine fan-out (`ResultsAggregator` keyed on `href`,
then `SimpleFilterRanker`), so re-implementing that would be the second of two
rankers on the same data. What `ddgs` cannot do is reconcile its output with a
Tavily result set and a Wikipedia extract, which is exactly this module's job.

Two ideas from `public.ts` survive intact and are the reason to keep it:

1. **Consensus ranking.** Rank a URL by how many INDEPENDENT TIERS returned it,
   then by its best per-tier rank. Agreement between a paid index and a free
   metasearch is a much stronger relevance signal than either one's ordering.
2. **A soft deadline.** Return as soon as the soft deadline passes AND at least
   one tier has answered; keep waiting (to the hard cap) if nothing has. A
   voice product cannot block on the slowest tier, but must not return empty
   just because the fast tier was the one that got blocked.
"""

from __future__ import annotations

import concurrent.futures
import time
import urllib.parse
from dataclasses import dataclass

from .engines import Result

SOFT_DEADLINE_S = 2.5
HARD_DEADLINE_S = 8.0


def dedup_key(raw_url: str) -> str:
    """Canonical key for a result URL.

    Host lower-cased without a leading `www.`, path without a trailing slash,
    query preserved, fragment dropped — the exact set of variations engines
    disagree on for the same page.
    """
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        host = parsed.hostname or ""
        host = host.lower().removeprefix("www.")
        path = parsed.path
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{host}{path}{query}"
    except ValueError:
        return raw_url


@dataclass(slots=True)
class _Merged:
    result: Result
    engines: set[str]
    best_rank: int
    order: int


def consensus_merge(batches: list[list[Result]], limit: int = 8) -> list[Result]:
    """Merge per-tier result lists into one consensus-ranked list.

    `Result.engines` counts distinct contributing TIERS (`Result.engine`
    values), so a URL returned by both Tavily and `ddgs` outranks one returned
    by either alone.
    """
    merged: dict[str, _Merged] = {}
    for batch in batches:
        for rank, result in enumerate(batch):
            key = dedup_key(result.url)
            existing = merged.get(key)
            if existing is None:
                merged[key] = _Merged(
                    result=Result(
                        title=result.title,
                        url=result.url,
                        snippet=result.snippet,
                        engine=result.engine,
                        rank=rank,
                        extra=dict(result.extra),
                    ),
                    engines={result.engine},
                    best_rank=rank,
                    order=len(merged),
                )
                continue
            existing.engines.add(result.engine)
            if rank < existing.best_rank:
                existing.best_rank = rank
                existing.result.title = result.title
                existing.result.url = result.url
            # Keep the most informative snippet, whichever engine ranked it best.
            if len(result.snippet) > len(existing.result.snippet):
                existing.result.snippet = result.snippet

    ranked = sorted(merged.values(), key=lambda m: (-len(m.engines), m.best_rank, m.order))
    out: list[Result] = []
    for item in ranked[:limit]:
        item.result.engines = len(item.engines)
        item.result.rank = len(out)
        out.append(item.result)
    return out


def fan_out(
    tasks: dict[str, callable],
    *,
    soft_deadline_s: float = SOFT_DEADLINE_S,
    hard_deadline_s: float = HARD_DEADLINE_S,
) -> tuple[list[list[Result]], dict[str, str]]:
    """Run engine callables in parallel, honouring soft/hard deadlines.

    Returns `(batches, failures)`. Individual engine failures are tolerated and
    reported; the caller decides whether an empty result is an error. Stragglers
    past the deadline are abandoned, not awaited.
    """
    batches: list[list[Result]] = []
    failures: dict[str, str] = {}
    started = time.monotonic()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        pending = set(futures)
        while pending:
            elapsed = time.monotonic() - started
            # Soft exit: we already have something and the soft budget is spent.
            if batches and elapsed >= soft_deadline_s:
                break
            remaining = hard_deadline_s - elapsed
            if remaining <= 0:
                break
            done, pending = concurrent.futures.wait(
                pending, timeout=min(remaining, 0.25), return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                name = futures[future]
                try:
                    results = future.result()
                except Exception as exc:  # noqa: BLE001 - one engine failing is normal
                    failures[name] = f"{type(exc).__name__}: {exc}"
                    continue
                if results:
                    batches.append(results)
                else:
                    failures[name] = "no results"
        for future in pending:
            future.cancel()
            failures.setdefault(futures[future], "deadline exceeded")

    return batches, failures
