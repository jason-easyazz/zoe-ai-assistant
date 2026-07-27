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


def test_quota_exhausted_falls_back(monkeypatch):
    """429 is an expected operational state — return [] so DDG takes over."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(429))
    assert wsp.tavily_search_sync("q") == []


def test_bad_key_falls_back(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-bad")
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(401))
    assert wsp.tavily_search_sync("q") == []


def test_network_error_falls_back(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    import httpx
    def boom(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "post", boom)
    assert wsp.tavily_search_sync("q") == []


def test_happy_path_returns_mapped_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    import httpx
    captured = {}
    def fake_post(url, **kw):
        captured["url"] = url
        captured["auth"] = kw["headers"]["Authorization"]
        captured["json"] = kw["json"]
        return _Resp(200, {"results": [{"title": "A", "url": "https://a.test", "content": "c"}]})
    monkeypatch.setattr(httpx, "post", fake_post)

    out = wsp.tavily_search_sync("bali flights", max_results=6)
    assert out == [{"title": "A", "href": "https://a.test", "body": "c"}]
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["auth"].startswith("Bearer ")          # verified API contract
    assert captured["json"]["query"] == "bali flights"
    assert captured["json"]["search_depth"] == "basic"     # 1 credit, not 2


def test_max_results_is_clamped_to_api_range(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    import httpx
    seen = {}
    def fake_post(url, **kw):
        seen["n"] = kw["json"]["max_results"]
        return _Resp(200, {"results": []})
    monkeypatch.setattr(httpx, "post", fake_post)
    wsp.tavily_search_sync("q", max_results=999)
    assert seen["n"] == 20        # API max
    wsp.tavily_search_sync("q", max_results=0)
    assert seen["n"] == 1         # API min


def test_empty_query_never_spends_a_credit(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    import httpx
    def must_not_call(*a, **k):
        raise AssertionError("should not spend a credit on an empty query")
    monkeypatch.setattr(httpx, "post", must_not_call)
    assert wsp.tavily_search_sync("   ") == []
