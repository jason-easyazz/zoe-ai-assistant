"""Search provider tier for Zoe's ``web_search`` — Tavily first, DDG fallback.

Zoe's live search is a DuckDuckGo scrape (``zoe_agent._ddg_search_sync``). It
works, but it is a scrape: fragile to HTML changes and rate limits, and its
snippets are noisy for a "back that claim up with a source" answer. This adds a
purpose-built tier IN FRONT of it, keeping the existing chain intact underneath:

    Tavily (keyed, citable)  ->  ddgs  ->  CloakBrowser

Tavily is LLM-native: it returns clean snippets with URLs, which is what the
"are you sure?" verification path needs. Free tier is 1,000 credits/month with
no card, and a basic search costs 1 credit.

**Degrades, never breaks.** No key, quota exhausted, timeout, or any API error
returns an EMPTY list — the caller falls through to the existing DDG path, i.e.
exactly today's behaviour. So this is inert until a key exists.

Provider selection (``ZOE_SEARCH_PROVIDER``):
  * ``auto``   (default) — Tavily when a key is present, else DDG.
  * ``tavily`` — prefer Tavily (still falls back on error).
  * ``ddg``    — never call Tavily (explicit off switch).

No new dependency: uses ``httpx``, already a service dependency.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"


def tavily_api_key() -> str:
    """The Tavily key, or "" when unset. Never logged."""
    return (os.environ.get("TAVILY_API_KEY") or "").strip()


def search_provider() -> str:
    """Selected provider: auto|tavily|ddg. Read per call so a flip needs no restart."""
    return (os.environ.get("ZOE_SEARCH_PROVIDER", "auto") or "auto").strip().lower()


def tavily_enabled() -> bool:
    """True when Tavily should be attempted: a key exists and it isn't switched off."""
    provider = search_provider()
    if provider == "ddg":
        return False
    return bool(tavily_api_key())


def _to_common(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Map Tavily's {title,url,content} onto the shape the caller already expects
    from ``_ddg_search_sync`` ({title, href, body}) so this is a drop-in tier."""
    out: list[dict[str, str]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue  # never surface a non-http source into a citation
        out.append({
            "title": str(r.get("title") or "").strip(),
            "href": url,
            "body": str(r.get("content") or "").strip(),
        })
    return out


def tavily_search_sync(query: str, max_results: int = 6, timeout_s: float = 8.0) -> list[dict[str, str]]:
    """Search via Tavily. Returns [] on ANY failure so the caller falls back.

    Synchronous (like ``_ddg_search_sync``) — the caller runs it in an executor.
    """
    q = (query or "").strip()
    if not q or not tavily_enabled():
        return []
    try:
        import httpx

        resp = httpx.post(
            _TAVILY_URL,
            headers={"Authorization": f"Bearer {tavily_api_key()}",
                     "Content-Type": "application/json"},
            json={
                "query": q,
                # 'basic' = 1 credit; the free tier is 1k/month, so keep the
                # cheap depth for routine lookups.
                "search_depth": os.environ.get("ZOE_TAVILY_DEPTH", "basic"),
                "max_results": max(1, min(int(max_results), 20)),
            },
            timeout=timeout_s,
        )
        if resp.status_code != 200:
            # 401 = bad/absent key, 429 = quota exhausted. Both are expected
            # operational states, not crashes — fall back quietly.
            logger.info("web_search: tavily HTTP %s — falling back to ddg", resp.status_code)
            return []
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - any failure falls back to ddg
        logger.info("web_search: tavily unavailable (%s) — falling back to ddg", type(exc).__name__)
        return []

    results = data.get("results") if isinstance(data, dict) else None
    return _to_common(results if isinstance(results, list) else [])
