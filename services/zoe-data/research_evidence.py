"""Deterministic research evidence helpers for chat/panel rendering."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from html import unescape

from agent_safety import guarded_urlopen

logger = logging.getLogger(__name__)


_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
_PRICE_RE = re.compile(r"(?i)\$ ?([0-9]+(?:\.[0-9]{1,2})?)")
_DDG_RESULT_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_HTML_PRICE_RE = re.compile(r"(?i)(?:\$|aud\s*)([0-9]{1,4}(?:\.[0-9]{1,2})?)")
DDG_SEARCH_HTML_MAX_BYTES = 5 * 1024 * 1024

# ── Web-lookup outcome (B10.0) ────────────────────────────────────────────────
# A challenge page is NOT "no results". DuckDuckGo answers scripted fetches with
# an anomaly page (measured live 2026-09-26: HTTP 202, ~14 kB, zero `result__a`
# links, `anomaly-modal` + `anomaly.js` in the body); other walls serve HTTP 200
# with a captcha title. Status alone is not a wall detector and neither is body
# size, so both signals feed `classify_ddg_response`.
WEB_LOOKUP_RESULTS = "results"
WEB_LOOKUP_NO_RESULTS = "no_results"
WEB_LOOKUP_BLOCKED = "blocked"
WEB_LOOKUP_ERROR = "error"
WEB_LOOKUP_OFF = "off"

DDG_BLOCK_MARKERS = (
    "anomaly-modal",
    "anomaly.js",
    "<title>captcha",
    "unusual traffic",
    "verifying your browser",
    "just a moment...",
    "cf-browser-verification",
    "challenges.cloudflare.com",
    "enable javascript and cookies to continue",
    "you have been blocked",
    "access to this page has been denied",
)
# What html.duckduckgo.com renders for a query with NO hits: an empty results
# shell carrying `<div class="no-results">No results.</div>`. This is the only
# body that earns `no_results` — that verdict is POSITIVE ("DDG answered and
# said nothing matches"), so a 2xx body with neither result anchors nor this
# marker (blank, truncated mid-page, consent/maintenance page, a markup change)
# is an `error` ("unrecognised page"), not a successful empty lookup.
DDG_EMPTY_RESULTS_MARKERS = ('class="no-results"',)
DDG_UNRECOGNISED_PAGE = "unrecognised page"
# Explicit refusals by status; 503 is what a Cloudflare/Akamai interstitial
# commonly returns. DDG's own anomaly page comes back as 202.
_BLOCKED_STATUSES = frozenset({202, 401, 403, 407, 429, 451, 503})

# ``ZOE_WEB_FALLBACK_PROVIDER``: auto (Tavily when keyed, else DDG) |
# duckduckgo (never call Tavily) | off (no web lookup at all). Read per call so
# a flip needs no restart.
WEB_FALLBACK_PROVIDER_ENV = "ZOE_WEB_FALLBACK_PROVIDER"
_WEB_FALLBACK_PROVIDERS = ("auto", "duckduckgo", "off")

WEB_LOOKUP_MESSAGES = {
    WEB_LOOKUP_RESULTS: "",
    WEB_LOOKUP_NO_RESULTS: "Web lookup found nothing for this.",
    WEB_LOOKUP_BLOCKED: "Web lookup unavailable right now — the search provider refused the request.",
    WEB_LOOKUP_ERROR: "Web lookup unavailable right now.",
    WEB_LOOKUP_OFF: "Web lookup is switched off.",
}
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "under",
    "over",
    "find",
    "cheapest",
    "source",
    "sources",
    "links",
    "link",
    "week",
    "month",
    "today",
    "tomorrow",
    "next",
    "best",
    "compare",
}


@dataclass(slots=True)
class ResearchResult:
    rank: int
    name: str
    value: str = ""
    location: str = ""
    url: str = ""
    confidence: float = 0.6
    notes: str = ""


@dataclass(slots=True)
class ResearchScreenshot:
    title: str
    source_url: str = ""
    captured_at: str = ""
    image_base64: str = ""
    is_top_pick: bool = False


@dataclass(slots=True)
class ResearchAction:
    action_id: str
    label: str
    action_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResearchEvidencePackage:
    query: str
    task_class: str
    executed_at: str
    backend: str
    plan_id: str
    research_brief: dict[str, Any]
    results: list[dict[str, Any]]
    screenshots: list[dict[str, Any]]
    sources: list[str]
    actions: list[dict[str, Any]]
    accessibility: dict[str, Any]
    # {status, provider, result_count, message}; empty when no lookup was attempted.
    web_lookup: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebFallbackOutcome:
    """What a web lookup actually did: rows PLUS why they may be empty."""

    status: str
    provider: str
    results: list[dict[str, str]] = field(default_factory=list)
    detail: str = ""

    @property
    def message(self) -> str:
        return WEB_LOOKUP_MESSAGES.get(self.status, WEB_LOOKUP_MESSAGES[WEB_LOOKUP_ERROR])

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "result_count": len(self.results),
            "message": self.message,
            "detail": self.detail,
        }


# Self-recall questions about the user's own life ("what do I do on
# weekends?") are memory/chat turf, never research briefs — even when they
# contain research-ish words. Retrospective/habitual auxiliaries only:
# "can/could" is deliberately absent so "where can I buy X" stays a research ask.
_SELF_RECALL_RE = re.compile(
    r"\b(?:what|when|where|who|why|how)\s+(?:do|did|does|have|has|had|am|was|were)\s+i\b"
)

# A message classifies as research only when an explicit request/imperative
# research frame is present. Bare topical substrings ("weekend", "best",
# "recipe", "price"...) previously hijacked first-person statements like
# "I enjoy hiking on weekends" (live bug — FIX-PACKET-2026-07-07 item 1).
# A first-person statement without one of these request frames can never
# match, so it falls through to "general" by construction.
_RESEARCH_FRAME_RE = re.compile(
    r"""
      ^(?:please\s+)?(?:find|search|research|compare|recommend|look\s+up(?!\s+to\b))\b  # imperative opener
    | \bfind\s+(?:me|us)\b
    | \bsearch\s+(?:for|the\s+web|online)\b
    | \blook\s+up\b(?!\s+to\b)                                # "look up prices", not "look up to her"
    | \b(?:can|could|would|will)\s+you\s+(?:please\s+)?
      (?:find|search|research|compare|look\s+up|recommend)\b
    | \b(?:what'?s|what\s+is|what\s+are)\s+the\s+(?:cheapest|best)\b
    | \bwhere\s+can\s+i\s+(?:buy|get|find|hire|rent)\b
    | \brecommend\s+(?:me\s+)?(?:a|an|some|the)\b
    | ^(?:best|cheapest|top)\b                                # elliptical search-style opener
    """,
    re.VERBOSE,
)


# Personal-data search target: a search whose object is the user's OWN notes /
# journal / memory / saved data. "search my notes for X", "in my notes",
# "my journal for" — these are note_search / recall, never web research.
_PERSONAL_DATA_TARGET_RE = re.compile(
    r"\b(?:in|through|for)?\s*my\s+(?:notes?|journal|diary|memor(?:y|ies)|saved\s+\w+)\b"
    r"|\bmy\s+(?:notes?|journal|diary|memor(?:y|ies))\s+for\b"
)


def classify_query(message: str) -> str:
    msg = (message or "").strip().lower()
    factual_starts = (
        "what is ",
        "who is ",
        "when is ",
        "capital of ",
        "define ",
        "weather ",
    )
    if len(msg.split()) <= 8 and msg.startswith(factual_starts):
        return "simple_factual"
    if _SELF_RECALL_RE.search(msg):
        return "general"
    # Personal-scope search ("search my notes for X", "find in my notes …") is
    # note_search over the user's own data — NOT web research. It must not trip
    # the "Before I start research…" stall (#1099 follow-up). Web research
    # ("find me the cheapest flight") has no personal-data target and is
    # unaffected.
    if _PERSONAL_DATA_TARGET_RE.search(msg):
        return "general"
    if _RESEARCH_FRAME_RE.search(msg):
        return "research"
    return "general"


def missing_brief_fields(message: str) -> list[str]:
    msg = (message or "").lower()
    missing: list[str] = []
    has_location_phrase = bool(
        re.search(r"\b(in|near|around|from|to)\s+[a-z0-9][a-z0-9\s\-]{1,40}\b", msg)
    )
    if not any(
        k in msg
        for k in (
            "local town",
            "local area",
            "my area",
            "my town",
        )
    ) and not has_location_phrase:
        missing.append("location")
    if not any(k in msg for k in ("$", "budget", "under ", "max ", "minimum", "price",
                                   "cheapest", "cheap", "affordable", "cost", "how much",
                                   "best deal", "lowest", "free")):
        missing.append("budget")
    if not any(
        k in msg
        for k in (
            "today",
            "tomorrow",
            "this weekend",
            "this week",
            "weekend",
            "week",
            "month",
            "date",
            "next ",
            "on ",
            "tonight",
        )
    ):
        missing.append("timeframe")
    return missing


def default_source_for_query(query: str) -> str:
    """Build a deterministic source URL when no explicit links are present."""
    search_q = quote_plus((query or "research").strip())
    return f"https://duckduckgo.com/?q={search_q}"


def _clean_html_text(value: str) -> str:
    text = _TAG_RE.sub(" ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _decode_ddg_href(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    uddg = query.get("uddg", [""])[0]
    if uddg:
        return unquote(uddg)
    return href


def _extract_price_from_html_text(html: str) -> str:
    """Extract a plausible retail price from raw HTML text."""
    values: list[float] = []
    for match in _HTML_PRICE_RE.finditer(html or ""):
        try:
            price = float(match.group(1))
        except Exception:
            continue
        if 1.0 <= price <= 2500.0:
            values.append(price)
    if not values:
        return ""
    best = min(values)
    return f"${best:.2f}"


def _query_terms(query: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]{3,}", (query or "").lower())
    return {p for p in parts if p not in _STOPWORDS}


def _price_bounds_for_query(query: str) -> tuple[float, float]:
    q = (query or "").lower()
    if "nbn" in q or "broadband" in q or "internet plan" in q:
        return 20.0, 250.0
    if "cat food" in q or "dog food" in q or "pet food" in q:
        return 5.0, 200.0
    if "flight" in q or "airfare" in q:
        return 50.0, 5000.0
    if "recipe" in q or "ingredient" in q:
        return 2.0, 250.0
    return 2.0, 5000.0


def _is_price_plausible(query: str, price_str: str) -> bool:
    if not price_str:
        return True
    try:
        value = float(price_str.replace("$", "").strip())
    except Exception:
        return False
    lo, hi = _price_bounds_for_query(query)
    return lo <= value <= hi


def _looks_relevant(query: str, *, title: str, url: str, snippet: str = "") -> bool:
    terms = _query_terms(query)
    if not terms:
        return True
    hay = f"{title} {url} {snippet}".lower()
    hits = sum(1 for t in terms if t in hay)
    # Keep rows that match at least two terms, or one strong match for long-tail queries.
    return hits >= 2 or (hits >= 1 and len(terms) <= 3)


def _fetch_page_price(url: str, timeout_s: float) -> str:
    # SSRF guard: result-target pages are arbitrary URLs; only fetch public hosts
    # (validated on the initial URL and on every redirect hop).
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with guarded_urlopen(url, timeout=timeout_s, headers=headers) as resp:
            data = resp.read(350000).decode("utf-8", errors="replace")
    except Exception:  # incl. SSRFBlocked
        return ""
    return _extract_price_from_html_text(data)


def _read_bounded_response(resp: Any, max_bytes: int) -> bytes:
    content_length = resp.headers.get("Content-Length") if hasattr(resp, "headers") else None
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise ValueError(f"response body exceeds {max_bytes} byte cap")

    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = resp.read(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"response body exceeds {max_bytes} byte cap")
        chunks.append(chunk)
    return b"".join(chunks)


def web_fallback_provider() -> str:
    """Selected fallback provider: auto|duckduckgo|off (unknown values → auto)."""
    raw = (os.environ.get("ZOE_WEB_FALLBACK_PROVIDER", "auto") or "auto").strip().lower()
    if raw not in _WEB_FALLBACK_PROVIDERS:
        logger.warning("%s=%r is not one of %s; using auto", WEB_FALLBACK_PROVIDER_ENV, raw, _WEB_FALLBACK_PROVIDERS)
        return "auto"
    return raw


def classify_ddg_response(status: int | None, body: str) -> str:
    """Pure classifier for a DuckDuckGo HTML response.

    A page carrying ``result__a`` links IS a results page: a challenge/anomaly
    page never has them (measured live), while a genuine results page can
    easily *mention* a wall phrase ("unusual traffic", "just a moment...") in a
    snippet or in the echoed query. So the block markers are consulted only once
    result links are known to be absent — a challenge page with zero result
    links is ``blocked``, never ``no_results``, and a results page is never
    thrown away over a phrase in its text. ``no_results`` is a POSITIVE verdict
    and needs DDG's empty-results shell (``DDG_EMPTY_RESULTS_MARKERS``); any
    other anchor-less 2xx body is ``error`` (``unrecognised page``). A ``None``
    status is a transport failure (nothing came back).
    """
    if status is None:
        return WEB_LOOKUP_ERROR
    has_results = _DDG_RESULT_RE.search(body or "") is not None
    if not has_results:
        low = (body or "").lower()
        if any(marker in low for marker in DDG_BLOCK_MARKERS):
            return WEB_LOOKUP_BLOCKED
        if status in _BLOCKED_STATUSES:
            return WEB_LOOKUP_BLOCKED
    if not 200 <= status < 300 and status not in _BLOCKED_STATUSES:
        return WEB_LOOKUP_ERROR
    if has_results:
        return WEB_LOOKUP_RESULTS
    # 2xx, no anchors, no wall: only DDG's own empty-results shell is a real
    # "nothing matches" answer; anything else is a page we cannot read.
    return WEB_LOOKUP_NO_RESULTS if _is_ddg_empty_results_page(body) else WEB_LOOKUP_ERROR


def _is_ddg_empty_results_page(body: str) -> bool:
    low = (body or "").lower()
    return any(marker in low for marker in DDG_EMPTY_RESULTS_MARKERS)


def _parse_ddg_results(body: str, max_results: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _DDG_RESULT_RE.finditer(body):
        if len(rows) >= max_results:
            break
        raw_href = (match.group("href") or "").strip()
        target = _decode_ddg_href(raw_href)
        if not target.startswith("http://") and not target.startswith("https://"):
            continue
        if target in seen:
            continue
        seen.add(target)
        title = _clean_html_text(match.group("title") or "") or f"Option {len(rows) + 1}"
        # Pull a small nearby window for snippet/price hints.
        snippet_window = body[match.end() : min(len(body), match.end() + 900)]
        snippet_match = re.search(
            r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
            snippet_window,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet = _clean_html_text(snippet_match.group(1) if snippet_match else "")
        rows.append(_fallback_row(title=title, url=target, snippet=snippet))
    return rows


def _fallback_row(*, title: str, url: str, snippet: str) -> dict[str, str]:
    price_match = _PRICE_RE.search(snippet)
    return {
        "title": title[:160],
        "url": url,
        "price": f"${price_match.group(1)}" if price_match else "",
        "snippet": snippet[:280],
    }


def _verify_rows(query: str, rows: list[dict[str, str]], timeout_s: float) -> list[dict[str, str]]:
    """Relevance + best-effort price enrichment from destination pages."""
    for row in rows:
        if not _looks_relevant(
            query,
            title=str(row.get("title") or ""),
            url=str(row.get("url") or ""),
            snippet=str(row.get("snippet") or ""),
        ):
            row["price"] = ""
            row["verified"] = "false"
            continue
        if row.get("price"):
            row["verified"] = "true"
            continue
        row["price"] = _fetch_page_price(row.get("url", ""), timeout_s=min(4.5, timeout_s))
        row["verified"] = "true"
    filtered: list[dict[str, str]] = []
    for row in rows:
        price = str(row.get("price") or "")
        if not _is_price_plausible(query, price):
            row["price"] = ""
        # Only keep verified rows; fallback to all rows if verification eliminated everything.
        if str(row.get("verified") or "") == "true":
            filtered.append(row)
    return filtered or rows


def _fetch_ddg(query: str, max_results: int, timeout_s: float) -> WebFallbackOutcome:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    status: int | None = None
    body = ""
    detail = ""
    try:
        with guarded_urlopen(url, timeout=timeout_s, headers=headers) as resp:
            status = int(getattr(resp, "status", None) or getattr(resp, "code", None) or 200)
            body = _read_bounded_response(resp, DDG_SEARCH_HTML_MAX_BYTES).decode("utf-8", errors="replace")
    except HTTPError as exc:
        # urllib raises on 4xx/5xx; the body still carries the wall's markers.
        status = int(exc.code)
        try:
            body = exc.read(DDG_SEARCH_HTML_MAX_BYTES).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - body is optional evidence
            body = ""
        detail = f"HTTP {status}"
    except Exception as exc:  # incl. SSRFBlocked, timeouts, over-cap bodies
        return WebFallbackOutcome(WEB_LOOKUP_ERROR, "duckduckgo", [], type(exc).__name__)

    verdict = classify_ddg_response(status, body)
    if verdict != WEB_LOOKUP_RESULTS:
        if verdict == WEB_LOOKUP_ERROR and 200 <= status < 300:
            detail = f"{DDG_UNRECOGNISED_PAGE} (HTTP {status})"
        return WebFallbackOutcome(verdict, "duckduckgo", [], detail or f"HTTP {status}")
    rows = _verify_rows(query, _parse_ddg_results(body, max_results), timeout_s)
    if not rows:
        return WebFallbackOutcome(WEB_LOOKUP_NO_RESULTS, "duckduckgo", [], "no http result targets")
    return WebFallbackOutcome(WEB_LOOKUP_RESULTS, "duckduckgo", rows)


def _fetch_tavily(query: str, max_results: int, timeout_s: float) -> WebFallbackOutcome:
    # Lazy import: web_search_provider pulls typed_env/httpx; the classifier half
    # of this module stays stdlib-only for the slim CI lane.
    from web_search_provider import tavily_search_outcome

    status, raw = tavily_search_outcome(query, max_results=max_results, timeout_s=timeout_s)
    if status != WEB_LOOKUP_RESULTS:
        return WebFallbackOutcome(status, "tavily", [])
    rows = [
        _fallback_row(
            title=str(r.get("title") or "") or f"Option {i}",
            url=str(r.get("href") or ""),
            snippet=str(r.get("body") or ""),
        )
        for i, r in enumerate(raw[:max_results], start=1)
    ]
    rows = _verify_rows(query, rows, timeout_s)
    if not rows:
        return WebFallbackOutcome(WEB_LOOKUP_NO_RESULTS, "tavily", [])
    return WebFallbackOutcome(WEB_LOOKUP_RESULTS, "tavily", rows)


# In-process record of the LAST lookup for the backend status surface
# (`/api/system/status` -> `web_lookup`): status / provider / detail / UTC
# timestamp only — never the query text (personal data) and never the key.
_LAST_WEB_LOOKUP: dict[str, Any] = {}


def _record_last_outcome(outcome: WebFallbackOutcome) -> None:
    _LAST_WEB_LOOKUP.clear()
    _LAST_WEB_LOOKUP.update(
        status=outcome.status,
        provider=outcome.provider,
        detail=outcome.detail,
        at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _tavily_key_present() -> bool:
    try:
        from web_search_provider import tavily_api_key

        return bool(tavily_api_key())
    except Exception:  # noqa: BLE001 - provider module absent → no key
        return False


def web_lookup_status() -> dict[str, Any]:
    """Backend status block for the web lookup — configuration + last outcome.

    ``provider`` is the RESOLVED provider a lookup would use right now
    (``auto`` collapses to ``tavily`` when keyed and not switched off, else
    ``duckduckgo``); ``configured`` is the raw ``ZOE_WEB_FALLBACK_PROVIDER``
    value. ``tavily_key_present`` says whether a key exists — the key itself is
    never exposed. ``last_outcome`` is ``None`` until a lookup has run.
    """
    configured = web_fallback_provider()
    if configured == "auto":
        resolved = "tavily" if _tavily_configured() else "duckduckgo"
    else:
        resolved = configured
    return {
        "provider": resolved,
        "configured": configured,
        "tavily_key_present": _tavily_key_present(),
        "last_outcome": dict(_LAST_WEB_LOOKUP) or None,
    }


def _tavily_configured() -> bool:
    try:
        from web_search_provider import tavily_enabled

        return bool(tavily_enabled())
    except Exception:  # noqa: BLE001 - provider module absent → DDG only
        return False


def fetch_web_fallback(query: str, max_results: int = 5, timeout_s: float = 8.0) -> WebFallbackOutcome:
    """Web lookup with an HONEST outcome: rows plus status + provider.

    Provider order under ``auto``: Tavily when a key is configured (a real API,
    not a scrape), then DuckDuckGo HTML. A blocked/error DDG answer is reported
    as such — never as "no results". One INFO line per lookup; the query text is
    never logged (it can carry personal data), only its length.
    """
    q = (query or "").strip()
    if not q:
        return WebFallbackOutcome(WEB_LOOKUP_NO_RESULTS, "none", [], "empty query")
    provider = web_fallback_provider()
    if provider == "off":
        outcome = WebFallbackOutcome(WEB_LOOKUP_OFF, "none", [], f"{WEB_FALLBACK_PROVIDER_ENV}=off")
    else:
        attempted: list[str] = []
        tavily: WebFallbackOutcome | None = None
        if provider == "auto" and _tavily_configured():
            attempted.append("tavily")
            tavily = _fetch_tavily(q, max_results, timeout_s)
        if tavily is not None and tavily.status == WEB_LOOKUP_RESULTS:
            outcome = tavily
        else:
            attempted.append("duckduckgo")
            ddg = _fetch_ddg(q, max_results, timeout_s)
            if tavily is None:
                outcome = ddg
            else:
                # Keep the BEST-QUALITY verdict. `results` beats everything; a
                # completed Tavily `no_results` is a real answer and must not be
                # overwritten by a later DDG wall/transport failure — the card
                # would say "unavailable" for a lookup that finished and found
                # nothing. Only real rows may replace it.
                keep_tavily = tavily.status == WEB_LOOKUP_NO_RESULTS and ddg.status != WEB_LOOKUP_RESULTS
                outcome = tavily if keep_tavily else ddg
                if outcome.status != WEB_LOOKUP_RESULTS:
                    ddg_why = f"{ddg.status} ({ddg.detail})" if ddg.detail else ddg.status
                    outcome.detail = f"tavily={tavily.status}; duckduckgo={ddg_why}"
                    if keep_tavily:
                        # The verdict is Tavily's; DDG's attempt is in `detail`.
                        attempted = ["tavily"]
        outcome.provider = outcome.provider if outcome.status == WEB_LOOKUP_RESULTS else ">".join(attempted)
    logger.info(
        "web_fallback: provider=%s status=%s results=%d query_len=%d%s",
        outcome.provider,
        outcome.status,
        len(outcome.results),
        len(q),
        f" detail={outcome.detail}" if outcome.detail else "",
    )
    _record_last_outcome(outcome)
    return outcome


def fetch_web_fallback_results(query: str, max_results: int = 5, timeout_s: float = 8.0) -> list[dict[str, str]]:
    """Compatibility shape (bare rows) for ``zoe_agent._ddg_search_sync``.

    Deliberately DDG-ONLY (its pre-B10.0 behaviour): ``zoe_agent._web_search_ddg``
    already ran the Tavily tier itself before falling through to this last
    resort, so routing it through :func:`fetch_web_fallback`'s ``auto`` would
    spend a second Tavily credit and a second timeout on the same query. Chat
    uses :func:`fetch_web_fallback`, which picks the provider and says WHY rows
    are empty.
    """
    q = (query or "").strip()
    if not q:
        return []
    return _fetch_ddg(q, max_results, timeout_s).results


def package_needs_web_fallback(package: dict[str, Any]) -> bool:
    """Return True when package lacks usable per-site research evidence."""
    sources = [str(s or "") for s in (package.get("sources") or []) if str(s or "").strip()]
    results = package.get("results") or []
    if not sources:
        return True
    if all("duckduckgo.com/?q=" in src for src in sources):
        return True
    if not results:
        return True
    has_nonempty_price = any(str(r.get("value") or "").strip() for r in results if isinstance(r, dict))
    has_non_duck_source = any("duckduckgo.com/?q=" not in src for src in sources)
    return not has_non_duck_source or (len(results) <= 1 and not has_nonempty_price)


def _extract_url_price_pairs(text: str) -> list[tuple[str, str]]:
    """Extract URL + nearby price pairs from free-form model output.

    Price is only attached when found within a local text window around the URL.
    This prevents query budget values from being blindly copied to every result.
    """
    body = text or ""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(body):
        url = match.group(0)
        if url in seen:
            continue
        seen.add(url)
        # Restrict price matching to the sentence containing this URL.
        # Use punctuation+space delimiters so decimal points in prices are not treated
        # as sentence breaks.
        delim_re = re.compile(r"(?:[!?;]\s+|\.\s+|\n+)")
        start = 0
        end = len(body)
        for dm in delim_re.finditer(body):
            if dm.end() <= match.start():
                start = dm.end()
                continue
            if dm.start() >= match.end():
                end = dm.start()
                break
        window = body[start:end]
        url_pos = match.start() - start
        price_hits = list(_PRICE_RE.finditer(window))
        chosen = None
        if price_hits:
            # Prefer prices at/after the URL mention, then nearest by character distance.
            chosen = min(
                price_hits,
                key=lambda pm: (
                    0 if pm.start() >= url_pos else 1,
                    abs(pm.start() - url_pos),
                ),
            )
        value = f"${chosen.group(1)}" if chosen else ""
        pairs.append((url, value))
    return pairs


def build_package(
    *,
    query: str,
    response_text: str,
    backend: str,
    plan_id: str = "",
    screenshot_b64: str = "",
    screenshot_url: str = "",
    web_fallback_results: list[dict[str, str]] | None = None,
    web_lookup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url_price_pairs = _extract_url_price_pairs(response_text or "")
    fallback_rows = web_fallback_results or []
    lookup = dict(web_lookup or {})
    lookup_failed = bool(lookup) and lookup.get("status") != WEB_LOOKUP_RESULTS
    if not url_price_pairs and fallback_rows:
        for row in fallback_rows:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            price = str(row.get("price") or "").strip()
            url_price_pairs.append((url, price))
    urls = [u for u, _ in url_price_pairs]
    if not urls and not lookup_failed:
        # No lookup was attempted (legacy callers): keep the DDG search link as a
        # pointer the user can follow.
        urls = [default_source_for_query(query)]
    results: list[ResearchResult] = []
    for idx, url in enumerate(urls[:5], start=1):
        value = ""
        if idx - 1 < len(url_price_pairs):
            value = url_price_pairs[idx - 1][1]
        results.append(
            ResearchResult(
                rank=idx,
                name=(
                    str(fallback_rows[idx - 1].get("title") or "").strip()
                    if idx - 1 < len(fallback_rows)
                    else f"Option {idx}"
                )
                or f"Option {idx}",
                value=value,
                url=url,
                confidence=max(0.45, 0.85 - (idx * 0.08)),
                notes=(
                    str(fallback_rows[idx - 1].get("snippet") or "").strip()
                    if idx - 1 < len(fallback_rows)
                    else ""
                ),
            )
        )
    if not results:
        if lookup_failed:
            # HONEST placeholder: say the lookup found nothing / was refused,
            # never dress an empty evidence package up as a ranked option.
            results.append(
                ResearchResult(
                    rank=1,
                    name=(
                        "Nothing found"
                        if lookup.get("status") == WEB_LOOKUP_NO_RESULTS
                        else "Web lookup unavailable"
                    ),
                    notes=str(lookup.get("message") or WEB_LOOKUP_MESSAGES[WEB_LOOKUP_ERROR]),
                    confidence=0.2,
                )
            )
        else:
            # fallback single-row summary to keep package non-empty
            results.append(
                ResearchResult(
                    rank=1,
                    name="Top result summary",
                    notes=(response_text or "")[:280],
                    confidence=0.55,
                )
            )

    screenshots: list[ResearchScreenshot] = []
    if screenshot_b64:
        screenshots.append(
            ResearchScreenshot(
                title="Captured browser evidence",
                source_url=screenshot_url or (urls[0] if urls else ""),
                captured_at=datetime.now(timezone.utc).isoformat(),
                image_base64=screenshot_b64,
                is_top_pick=True,
            )
        )

    actions = [
        ResearchAction(
            action_id="save_recipe",
            label="Save Recipe",
            action_type="save_recipe",
            payload={},
        ),
        ResearchAction(
            action_id="save_deal",
            label="Save Deal",
            action_type="save_deal",
            payload={},
        ),
        ResearchAction(
            action_id="save_trip_option",
            label="Save Trip Option",
            action_type="save_trip_option",
            payload={},
        ),
    ]

    pkg = ResearchEvidencePackage(
        query=query,
        task_class=classify_query(query),
        executed_at=datetime.now(timezone.utc).isoformat(),
        backend=backend,
        plan_id=plan_id,
        research_brief={
            "goal": query,
            "constraints": [],
            "must_haves": [],
            "nice_to_haves": [],
            "question_history": [],
        },
        results=[asdict(r) for r in results],
        screenshots=[asdict(s) for s in screenshots],
        sources=urls[:8],
        actions=[asdict(a) for a in actions],
        accessibility={
            "touch_density": "comfortable",
            "font_scale": 1.15,
            "high_contrast": True,
        },
        web_lookup=lookup,
    )
    return asdict(pkg)
