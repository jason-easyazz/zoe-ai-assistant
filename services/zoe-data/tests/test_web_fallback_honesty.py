"""B10.0 — the chat web fallback must be HONEST about why it has nothing.

Live defect (2026-09-26): DuckDuckGo answers scripted fetches with an anomaly /
challenge page (HTTP 202, `anomaly-modal` + `anomaly.js`, zero `result__a`
links). The old `fetch_web_fallback_results` swallowed that and returned `[]`,
so a research turn silently attached an empty evidence package. These tests pin:

* a challenge page classifies as ``blocked`` — NEVER ``no_results`` (with a
  negative control proving the body markers carry the verdict);
* the caller gets a structured outcome (status + provider + rows);
* ``ZOE_WEB_FALLBACK_PROVIDER`` picks Tavily-first under ``auto`` only when a
  key is configured (negative control: no key → the Tavily transport is never
  constructed), ``duckduckgo`` forces the scrape, ``off`` makes no request;
* the INFO log line carries provider/status/count/query LENGTH, never the query.

ci_safe: every HTTP call is faked; the SSRF guard's DNS resolve is stubbed.
"""
from __future__ import annotations

import io
import logging
import re
from urllib.error import HTTPError

import pytest

import research_evidence as re_mod
import web_search_provider as wsp
from research_evidence import (
    WEB_LOOKUP_BLOCKED,
    WEB_LOOKUP_ERROR,
    WEB_LOOKUP_NO_RESULTS,
    WEB_LOOKUP_OFF,
    WEB_LOOKUP_RESULTS,
    WebFallbackOutcome,
    build_package,
    classify_ddg_response,
    fetch_web_fallback,
    fetch_web_fallback_results,
)

pytestmark = pytest.mark.ci_safe


# ── fixtures: real-shaped pages ───────────────────────────────────────────────

# Shape of html.duckduckgo.com's anomaly page as captured by the web-search spike
# (PR #1610, labs/web-search-spike/tests/fixtures/ddg_anomaly.html) and re-seen
# live on 2026-09-26 at HTTP 202.
CHALLENGE_PAGE = (
    "<!DOCTYPE html><html><head><title>DuckDuckGo</title></head><body>"
    '<div id="anomaly-modal"></div><script src="/anomaly.js"></script>'
    "</body></html>"
)

RESULTS_PAGE = """
<!DOCTYPE html><html><body><div id="links">
  <div class="result results_links">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdeals&amp;rut=abc">Example deal</a>
    <a class="result__snippet" href="https://example.com/deals">Example deal for $19.99 today</a>
  </div>
  <div class="result results_links">
    <a class="result__a" href="https://example.org/other-deal">Other example deal</a>
    <a class="result__snippet" href="https://example.org/other-deal">Another example deal at $21.50</a>
  </div>
</div></body></html>
"""

EMPTY_PAGE = (
    "<!DOCTYPE html><html><body><div id=\"links\">"
    "<div class=\"no-results\">No results.</div></div></body></html>"
)


