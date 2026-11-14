"""The web-lookup provider seam: a query leaves only through the scrubber, under caps.

FakeProvider is the only provider most tests touch. The two real fetchers are
proved against local stand-ins: the DuckDuckGo fetch against a loopback HTTP
server reached through the real ``guarded_urlopen`` (name resolution and the
socket are pointed at it; the redirect handling is the real code), Tavily
through ``httpx.MockTransport``. Synthetic data only.
"""
from __future__ import annotations

import http.server
import socket
import threading
import time
import urllib.error

import httpx
import pytest

import agent_safety
import research_evidence as re_mod
import web_lookup_provider as wp
import web_search_provider as tavily
from research_evidence import WebFallbackOutcome
from web_lookup_scrub import PrivateTerms, ScrubResult, scrub_query

pytestmark = pytest.mark.ci_safe

ROW = {"title": "Example Almanac", "url": "https://almanac.example.org/tides", "snippet": "High tide is at 6:12."}
OK = ScrubResult(True, "high tide today", "ok")


def test_a_bare_string_cannot_be_sent():
    fake = wp.FakeProvider(default=[ROW])
    with pytest.raises(TypeError):
        wp.lookup(fake, "high tide today")  # type: ignore[arg-type]
    assert fake.queries == []


def test_a_refused_scrub_never_reaches_the_provider():
    fake = wp.FakeProvider(default=[ROW])
    scrub = scrub_query("how old is Tobiah", PrivateTerms.of(names=["Tobiah"]))
    out = wp.lookup(fake, scrub)
    assert (out.status, out.provider, out.results, out.detail) == ("refused", "none", [], "query withheld (private_only)")
    assert fake.queries == []
    forged = ScrubResult(False, "tobiah age", "private_only")  # send=False wins even when a query is present
    assert wp.lookup(fake, forged).status == "refused" and fake.queries == []


def test_only_the_scrubbed_query_is_handed_over():
    fake = wp.FakeProvider(script={"shellfish allergy dangerous": [ROW]})
    scrub = scrub_query("is Mirabel's shellfish allergy dangerous", PrivateTerms.of(names=["Mirabel"]))
    out = wp.lookup(fake, scrub)
    assert out.status == "results" and out.results == [ROW]
    assert fake.queries == ["shellfish allergy dangerous"]


def test_no_results_and_provider_statuses_pass_through_honestly():
    assert wp.lookup(wp.FakeProvider(), OK).status == "no_results"
    blocked = wp.FakeProvider(default=WebFallbackOutcome("blocked", "duckduckgo", [ROW], "HTTP 202"))
    out = wp.lookup(blocked, OK)
    assert (out.status, out.results, out.detail) == ("blocked", [], "HTTP 202")  # rows never ride a non-result


def test_a_provider_that_raises_is_an_honest_error_without_its_message():
    out = wp.lookup(wp.FakeProvider(default=RuntimeError("secret token tvly-abc in message")), OK)
    assert (out.status, out.detail) == ("error", "RuntimeError")


def test_a_hanging_provider_is_cut_off_at_the_deadline():
    release = threading.Event()
    fake = wp.FakeProvider(default=lambda q: release.wait(5) or [ROW])
    t0 = time.monotonic()
    out = wp.lookup(fake, OK, deadline_s=0.1)
    assert time.monotonic() - t0 < 1.0
    assert out.status == "error" and out.detail.startswith("timeout") and out.results == []
    release.set()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://localhost/x", "https://192.168.1.10/", "http://10.0.0.5:8080/", "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/", "http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://router.lan/",
    "https://printer.local/", "http://intranet/", "ftp://files.example.org/a", "javascript:alert(1)", "file:///etc/passwd",
    "https://user:pw@example.org/", "https://example.org/a b", "", None, "https://" + "a" * 600 + ".example.org/",
])
def test_a_row_without_a_public_link_is_dropped(url):
    assert wp.public_link(url) == ""
    assert wp.bound_rows([{**ROW, "url": url}]) == []


