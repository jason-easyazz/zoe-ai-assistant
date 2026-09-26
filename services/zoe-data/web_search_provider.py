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
from typing import Any

from typed_env import env_str

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"


def tavily_api_key() -> str:
    """The household's Tavily key, or "" when unset. Never logged.

    Household-scoped by design, like every other integration credential in
    ``.env`` (Home Assistant token, model API keys): Zoe is one household's
    assistant, and search is a shared utility with a shared free-tier quota —
    not a per-member identity. There is no per-user key store to read from.
    """
    return env_str("TAVILY_API_KEY", "")


def search_provider() -> str:
    """Selected provider: auto|tavily|ddg. Read per call so a flip needs no restart."""
    return env_str("ZOE_SEARCH_PROVIDER", "auto").lower()


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
        if not url.lower().startswith(("http://", "https://")):
            continue  # never surface a non-http source into a citation (schemes are case-insensitive)
        out.append({
            "title": str(r.get("title") or "").strip(),
            "href": url,
            "body": str(r.get("content") or "").strip(),
        })
    return out


# Outcome statuses shared with ``research_evidence.fetch_web_fallback`` so the
# chat fallback can say *why* it has nothing, not just that it has nothing.
TAVILY_OUTCOME_RESULTS = "results"
TAVILY_OUTCOME_NO_RESULTS = "no_results"
TAVILY_OUTCOME_ERROR = "error"
TAVILY_OUTCOME_OFF = "off"


def tavily_search_outcome(
    query: str, max_results: int = 6, timeout_s: float = 8.0
) -> tuple[str, list[dict[str, str]]]:
    """Search via Tavily and report HOW it went: ``(status, rows)``.

    ``status`` is one of ``results`` / ``no_results`` / ``error`` / ``off``.
    ``off`` means no key or an explicit ``ZOE_SEARCH_PROVIDER=ddg`` (nothing was
    sent); ``error`` covers every HTTP/transport failure (401 bad key, 429 quota,
    timeouts). Rows are ``[]`` for anything but ``results``. The key is never
    logged. Synchronous (like ``_ddg_search_sync``) — run it in an executor.
    """
    q = (query or "").strip()
    if not q or not tavily_enabled():
        return TAVILY_OUTCOME_OFF, []
    try:
        import httpx

        from agent_safety import assert_public_url

        # SSRF guard: the endpoint is a constant, but assert it still resolves to
        # a PUBLIC ip — an operator override or hostile DNS must not point Zoe's
        # keyed, authenticated request at a private/link-local address.
        assert_public_url(_TAVILY_URL)

        # trust_env=False: httpx otherwise honours HTTP(S)_PROXY from the
        # environment, which would route this bearer-token request through an
        # arbitrary proxy. Pin the transport explicitly.
        with httpx.Client(trust_env=False, timeout=timeout_s) as client:
            resp = client.post(
                _TAVILY_URL,
                headers={"Authorization": f"Bearer {tavily_api_key()}",
                         "Content-Type": "application/json"},
                json={
                    "query": q,
                    # 'basic' = 1 credit; the free tier is 1k/month, so keep the
                    # cheap depth for routine lookups. env_str maps a blank
                    # `ZOE_TAVILY_DEPTH=` in .env to the default rather than
                    # sending an empty search_depth the API would reject.
                    "search_depth": env_str("ZOE_TAVILY_DEPTH", "basic"),
                    "max_results": max(1, min(int(max_results), 20)),
                },
            )
        if resp.status_code != 200:
            # 401 = bad/absent key, 429 = quota exhausted. Both are expected
            # operational states, not crashes — fall back quietly.
            logger.info("web_search: tavily HTTP %s — falling back to ddg", resp.status_code)
            return TAVILY_OUTCOME_ERROR, []
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - any failure falls back to ddg
        logger.info("web_search: tavily unavailable (%s) — falling back to ddg", type(exc).__name__)
        return TAVILY_OUTCOME_ERROR, []

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        # HTTP 200 without an interpretable `results` list is an API failure
        # (contract drift, proxy/interstitial JSON) — NOT an honest "nothing
        # found". Report `error` so the recorded detail tells an operator which.
        logger.info("web_search: tavily 200 with malformed body (%s) — falling back to ddg", type(results).__name__)
        return TAVILY_OUTCOME_ERROR, []
    rows = _to_common(results)
    return (TAVILY_OUTCOME_RESULTS if rows else TAVILY_OUTCOME_NO_RESULTS), rows


def tavily_search_sync(query: str, max_results: int = 6, timeout_s: float = 8.0) -> list[dict[str, str]]:
    """Search via Tavily. Returns [] on ANY failure so the caller falls back.

    Compatibility shape for ``zoe_agent``'s web_search tier; the status-aware
    form is :func:`tavily_search_outcome`.
    """
    return tavily_search_outcome(query, max_results=max_results, timeout_s=timeout_s)[1]
