"""Tavily search tier — enablement, mapping, and fail-open fallback.

The load-bearing property: with no key (or any failure) it returns [] so the
caller falls through to the existing DDG path — i.e. today's behaviour.
"""
import pytest

import web_search_provider as wsp

pytestmark = pytest.mark.ci_safe


# ── enablement (inert by default) ────────────────────────────────────────────

def test_disabled_without_a_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("ZOE_SEARCH_PROVIDER", raising=False)
    assert wsp.tavily_enabled() is False
    # and the search itself is a no-op, so the caller falls back
    assert wsp.tavily_search_sync("anything") == []


def test_enabled_when_key_present(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.delenv("ZOE_SEARCH_PROVIDER", raising=False)
    assert wsp.tavily_enabled() is True


def test_ddg_provider_is_an_explicit_off_switch(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("ZOE_SEARCH_PROVIDER", "ddg")
    assert wsp.tavily_enabled() is False  # key present but explicitly disabled


def test_blank_key_is_not_a_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "   ")
    assert wsp.tavily_enabled() is False


# ── result mapping ───────────────────────────────────────────────────────────

def test_maps_tavily_shape_onto_ddg_shape():
    out = wsp._to_common([
        {"title": "T", "url": "https://example.com/a", "content": "snippet", "score": 0.9},
    ])
    assert out == [{"title": "T", "href": "https://example.com/a", "body": "snippet"}]


def test_uppercase_scheme_is_accepted():
    """URL schemes are case-insensitive — an HTTPS:// result must not be dropped."""
    out = wsp._to_common([{"title": "T", "url": "HTTPS://Example.com/a", "content": "c"}])
    assert out == [{"title": "T", "href": "HTTPS://Example.com/a", "body": "c"}]


def test_drops_non_http_sources():
    """A citation must never point at a javascript:/data: URL."""
    out = wsp._to_common([
        {"title": "bad", "url": "javascript:alert(1)", "content": "x"},
        {"title": "no url", "content": "x"},
        {"title": "ok", "url": "https://example.com", "content": "y"},
        "not-a-dict",
    ])
    assert out == [{"title": "ok", "href": "https://example.com", "body": "y"}]


# ── fail-open behaviour (the safety property) ────────────────────────────────

class _Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload or {}
    def json(self):
        return self._payload


class _FakeClient:
    """Stands in for httpx.Client(...) used as a context manager."""
    def __init__(self, responder, captured):
        self._responder, self._captured = responder, captured
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def post(self, url, **kw):
        self._captured.update({"url": url, **kw})
        return self._responder(url, **kw)


@pytest.fixture(autouse=True)
def _no_network_guard(monkeypatch):
    """The SSRF guard resolves DNS for real — stub it so these stay ci_safe
    (CI has no network). Its behaviour is exercised by agent_safety's own tests."""
    import agent_safety
    monkeypatch.setattr(agent_safety, "assert_public_url", lambda u: u)


def _fake_transport(monkeypatch, responder):
    """Patch httpx.Client so the provider's `with httpx.Client(...)` is faked.
    Returns the dict capturing the outbound request."""
    import httpx
    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder, captured))
    return captured


def test_quota_exhausted_falls_back(monkeypatch):
    """429 is an expected operational state — return [] so DDG takes over."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    _fake_transport(monkeypatch, lambda url, **kw: _Resp(429))
    assert wsp.tavily_search_sync("q") == []


def test_bad_key_falls_back(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-bad")
    _fake_transport(monkeypatch, lambda url, **kw: _Resp(401))
    assert wsp.tavily_search_sync("q") == []


def test_network_error_falls_back(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    import httpx
    def boom(url, **kw):
        raise httpx.ConnectError("no route")
    _fake_transport(monkeypatch, boom)
    assert wsp.tavily_search_sync("q") == []


def test_happy_path_returns_mapped_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    captured = _fake_transport(
        monkeypatch,
        lambda url, **kw: _Resp(200, {"results": [{"title": "A", "url": "https://a.test", "content": "c"}]}))

    out = wsp.tavily_search_sync("bali flights", max_results=6)
    assert out == [{"title": "A", "href": "https://a.test", "body": "c"}]
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"]["Authorization"].startswith("Bearer ")   # verified API contract
    assert captured["json"]["query"] == "bali flights"
    assert captured["json"]["search_depth"] == "basic"                  # 1 credit, not 2


def test_max_results_is_clamped_to_api_range(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    seen = _fake_transport(monkeypatch, lambda url, **kw: _Resp(200, {"results": []}))
    wsp.tavily_search_sync("q", max_results=999)
    assert seen["json"]["max_results"] == 20        # API max
    wsp.tavily_search_sync("q", max_results=0)
    assert seen["json"]["max_results"] == 1         # API min


def test_empty_query_never_spends_a_credit(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    def must_not_call(url, **kw):
        raise AssertionError("should not spend a credit on an empty query")
    _fake_transport(monkeypatch, must_not_call)
    assert wsp.tavily_search_sync("   ") == []