def test_public_links_survive():
    for url in ("https://almanac.example.org/tides?d=1", "http://example.com/", "https://93.184.216.34/page"):
        assert wp.public_link(url) == url


def test_what_crosses_back_is_capped():
    big = [{"title": "T" * 5000, "url": f"https://site{i}.example.org/", "snippet": "S" * 50000, "raw_html": "<x>"} for i in range(40)]
    rows = wp.lookup(wp.FakeProvider(default=big), OK).results
    assert 1 <= len(rows) <= wp.MAX_ROWS
    assert all(set(r) == {"title", "url", "snippet"} for r in rows)
    assert all(len(r["title"]) <= wp.MAX_TITLE_CHARS and len(r["snippet"]) <= wp.MAX_SNIPPET_CHARS for r in rows)
    assert sum(len(v) for r in rows for v in r.values()) <= wp.MAX_PAYLOAD_CHARS
    assert wp.lookup(wp.FakeProvider(default=[ROW] * 9), OK, max_results=2).results == [ROW, ROW]
    only_private = [{**ROW, "url": "http://192.168.0.1/"}]
    assert wp.lookup(wp.FakeProvider(default=only_private), OK).status == "no_results"


def test_the_live_adapter_is_behind_the_flag(monkeypatch):
    called = []
    monkeypatch.setattr(wp, "fetch_web_fallback", lambda *a, **k: called.append((a, k)) or WebFallbackOutcome("results", "tavily", [ROW]))
    monkeypatch.delenv("ZOE_WEB_SEARCH_TOOL", raising=False)
    off = wp.provider_for_env()
    assert isinstance(off, wp.OffProvider) and wp.lookup(off, OK).status == "off" and called == []
    monkeypatch.setenv("ZOE_WEB_SEARCH_TOOL", "1")
    out = wp.lookup(wp.provider_for_env(), OK)
    assert out.status == "results" and out.results == [ROW]
    (args, kwargs), = called
    assert args == ("high tide today", wp.MAX_ROWS, re_mod.WEB_SEARCH_TOOL_PROVIDER_TIMEOUT_S)
    assert kwargs == {"enrich_prices": False, "deadline_s": re_mod.WEB_SEARCH_TOOL_DEADLINE_S,
                      "max_redirects": wp.MAX_REDIRECTS, "max_response_bytes": wp.MAX_PROVIDER_BYTES}


def test_the_caps_reach_both_fetchers_and_other_callers_are_unchanged(monkeypatch):
    seen = {}
    monkeypatch.setattr(re_mod, "_tavily_configured", lambda: True)
    monkeypatch.setattr(re_mod, "web_fallback_provider", lambda: "auto")
    monkeypatch.setattr(re_mod, "_fetch_tavily", lambda q, n, t, **k: seen.setdefault("tavily", k) and None or WebFallbackOutcome("error", "tavily"))
    monkeypatch.setattr(re_mod, "_fetch_ddg", lambda q, n, t, **k: seen.setdefault("ddg", k) and None or WebFallbackOutcome("no_results", "duckduckgo"))
    re_mod.fetch_web_fallback("q", 5, 3.0, enrich_prices=False, max_redirects=3, max_response_bytes=1000)
    assert seen == {"tavily": {"enrich_prices": False, "max_bytes": 1000}, "ddg": {"enrich_prices": False, "max_redirects": 3}}
    seen.clear()
    re_mod.fetch_web_fallback("q", 5, 3.0)
    assert seen == {"tavily": {"enrich_prices": True}, "ddg": {"enrich_prices": True}}


# ── the DuckDuckGo fetch: redirects are limited, by the real guarded_urlopen ──