class _FakeUrlResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status
        self.headers: dict[str, str] = {}
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int | None = None):
        limit = len(self._body) if size is None else min(len(self._body), self._offset + size)
        chunk = self._body[self._offset:limit]
        self._offset = limit
        return chunk


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """No key, no provider override, no page-price enrichment, no DNS."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("ZOE_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, raising=False)
    monkeypatch.setattr(re_mod, "_fetch_page_price", lambda *a, **k: "")
    import agent_safety

    monkeypatch.setattr(agent_safety, "assert_public_url", lambda u: u)


def _serve_ddg(monkeypatch, body: str, status: int = 200) -> dict:
    """Fake the DDG fetch. Returns a dict counting calls."""
    calls = {"n": 0}

    def _open(url, *, timeout, headers=None):
        calls["n"] += 1
        return _FakeUrlResponse(body, status)

    monkeypatch.setattr(re_mod, "guarded_urlopen", _open)
    return calls


def _forbid_tavily_transport(monkeypatch):
    """Negative control: constructing an httpx client at all is a leak."""
    import httpx

    class _Leak:
        def __init__(self, *a, **k):
            raise AssertionError("Tavily transport constructed without a configured key")

    monkeypatch.setattr(httpx, "Client", _Leak)


# ── 1. pure classifier ────────────────────────────────────────────────────────

def test_challenge_page_is_blocked_never_no_results():
    assert classify_ddg_response(202, CHALLENGE_PAGE) == WEB_LOOKUP_BLOCKED
    # Even at 200: status alone is not a wall detector (Cloudflare serves 200).
    assert classify_ddg_response(200, CHALLENGE_PAGE) == WEB_LOOKUP_BLOCKED
    assert classify_ddg_response(200, CHALLENGE_PAGE) != WEB_LOOKUP_NO_RESULTS


def test_negative_control_markers_carry_the_verdict(monkeypatch):
    """If the body markers were ignored, the same page would read as no_results.

    Proves the classifier is looking at the challenge body, not passing by luck.
    """
    monkeypatch.setattr(re_mod, "DDG_BLOCK_MARKERS", ())
    assert classify_ddg_response(200, CHALLENGE_PAGE) == WEB_LOOKUP_NO_RESULTS
    # and the 202 status is an independent second signal for the same page
    assert classify_ddg_response(202, CHALLENGE_PAGE) == WEB_LOOKUP_BLOCKED


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, RESULTS_PAGE, WEB_LOOKUP_RESULTS),
        (202, RESULTS_PAGE, WEB_LOOKUP_RESULTS),   # results win over an odd 2xx
        (200, EMPTY_PAGE, WEB_LOOKUP_NO_RESULTS),
        (200, "", WEB_LOOKUP_NO_RESULTS),
        (403, EMPTY_PAGE, WEB_LOOKUP_BLOCKED),
        (429, "", WEB_LOOKUP_BLOCKED),
        (503, "", WEB_LOOKUP_BLOCKED),
        (500, EMPTY_PAGE, WEB_LOOKUP_ERROR),
        (None, "", WEB_LOOKUP_ERROR),               # transport failure
        (200, "<title>Captcha</title>", WEB_LOOKUP_BLOCKED),
        (200, "Just a moment...", WEB_LOOKUP_BLOCKED),
    ],
)
def test_classifier_matrix(status, body, expected):
    assert classify_ddg_response(status, body) == expected


# A normal results page that merely MENTIONS a wall phrase — in a snippet and in
# the echoed query — must stay `results` (review P1 on #1691: the whole-body
# marker scan misclassified such pages as blocked and threw real rows away).
RESULTS_PAGE_MENTIONING_WALL = RESULTS_PAGE.replace(
    '<div id="links">',
    '<input name="q" value="why does duckduckgo say unusual traffic just a moment...">'
    '<div id="links">',
).replace(
    "Example deal for $19.99 today",
    "Example deal for $19.99 today — guide: fix the &quot;unusual traffic&quot; "
    "and &quot;verifying your browser&quot; captcha wall",
)


def test_results_page_mentioning_a_wall_phrase_is_still_results():
    assert "unusual traffic" in RESULTS_PAGE_MENTIONING_WALL
    assert classify_ddg_response(200, RESULTS_PAGE_MENTIONING_WALL) == WEB_LOOKUP_RESULTS
    assert classify_ddg_response(202, RESULTS_PAGE_MENTIONING_WALL) == WEB_LOOKUP_RESULTS


def test_negative_control_same_phrases_without_result_links_are_blocked():
    """Strip the result anchors and the very same phrases DO mean a wall — proves
    the fix keys on the absence of `result__a` links, not on dropping markers."""
    no_links = re.sub(r'<a class="result__a"[^>]*>.*?</a>', "", RESULTS_PAGE_MENTIONING_WALL, flags=re.S)
    assert "result__a" not in no_links and "unusual traffic" in no_links
    assert classify_ddg_response(200, no_links) == WEB_LOOKUP_BLOCKED


def test_fetch_keeps_rows_from_a_results_page_mentioning_a_wall_phrase(monkeypatch):
    _serve_ddg(monkeypatch, RESULTS_PAGE_MENTIONING_WALL)
    out = fetch_web_fallback("example deal", max_results=5)
    assert out.status == WEB_LOOKUP_RESULTS
    assert [r["url"] for r in out.results] == [
        "https://example.com/deals",
        "https://example.org/other-deal",
    ]


# ── 2. structured outcome from the fetch ──────────────────────────────────────

def test_fetch_reports_blocked_on_live_shaped_challenge(monkeypatch):
    _serve_ddg(monkeypatch, CHALLENGE_PAGE, status=202)
    out = fetch_web_fallback("cheapest flights to bali")
    assert isinstance(out, WebFallbackOutcome)
    assert out.status == WEB_LOOKUP_BLOCKED
    assert out.provider == "duckduckgo"
    assert out.results == []
    assert "unavailable" in out.message.lower()
    d = out.as_dict()
    assert d["status"] == WEB_LOOKUP_BLOCKED and d["result_count"] == 0 and d["message"]


def test_fetch_reports_blocked_on_http_error_status(monkeypatch):
    def _open(url, *, timeout, headers=None):
        raise HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"Access Denied"))

    monkeypatch.setattr(re_mod, "guarded_urlopen", _open)
    out = fetch_web_fallback("cheapest flights to bali")
    assert out.status == WEB_LOOKUP_BLOCKED
    assert out.detail == "HTTP 403"


def test_fetch_reports_error_on_transport_failure(monkeypatch):
    def _open(url, *, timeout, headers=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(re_mod, "guarded_urlopen", _open)
    out = fetch_web_fallback("cheapest flights to bali")
    assert out.status == WEB_LOOKUP_ERROR
    assert out.results == []
    assert out.detail == "TimeoutError"


def test_fetch_reports_no_results_on_genuinely_empty_page(monkeypatch):
    _serve_ddg(monkeypatch, EMPTY_PAGE)
    out = fetch_web_fallback("cheapest flights to bali")
    assert out.status == WEB_LOOKUP_NO_RESULTS
    assert out.results == []
    assert "nothing" in out.message.lower()


def test_fetch_returns_rows_on_results_page(monkeypatch):
    _serve_ddg(monkeypatch, RESULTS_PAGE)
    out = fetch_web_fallback("example deal", max_results=5)
    assert out.status == WEB_LOOKUP_RESULTS
    assert out.provider == "duckduckgo"
    assert [r["url"] for r in out.results] == [
        "https://example.com/deals",
        "https://example.org/other-deal",
    ]
    assert out.results[0]["price"] == "$19.99"
    assert out.message == ""


def test_compat_wrapper_keeps_bare_list_shape(monkeypatch):
    """zoe_agent._ddg_search_sync still consumes a plain list of rows."""
    _serve_ddg(monkeypatch, RESULTS_PAGE)
    rows = fetch_web_fallback_results("example deal", max_results=1)
    assert isinstance(rows, list) and rows[0]["url"] == "https://example.com/deals"
    _serve_ddg(monkeypatch, CHALLENGE_PAGE, status=202)
    assert fetch_web_fallback_results("example deal") == []


def test_compat_wrapper_is_ddg_only_even_with_a_tavily_key(monkeypatch):
    """zoe_agent._web_search_ddg already ran Tavily itself before reaching this
    last-resort tier; routing the wrapper through `auto` would spend a SECOND
    Tavily credit + timeout on the same query (review P2 on #1691)."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, "auto")
    _forbid_tavily_transport(monkeypatch)

    def _leak(*a, **k):
        raise AssertionError("compat wrapper consulted Tavily")

    monkeypatch.setattr(wsp, "tavily_search_outcome", _leak)
    ddg = _serve_ddg(monkeypatch, RESULTS_PAGE)
    rows = fetch_web_fallback_results("example deal", max_results=1)
    assert ddg["n"] == 1 and rows[0]["url"] == "https://example.com/deals"


def test_empty_query_makes_no_request(monkeypatch):
    calls = _serve_ddg(monkeypatch, RESULTS_PAGE)
    out = fetch_web_fallback("   ")
    assert out.status == WEB_LOOKUP_NO_RESULTS and calls["n"] == 0


# ── 3. provider preference (ZOE_WEB_FALLBACK_PROVIDER) ────────────────────────

def test_off_disables_the_lookup_entirely(monkeypatch):
    monkeypatch.setenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, "off")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    calls = _serve_ddg(monkeypatch, RESULTS_PAGE)
    _forbid_tavily_transport(monkeypatch)
    out = fetch_web_fallback("example deal")
    assert out.status == WEB_LOOKUP_OFF
    assert out.results == [] and calls["n"] == 0
    assert "switched off" in out.message


