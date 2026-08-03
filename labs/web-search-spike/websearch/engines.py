"""Engine tier — a thin wrapper over `ddgs`, which zoe-data already ships.

DESIGN CALL (2026-08-03, revised): **`ddgs` is the engine tier. The custom
DuckDuckGo HTML parser this module used to carry has been DELETED.**

The first pass of this spike hand-ported oh-my-pi's DuckDuckGo regex parser.
That was the wrong call, and measurement — not reasoning — settled it. See
DESIGN.md §7 for the full Option C analysis; the decisive evidence:

    # taken while html.duckduckgo.com was serving 202 + anomaly-modal to us
    DDGS().text(q, backend="duckduckgo") -> DDGSException("No results found.")
    DDGS().text(q, backend="auto")       -> 5 real results in 1.77 s

`ddgs` 9.14.4 is not a DuckDuckGo scraper; it is a **metasearch aggregator over
18 engines** (duckduckgo, bing, brave, google, mojeek, startpage, wikipedia,
yahoo, yandex, grokipedia, …) with its own `ResultsAggregator` dedup (keyed on
`href`) and `SimpleFilterRanker`. It answered at the exact moment our
single-engine parser was 100% blocked. Keeping a hand-rolled parser beside it
would mean two DuckDuckGo parsers in one chain, the worse one load-bearing.

WHAT SURVIVES from the first pass, because `ddgs` genuinely lacks it:

1. **Blocked != empty.** `ddgs/ddgs.py:454` raises `DDGSException(err or "No
   results found.")` for BOTH "every engine was blocked" and "this query has no
   hits" — measured above. Unwrapped, that makes Zoe tell Jason "there's
   nothing" when the truth is the lookup failed. `search()` below disambiguates
   with a control query and raises `EnginesBlocked` rather than returning `[]`.
2. **Provenance.** `ddgs` gives results no per-engine attribution, so you cannot
   tell which of the 18 answered. The eval harness needs that to compare
   combinations, so `search_by_backend()` probes each individually.
"""

from __future__ import annotations

import html as _html
import re
import time
from dataclasses import dataclass, field

# Text backends worth attributing separately in the eval harness. `auto` fans
# out across all of them; these are the ones whose individual health we care
# about when diagnosing a bad run.
NAMED_BACKENDS = ("duckduckgo", "brave", "google", "mojeek", "startpage", "wikipedia")

# A query that must always have hits. If even this returns nothing, the tier is
# blocked or down — that is not a property of the caller's query.
CONTROL_QUERY = "wikipedia"

# Wikimedia and most JSON APIs REJECT a generic browser UA (measured: 403) but
# accept a descriptive one. Every tier that talks to an API identifies itself.
UA_POLITE = "ZoeAssistant/0.1 (local household assistant; contact: jason@easyazz.com)"

# Re-probing the control on every failure would double our request rate into an
# engine that is already rate-limiting us, so a verdict is reused briefly.
_CONTROL_TTL_S = 120.0
_control_cache: tuple[float, bool] | None = None


class EnginesBlocked(RuntimeError):
    """Every engine refused us. NOT the same as "this query has no results"."""


@dataclass(slots=True)
class Result:
    """One search hit, normalised across tiers (ddgs, Tavily, scrapers)."""

    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    # Set by merge.consensus_merge: how many TIERS returned this URL.
    engines: int = 1
    rank: int = 0
    extra: dict = field(default_factory=dict)


_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")


def clean_text(value: str) -> str:
    """Strip inline tags, unescape entities, collapse whitespace."""
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", value))).strip()


def is_blocked(body: str) -> bool:
    """True when a RAW HTML body is a bot challenge rather than content.

    Still needed for the tiers that hand us raw bodies (Jina Reader,
    CloakBrowser) — `ddgs` parses internally so it never exposes one. Engines
    mix status codes on these: DuckDuckGo serves 202, and Mojeek served HTTP
    200 with `<title>Captcha</title>`, so **status is not a usable signal**.
    """
    low = body.lower()
    return any(
        marker in low
        for marker in ("anomaly-modal", "anomaly.js", "<title>captcha", "unusual traffic", "verifying your browser")
    )


def _to_results(rows: list[dict], engine: str) -> list[Result]:
    out: list[Result] = []
    for rank, row in enumerate(rows):
        url = str(row.get("href") or row.get("url") or "").strip()
        title = clean_text(str(row.get("title") or ""))
        if not url or not title:
            continue
        out.append(
            Result(
                title=title,
                url=url,
                snippet=clean_text(str(row.get("body") or row.get("snippet") or "")),
                engine=engine,
                rank=rank,
            )
        )
    return out


def _raw_search(query: str, backend: str, limit: int, timeout: float) -> list[dict]:
    """The single point that touches `ddgs`. Injectable for offline tests."""
    from ddgs import DDGS

    return DDGS(timeout=timeout).text(query, max_results=limit, backend=backend)


def engines_reachable(searcher=None, *, timeout: float = 10.0, force: bool = False) -> bool:
    """Can we reach ANY engine right now? Memoised for `_CONTROL_TTL_S`.

    This is the block-vs-empty disambiguator: it asks a question that always
    has an answer, so a null result can only mean the tier is refusing us.
    """
    global _control_cache
    now = time.monotonic()
    if not force and _control_cache is not None and now - _control_cache[0] < _CONTROL_TTL_S:
        return _control_cache[1]
    search_fn = searcher or _raw_search
    try:
        ok = bool(search_fn(CONTROL_QUERY, "auto", 3, timeout))
    except Exception:  # noqa: BLE001 - any control failure means "not reachable"
        ok = False
    _control_cache = (now, ok)
    return ok


def reset_control_cache() -> None:
    """Test seam — drop the memoised reachability verdict."""
    global _control_cache
    _control_cache = None


def search(
    query: str,
    *,
    limit: int = 8,
    backend: str = "auto",
    timeout: float = 10.0,
    searcher=None,
) -> list[Result]:
    """Search via `ddgs`, distinguishing BLOCKED from genuinely-empty.

    Raises `EnginesBlocked` when no engine will answer us at all; returns `[]`
    only when engines are reachable and the query truly has no hits.
    """
    search_fn = searcher or _raw_search
    try:
        rows = search_fn(query, backend, limit, timeout)
    except Exception as exc:  # noqa: BLE001 - ddgs raises DDGSException for BOTH cases
        if engines_reachable(searcher):
            return []
        raise EnginesBlocked(f"no engine answered ({type(exc).__name__}: {exc})") from exc
    if not rows:
        if engines_reachable(searcher):
            return []
        raise EnginesBlocked("no engine answered (empty result set)")
    return _to_results(rows, f"ddgs:{backend}")


def search_by_backend(
    query: str, *, limit: int = 8, timeout: float = 10.0, backends=NAMED_BACKENDS, searcher=None
) -> dict[str, list[Result] | str]:
    """Per-backend provenance for the eval harness.

    `ddgs` gives results no engine attribution, so the only way to learn which
    engines are alive is to ask each one. Values are a result list, or a string
    describing the failure — never a silent empty list.
    """
    search_fn = searcher or _raw_search
    out: dict[str, list[Result] | str] = {}
    for backend in backends:
        try:
            rows = search_fn(query, backend, limit, timeout)
        except Exception as exc:  # noqa: BLE001
            out[backend] = f"FAILED {type(exc).__name__}: {str(exc)[:90]}"
            continue
        out[backend] = _to_results(rows, f"ddgs:{backend}") if rows else "BLOCKED_OR_EMPTY"
    return out
