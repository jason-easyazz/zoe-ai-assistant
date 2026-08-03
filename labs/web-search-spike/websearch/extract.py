"""Page-extract tier: Jina Reader (keyless) + CloakBrowser (local, installed).

Jina Reader is a plain `GET https://r.jina.ai/<url>` that returns the page as
markdown. Keyless, ~20 RPM on the anonymous tier, rides `httpx` — no new
dependency. It is the free answer to "read this page", which neither `ddgs`
nor the structured scrapers provide.

MEASURED 2026-08-03, and both numbers matter:

    r.jina.ai/…/wiki/Canberra          -> 200, 133,798 bytes, 8.50 s
    r.jina.ai/…britannica.com/place/…  -> 403 AbuseAlleviationError
                                          "Anonymous access to domain …"

So: (1) anonymous Jina is **domain-restricted** — a 403 here is a tier
limitation, not a dead page, and must be reported as such rather than folded
into "no content"; (2) 8.5 s and 133 KB are both far outside a voice budget.
Jina is an ENRICHMENT tier for a page we already chose, capped hard on both
bytes and time — never on the critical path of a spoken answer.

CloakBrowser is the local fallback (`cloakbrowser` 0.3.28 IS installed on this
box). `services/zoe-data/browser_broker.py` wraps it, and its executor used to
return a **base64 PNG screenshot only** — no page text — so the broker could
not feed a text packet.

RESOLVED by PR #1626 (`feat/browser-broker-text-extraction`), which adds
readability-lite main-content extraction to the broker. The tier lives in
`cloak.py`, which imports that function **by path** so the eval scores the real
implementation rather than a copy. MEASURED 2026-08-03: 4.57 s and ~553 MB peak
RSS across 12 Chromium processes for one Wikipedia page — faster than Jina's
8.5 s, but the RAM is local and Jina's is someone else's.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .engines import UA_POLITE, is_blocked

JINA_PREFIX = "https://r.jina.ai/"
JINA_TIMEOUT_S = 20.0
# Jina returned 133 KB for one Wikipedia article. The packet budget is ~1 KB,
# so anything past this is guaranteed waste — cap at the transport, not later.
MAX_EXTRACT_CHARS = 20_000


class ExtractUnavailable(RuntimeError):
    """The tier refused this URL (rate limit, domain restriction, challenge)."""


@dataclass(slots=True)
class Page:
    url: str
    text: str
    tier: str
    elapsed_s: float
    truncated: bool = False


def _strip_jina_header(body: str) -> str:
    """Drop Jina's `Title:/URL Source:/Published Time:` preamble."""
    marker = "Markdown Content:"
    idx = body.find(marker)
    return body[idx + len(marker) :].strip() if idx >= 0 else body.strip()


def jina_reader(url: str, *, timeout: float = JINA_TIMEOUT_S, client: httpx.Client | None = None) -> Page:
    """Fetch a page as markdown via keyless Jina Reader."""
    started = time.monotonic()
    owned = client or httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False)
    try:
        resp = owned.get(JINA_PREFIX + url, headers={"User-Agent": UA_POLITE, "Accept": "text/plain"})
    finally:
        if client is None:
            owned.close()
    elapsed = time.monotonic() - started

    if resp.status_code == 403:
        raise ExtractUnavailable(f"jina anonymous tier refused this domain (403): {url}")
    if resp.status_code == 429:
        raise ExtractUnavailable(f"jina rate limit (429, ~20 RPM anonymous): {url}")
    if resp.status_code >= 400:
        raise ExtractUnavailable(f"jina HTTP {resp.status_code}: {url}")

    body = resp.text
    if is_blocked(body):
        raise ExtractUnavailable(f"jina returned a bot challenge for {url}")

    text = _strip_jina_header(body)
    truncated = len(text) > MAX_EXTRACT_CHARS
    return Page(
        url=url, text=text[:MAX_EXTRACT_CHARS], tier="jina", elapsed_s=elapsed, truncated=truncated
    )


def cloakbrowser_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("cloakbrowser") is not None