def test_unknown_provider_value_falls_back_to_auto(monkeypatch):
    monkeypatch.setenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, "bing")
    assert re_mod.web_fallback_provider() == "auto"
    monkeypatch.setenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, " DuckDuckGo ")
    assert re_mod.web_fallback_provider() == "duckduckgo"


def test_negative_control_no_key_means_tavily_never_touched(monkeypatch):
    """auto + no TAVILY_API_KEY must go straight to DDG — the Tavily transport
    must not even be constructed (a bearer request with an empty key is a leak)."""
    monkeypatch.setenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, "auto")
    _forbid_tavily_transport(monkeypatch)

    # Second tripwire one layer up: the fallback must not even ASK the provider
    # module (its own key gate would otherwise mask a leak here).
    def _leak(*a, **k):
        raise AssertionError("fallback consulted Tavily without a configured key")

    monkeypatch.setattr(wsp, "tavily_search_outcome", _leak)
    _serve_ddg(monkeypatch, RESULTS_PAGE)
    out = fetch_web_fallback("example deal")
    assert out.status == WEB_LOOKUP_RESULTS and out.provider == "duckduckgo"


def test_negative_control_the_leak_guard_itself_fires(monkeypatch):
    """Prove `_forbid_tavily_transport` would catch a leak: with a key present
    the Tavily path DOES construct the client, and the guard trips."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    _forbid_tavily_transport(monkeypatch)
    status, rows = wsp.tavily_search_outcome("example deal")
    # The provider swallows every exception into `error` — including the guard's
    # AssertionError — which is exactly the signal: the transport WAS built.
    assert status == wsp.TAVILY_OUTCOME_ERROR and rows == []


def _fake_tavily(monkeypatch, status: str, rows: list[dict] | None = None) -> dict:
    calls = {"n": 0}

    def _outcome(query, max_results=6, timeout_s=8.0):
        calls["n"] += 1
        return status, list(rows or [])

    monkeypatch.setattr(wsp, "tavily_search_outcome", _outcome)
    return calls


def test_auto_with_key_prefers_tavily(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    tav = _fake_tavily(
        monkeypatch,
        wsp.TAVILY_OUTCOME_RESULTS,
        [{"title": "Example deal", "href": "https://example.com/deals", "body": "Example deal for $19.99"}],
    )
    ddg = _serve_ddg(monkeypatch, RESULTS_PAGE)
    out = fetch_web_fallback("example deal")
    assert out.status == WEB_LOOKUP_RESULTS and out.provider == "tavily"
    assert out.results[0]["url"] == "https://example.com/deals"
    assert out.results[0]["price"] == "$19.99"
    assert out.results[0]["verified"] == "true"
    assert tav["n"] == 1 and ddg["n"] == 0


def test_auto_tavily_failure_falls_back_to_ddg_and_says_so(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    tav = _fake_tavily(monkeypatch, wsp.TAVILY_OUTCOME_ERROR)
    ddg = _serve_ddg(monkeypatch, CHALLENGE_PAGE, status=202)
    out = fetch_web_fallback("example deal")
    assert tav["n"] == 1 and ddg["n"] == 1
    assert out.status == WEB_LOOKUP_BLOCKED
    assert out.provider == "tavily>duckduckgo"
    assert "tavily=error" in out.detail and "duckduckgo=" in out.detail


def test_auto_tavily_no_results_then_ddg_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    _fake_tavily(monkeypatch, wsp.TAVILY_OUTCOME_NO_RESULTS)
    _serve_ddg(monkeypatch, RESULTS_PAGE)
    out = fetch_web_fallback("example deal")
    assert out.status == WEB_LOOKUP_RESULTS and out.provider == "duckduckgo"


def test_duckduckgo_forces_the_scrape_even_with_a_key(monkeypatch):
    monkeypatch.setenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, "duckduckgo")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    tav = _fake_tavily(monkeypatch, wsp.TAVILY_OUTCOME_RESULTS, [{"title": "T", "href": "https://t.test", "body": "b"}])
    _forbid_tavily_transport(monkeypatch)
    ddg = _serve_ddg(monkeypatch, RESULTS_PAGE)
    out = fetch_web_fallback("example deal")
    assert out.provider == "duckduckgo" and tav["n"] == 0 and ddg["n"] == 1


def test_search_provider_ddg_switch_still_disables_tavily(monkeypatch):
    """The pre-existing ZOE_SEARCH_PROVIDER=ddg off-switch is honoured under auto."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("ZOE_SEARCH_PROVIDER", "ddg")
    tav = _fake_tavily(monkeypatch, wsp.TAVILY_OUTCOME_RESULTS, [{"title": "T", "href": "https://t.test", "body": "b"}])
    _serve_ddg(monkeypatch, RESULTS_PAGE)
    out = fetch_web_fallback("example deal")
    assert out.provider == "duckduckgo" and tav["n"] == 0


