"""Deterministic research evidence helpers for chat/panel rendering."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from html import unescape


_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
_PRICE_RE = re.compile(r"(?i)\$ ?([0-9]+(?:\.[0-9]{1,2})?)")
_DDG_RESULT_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_HTML_PRICE_RE = re.compile(r"(?i)(?:\$|aud\s*)([0-9]{1,4}(?:\.[0-9]{1,2})?)")
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
    research_markers = (
        "cheapest",
        "price",
        "compare",
        "find me",
        "best",
        "flight",
        "recipe",
        "weekend",
        "events",
        "deal",
        "bottle shop",
    )
    if any(m in msg for m in research_markers):
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
    if not any(k in msg for k in ("$", "budget", "under ", "max ", "minimum", "price")):
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
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            data = resp.read(350000).decode("utf-8", errors="replace")
    except Exception:
        return ""
    return _extract_price_from_html_text(data)


def fetch_web_fallback_results(query: str, max_results: int = 5, timeout_s: float = 8.0) -> list[dict[str, str]]:
    """Fetch lightweight fallback web results from DuckDuckGo HTML endpoint."""
    if not (query or "").strip():
        return []
    url = f"https://duckduckgo.com/html/?q={quote_plus(query.strip())}"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

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
        price_match = _PRICE_RE.search(snippet)
        rows.append(
            {
                "title": title[:160],
                "url": target,
                "price": f"${price_match.group(1)}" if price_match else "",
                "snippet": snippet[:280],
            }
        )
    # Best-effort price enrichment from destination pages.
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
) -> dict[str, Any]:
    url_price_pairs = _extract_url_price_pairs(response_text or "")
    fallback_rows = web_fallback_results or []
    if not url_price_pairs and fallback_rows:
        for row in fallback_rows:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            price = str(row.get("price") or "").strip()
            url_price_pairs.append((url, price))
    urls = [u for u, _ in url_price_pairs]
    if not urls:
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
    )
    return asdict(pkg)