class _Hops(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # /hop/3 -> /hop/2 -> /hop/1 -> /hop/0 (200)
        n = int(self.path.rsplit("/", 1)[1])
        if n > 0:
            self.send_response(302)
            self.send_header("Location", f"http://hop{n - 1}.example.org:{self.server.server_address[1]}/hop/{n - 1}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"arrived"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def hop_server(monkeypatch):
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hops)
    except OSError as exc:  # no loopback in this sandbox
        import os
        if os.environ.get("CI"):
            raise
        pytest.skip(f"loopback unavailable: {exc}")
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    port = server.server_address[1]
    import ipaddress
    monkeypatch.setattr(agent_safety, "resolve_ips", lambda host: [ipaddress.ip_address("93.184.216.34")])
    real_connect = socket.create_connection
    monkeypatch.setattr(agent_safety.socket, "create_connection", lambda addr, *a, **k: real_connect(("127.0.0.1", port), *a, **k))
    yield port
    server.shutdown()
    server.server_close()


def test_redirects_are_limited(hop_server):
    url = "http://start.example.org:%d/hop/%d"
    with agent_safety.guarded_urlopen(url % (hop_server, 3), timeout=2, max_redirects=3) as resp:
        assert resp.read() == b"arrived"
    with pytest.raises(urllib.error.HTTPError) as exc:
        agent_safety.guarded_urlopen(url % (hop_server, 4), timeout=2, max_redirects=3)
    assert "redirect cap (3)" in str(exc.value)
    with pytest.raises(urllib.error.HTTPError):
        agent_safety.guarded_urlopen(url % (hop_server, 1), timeout=2, max_redirects=0)
    with agent_safety.guarded_urlopen(url % (hop_server, 6), timeout=2) as resp:  # uncapped callers: as before
        assert resp.read() == b"arrived"


def test_a_redirect_to_a_private_address_is_still_refused(hop_server, monkeypatch):
    import ipaddress
    monkeypatch.setattr(agent_safety, "resolve_ips", lambda host: [ipaddress.ip_address("10.0.0.7" if host.startswith("hop0") else "93.184.216.34")])
    with pytest.raises(agent_safety.SSRFBlocked):
        agent_safety.guarded_urlopen("http://start.example.org:%d/hop/1" % hop_server, timeout=2, max_redirects=3)


def test_the_ddg_fetch_reports_a_redirect_loop_as_an_error_not_as_no_results(hop_server, monkeypatch):
    monkeypatch.setattr(re_mod, "quote_plus", lambda q: q)
    real = agent_safety.guarded_urlopen
    monkeypatch.setattr(re_mod, "guarded_urlopen", lambda url, **k: real("http://start.example.org:%d/hop/9" % hop_server, **k))
    out = re_mod._fetch_ddg("high tide today", 5, 2.0, enrich_prices=False, max_redirects=wp.MAX_REDIRECTS)
    assert out.status in ("error", "blocked") and out.results == []


# ── Tavily: body cap, and no redirect is followed ────────────────────────────


def _tavily(monkeypatch, handler):
    calls = []
    real = httpx.Client

    def wrapped(request):
        calls.append(request)
        return handler(request)

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("ZOE_SEARCH_PROVIDER", "auto")
    monkeypatch.setattr(agent_safety, "assert_public_url", lambda u: u)
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(wrapped), **kw))
    return calls


def test_tavily_body_is_capped(monkeypatch):
    rows = [{"title": "Example Almanac", "url": "https://almanac.example.org/tides", "content": "x" * 400}]
    _tavily(monkeypatch, lambda req: httpx.Response(200, json={"results": rows}))
    assert tavily.tavily_search_outcome("high tide today", 5, 2.0, max_bytes=100) == ("error", [])
    status, got = tavily.tavily_search_outcome("high tide today", 5, 2.0, max_bytes=10_000)
    assert status == "results" and got[0]["href"] == "https://almanac.example.org/tides"
    assert tavily.tavily_search_outcome("high tide today", 5, 2.0)[0] == "results"  # uncapped callers: as before


def test_tavily_follows_no_redirect(monkeypatch):
    calls = _tavily(monkeypatch, lambda req: httpx.Response(302, headers={"Location": "http://169.254.169.254/"}))
    for cap in (None, 10_000):
        assert tavily.tavily_search_outcome("high tide today", 5, 2.0, max_bytes=cap) == ("error", [])
    assert len(calls) == 2 and all(str(r.url) == "https://api.tavily.com/search" for r in calls)