# ── 4. one INFO line per lookup, never the query text ─────────────────────────

def test_log_line_has_provider_status_count_and_length_not_query(monkeypatch, caplog):
    _serve_ddg(monkeypatch, CHALLENGE_PAGE, status=202)
    query = "cheapest flights for Jason Smith to Bali"
    with caplog.at_level(logging.INFO, logger="research_evidence"):
        fetch_web_fallback(query)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("web_fallback:")]
    assert len(lines) == 1, lines
    line = lines[0]
    assert "provider=duckduckgo" in line
    assert f"status={WEB_LOOKUP_BLOCKED}" in line
    assert "results=0" in line
    assert f"query_len={len(query)}" in line
    assert "Jason" not in line and "Bali" not in line


# ── 5. honest evidence package ────────────────────────────────────────────────

def test_package_with_blocked_lookup_is_honest_not_placeholder():
    outcome = WebFallbackOutcome(WEB_LOOKUP_BLOCKED, "duckduckgo", [], "HTTP 202")
    pkg = build_package(
        query="cheapest flights to bali",
        response_text="Here is what I know.",
        backend="zoeAgent",
        web_fallback_results=outcome.results,
        web_lookup=outcome.as_dict(),
    )
    assert pkg["web_lookup"]["status"] == WEB_LOOKUP_BLOCKED
    assert pkg["web_lookup"]["result_count"] == 0
    assert "unavailable" in pkg["web_lookup"]["message"].lower()
    # no fabricated duckduckgo.com/?q= "source", no "Option 1"/"Top result summary"
    assert pkg["sources"] == []
    assert len(pkg["results"]) == 1
    assert pkg["results"][0]["name"] == "Web lookup unavailable"
    assert pkg["results"][0]["url"] == ""
    assert "unavailable" in pkg["results"][0]["notes"].lower()


