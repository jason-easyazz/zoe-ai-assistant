"""Structured scrapers: URL-matched handlers that beat generic HTML extraction.

Ported from oh-my-pi (MIT) `src/web/scrapers/`. The pattern is the valuable
part, and it ports in ~10 lines: a handler is `(url) -> Extract | None`; it
inspects the URL, returns `None` if it does not own it, and the registry tries
each in turn. Adding a site is one function plus one registry line.

Only the JSON-API scrapers are ported. oh-my-pi's ~75 handlers are mostly
developer-tool sites (crates.io, hackage, nuget) that a household voice
assistant will never be asked about; and the HTML-parsing ones (its
`wikipedia.ts` mobile-html path) need a DOM library Zoe does not have. The
three here are the ones a voice product actually hits, and all three are pure
JSON — no parser dependency at all.

Wikipedia is the load-bearing one: it is both the top organic result for most
factual questions AND a clean structured extract, which is exactly what
claim-backing needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from .engines import UA_POLITE

DEFAULT_TIMEOUT_S = 6.0


@dataclass(slots=True)
class Extract:
    """A structured read of one URL — richer than a search snippet."""

    source: str
    title: str
    url: str
    text: str
    facts: dict


# --- Wikipedia -------------------------------------------------------------

_WIKI_RE = re.compile(r"^(?P<lang>\w+)\.wikipedia\.org$")


def wikipedia(url: str, client: httpx.Client) -> Extract | None:
    """Wikipedia article → intro extract via the Action API.

    Uses `action=query&prop=extracts&explaintext` rather than the REST
    `page/summary` endpoint: same data, but it returns the full intro rather
    than a one-line teaser, and it does not require the REST API's stricter
    rate limits. NOTE: Wikimedia 403s a generic browser User-Agent — the
    polite descriptive UA is mandatory (measured 2026-08-03).
    """
    parsed = httpx.URL(url)
    if not _WIKI_RE.match(parsed.host):
        return None
    match = re.match(r"/wiki/(.+)", parsed.path)
    if not match:
        return None
    title = httpx.URL(f"http://x/{match.group(1)}").path[1:]
    lang = _WIKI_RE.match(parsed.host).group("lang")
    resp = client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query", "format": "json", "prop": "extracts",
            "exintro": 1, "explaintext": 1, "redirects": 1, "titles": title,
        },
        headers={"User-Agent": UA_POLITE},
    )
    resp.raise_for_status()
    return parse_wikipedia(resp.json(), url)


def parse_wikipedia(payload: dict, url: str) -> Extract | None:
    """Pure parser — the offline test seam."""
    pages = (payload.get("query") or {}).get("pages") or {}
    for page_id, page in pages.items():
        if str(page_id) == "-1" or "extract" not in page:
            continue
        text = (page.get("extract") or "").strip()
        if not text:
            continue
        return Extract(
            source="wikipedia",
            title=page.get("title", ""),
            url=url,
            text=text,
            facts={"pageid": page.get("pageid")},
        )
    return None


# --- Hacker News -----------------------------------------------------------

def hackernews(url: str, client: httpx.Client) -> Extract | None:
    """HN item or search → title, score, comment count via the Algolia API."""
    parsed = httpx.URL(url)
    if parsed.host not in ("news.ycombinator.com", "hn.algolia.com"):
        return None
    item_id = parsed.params.get("id")
    if not item_id:
        return None
    resp = client.get(
        f"https://hn.algolia.com/api/v1/items/{item_id}", headers={"User-Agent": UA_POLITE}
    )
    resp.raise_for_status()
    return parse_hackernews(resp.json(), url)


def parse_hackernews(payload: dict, url: str) -> Extract | None:
    """Pure parser — the offline test seam. Accepts an item or a search hit."""
    hit = payload
    if "hits" in payload:
        hits = payload.get("hits") or []
        if not hits:
            return None
        hit = hits[0]
    title = hit.get("title") or hit.get("story_title") or ""
    if not title:
        return None
    points = hit.get("points")
    comments = hit.get("num_comments")
    bits = [b for b in (f"{points} points" if points is not None else "",
                        f"{comments} comments" if comments is not None else "") if b]
    return Extract(
        source="hackernews",
        title=title,
        url=hit.get("url") or url,
        text=" · ".join(bits) or title,
        facts={"points": points, "comments": comments, "id": hit.get("objectID")},
    )


# --- registry --------------------------------------------------------------

SCRAPERS = (wikipedia, hackernews)


def scrape(url: str, client: httpx.Client | None = None) -> Extract | None:
    """Try each handler in registry order; return the first that owns the URL.

    A handler that raises is treated as a miss — a structured extract is always
    an enrichment over the search snippet we already have, never a hard
    dependency, so it must never fail the surrounding lookup.
    """
    owned = client or httpx.Client(timeout=DEFAULT_TIMEOUT_S, follow_redirects=True, trust_env=False)
    try:
        for handler in SCRAPERS:
            try:
                extract = handler(url, owned)
            except Exception:  # noqa: BLE001 - enrichment is best-effort by design
                continue
            if extract is not None:
                return extract
        return None
    finally:
        if client is None:
            owned.close()
