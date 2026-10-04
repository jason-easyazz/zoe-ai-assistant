"""browser_broker.search_web — the broker's ONE bounded search entry point
(used by verify_on_challenge). The lookup tier is faked; no network (ci_safe)."""
from __future__ import annotations

import asyncio
import time

import pytest

import browser_broker as bb
import research_evidence as re_

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


def _outcome(status, rows):
    return re_.WebFallbackOutcome(status, "tavily", rows)


def test_results_carry_domains_and_drop_non_http(monkeypatch):
    rows = [
        {"title": "A", "url": "https://www.example-footy.org/a", "snippet": "alpha"},
        {"title": "B", "url": "javascript:alert(1)", "snippet": "bad"},
        {"title": "C", "url": "ftp://files.example.com/x", "snippet": "bad"},
        {"title": "D", "url": "http://stats.example.com/d", "snippet": "delta"},
    ]
    monkeypatch.setattr(re_, "fetch_web_fallback", lambda q, **kw: _outcome("results", rows))
    res = _run(bb.search_web("who won the 1987 grand final"))
    assert res["ok"] is True and res["status"] == "results"
    assert res["domains"] == ["example-footy.org", "stats.example.com"]
    assert [r["domain"] for r in res["results"]] == ["example-footy.org", "stats.example.com"]


def test_all_rows_unusable_is_no_results(monkeypatch):
    monkeypatch.setattr(re_, "fetch_web_fallback",
                        lambda q, **kw: _outcome("results", [{"title": "x", "url": "mailto:a@b.c"}]))
    res = _run(bb.search_web("q"))
    assert res["ok"] is False and res["status"] == "no_results" and res["results"] == []


@pytest.mark.parametrize("status", ["no_results", "blocked", "error", "off"])
def test_lookup_status_is_passed_through(monkeypatch, status):
    monkeypatch.setattr(re_, "fetch_web_fallback", lambda q, **kw: _outcome(status, []))
    res = _run(bb.search_web("q"))
    assert res["ok"] is False and res["status"] == status


def test_hard_wall_returns_timeout(monkeypatch):
    monkeypatch.setattr(re_, "fetch_web_fallback", lambda q, **kw: time.sleep(2.0))
    async def timed():
        t0 = time.monotonic()
        res = await bb.search_web("q", timeout_s=0.5)
        return res, time.monotonic() - t0

    res, took = _run(timed())  # (asyncio.run then joins the worker thread; the awaited call did not)
    assert res["status"] == "timeout" and res["ok"] is False
    assert took < 1.5  # bound + the 0.5 s wall, not the provider's 2 s


def test_provider_exception_never_raises(monkeypatch):
    def boom(q, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(re_, "fetch_web_fallback", boom)
    assert _run(bb.search_web("q"))["status"] == "error"


def test_empty_query_makes_no_call(monkeypatch):
    called = []
    monkeypatch.setattr(re_, "fetch_web_fallback", lambda q, **kw: called.append(q))
    assert _run(bb.search_web("   "))["status"] == "no_results" and called == []


def test_one_search_with_the_bound_and_no_price_enrichment(monkeypatch):
    seen = {}

    def spy(q, **kw):
        seen.update(kw, q=q)
        return _outcome("results", [{"title": "t", "url": "https://a.example.org/x", "snippet": "s"}])

    monkeypatch.setattr(re_, "fetch_web_fallback", spy)
    _run(bb.search_web("who won", timeout_s=7.4))
    assert seen["q"] == "who won" and seen["enrich_prices"] is False
    assert seen["deadline_s"] == 7.4 and seen["timeout_s"] == 7.4