def test_package_with_no_results_says_nothing_found():
    outcome = WebFallbackOutcome(WEB_LOOKUP_NO_RESULTS, "tavily>duckduckgo", [])
    pkg = build_package(query="q", response_text="", backend="zoeAgent", web_lookup=outcome.as_dict())
    assert pkg["results"][0]["name"] == "Nothing found"
    assert "nothing" in pkg["results"][0]["notes"].lower()
    assert pkg["sources"] == []


def test_package_with_results_ranks_them():
    outcome = WebFallbackOutcome(
        WEB_LOOKUP_RESULTS,
        "tavily",
        [{"title": "Example deal", "url": "https://example.com/deals", "price": "$19.99", "snippet": "s", "verified": "true"}],
    )
    pkg = build_package(
        query="example deal", response_text="", backend="zoeAgent",
        web_fallback_results=outcome.results, web_lookup=outcome.as_dict(),
    )
    assert pkg["web_lookup"]["status"] == WEB_LOOKUP_RESULTS
    assert pkg["results"][0]["name"] == "Example deal"
    assert pkg["results"][0]["url"] == "https://example.com/deals"
    assert pkg["sources"] == ["https://example.com/deals"]


def test_package_without_lookup_keeps_legacy_shape():
    """Callers that never attempted a lookup (no web_lookup) are unchanged."""
    pkg = build_package(query="example deal", response_text="", backend="zoeAgent")
    assert pkg["web_lookup"] == {}
    assert pkg["sources"] and "duckduckgo.com/?q=" in pkg["sources"][0]
    assert pkg["results"][0]["name"] == "Option 1"


# ── 6. chat router: a failed/off lookup captures NO screenshot ────────────────
# `_build_research_package` used to hand an empty `sources` list to the
# screenshot step, whose fallback navigated the browser to a DuckDuckGo search
# URL — so `off` still sent the query out, and a search-page capture could be
# shown as "evidence" beside the failure message (review P1s on #1691).

@pytest.fixture
def chat_router():
    return pytest.importorskip("routers.chat", reason="needs service modules")


def _spy_capture(monkeypatch, chat_router):
    calls: list[str] = []

    async def _capture(*, query, candidate_source, user_id, session_id):
        calls.append(candidate_source)
        return "aW1n", candidate_source or "https://duckduckgo.com/?q=leak"

    monkeypatch.setattr(chat_router, "_capture_research_screenshot", _capture)
    return calls


def _build(chat_router):
    import asyncio

    return asyncio.run(
        chat_router._build_research_package(
            query="cheapest flights to bali",
            response_text="",
            backend="zoeAgent",
            user_id="u",
            session_id="s",
        )
    )


def test_off_builds_package_with_no_request_and_no_capture(monkeypatch, chat_router):
    monkeypatch.setenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, "off")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    _forbid_tavily_transport(monkeypatch)
    ddg = _serve_ddg(monkeypatch, RESULTS_PAGE)
    captures = _spy_capture(monkeypatch, chat_router)
    pkg = _build(chat_router)
    assert pkg["web_lookup"]["status"] == WEB_LOOKUP_OFF
    assert ddg["n"] == 0, "off must send nothing to DuckDuckGo"
    assert captures == [], "off must not navigate a browser to a search page either"
    assert pkg["sources"] == [] and pkg["screenshots"] == []


def test_blocked_lookup_captures_no_search_page_as_evidence(monkeypatch, chat_router):
    _serve_ddg(monkeypatch, CHALLENGE_PAGE, status=202)
    captures = _spy_capture(monkeypatch, chat_router)
    pkg = _build(chat_router)
    assert pkg["web_lookup"]["status"] == WEB_LOOKUP_BLOCKED
    assert captures == [] and pkg["screenshots"] == []


def test_positive_control_results_still_capture_the_first_source(monkeypatch, chat_router):
    """Proves the spy is live: with real rows the capture DOES run, against the
    first result — never against a duckduckgo.com search URL."""
    _serve_ddg(monkeypatch, RESULTS_PAGE)
    captures = _spy_capture(monkeypatch, chat_router)
    pkg = _build(chat_router)
    assert pkg["web_lookup"]["status"] == WEB_LOOKUP_RESULTS
    assert captures == ["https://example.com/deals"]
    assert pkg["screenshots"] and pkg["screenshots"][0]["source_url"] == "https://example.com/deals"


def test_reply_with_its_own_source_is_still_captured_when_lookup_is_off(monkeypatch, chat_router):
    """A reply citing one real URL (no price) still asks for enrichment; when
    that lookup is off/blocked the reply's OWN source stays in `sources` and must
    still be photographed — the lookup outcome governs the honest row, not the
    evidence for sources the reply already cites (Codex P2, #1691)."""
    import asyncio

    monkeypatch.setenv(re_mod.WEB_FALLBACK_PROVIDER_ENV, "off")
    ddg = _serve_ddg(monkeypatch, RESULTS_PAGE)
    captures = _spy_capture(monkeypatch, chat_router)
    pkg = asyncio.run(
        chat_router._build_research_package(
            query="cheapest flights to bali",
            response_text="See https://example.com/bali-fares for current fares.",
            backend="zoeAgent",
            user_id="u",
            session_id="s",
        )
    )
    assert pkg["web_lookup"]["status"] == WEB_LOOKUP_OFF and ddg["n"] == 0
    assert pkg["sources"] == ["https://example.com/bali-fares"]
    assert captures == ["https://example.com/bali-fares"]
    assert pkg["screenshots"] and pkg["screenshots"][0]["source_url"] == "https://example.com/bali-fares"
