"""B10.1 — the flag-gated brain-callable web search (`ZOE_WEB_SEARCH_TOOL`).

Three surfaces, keyed on ONE env var, asserted in BOTH directions of the flag:

  1. `POST /api/system/web-search` (`routers/system.py`) — absent (404) by
     default and never touches the lookup; on, it is `fetch_web_fallback`
     (B10.0) with its outcome `status` verbatim + ≤5 title/url/snippet rows.
  2. `GET /api/system/status` `web_lookup.tool_enabled` (`web_lookup_status`).
  3. The capability prose (`agent_sync`) advertises `web_search` only when on
     — off, it is byte-identical to the B0.14 honest text.

Query privacy: the query text never reaches any log record — proven with a
caplog tripwire that is itself validated by a negative control (an injected
leak IS caught). ci_safe: no sockets — the provider fetches are faked at
`_fetch_ddg` / `_fetch_tavily`.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

import agent_sync
import research_evidence as re_mod
import routers.system as system
from research_evidence import (
    WEB_LOOKUP_BLOCKED,
    WEB_LOOKUP_ERROR,
    WEB_LOOKUP_NO_RESULTS,
    WEB_LOOKUP_OFF,
    WEB_LOOKUP_RESULTS,
    WebFallbackOutcome,
    web_lookup_status,
    web_search_tool_enabled,
)

pytestmark = pytest.mark.ci_safe

QUERY = "tickets to Bali at the moment ZOEPRIVATE"
FLAG = "ZOE_WEB_SEARCH_TOOL"


def _rows(n: int) -> list[dict[str, str]]:
    return [
        {"title": f"t{i}", "url": f"https://example.com/{i}", "snippet": f"s{i}", "price": "$1", "extra": "x"}
        for i in range(n)
    ]


@pytest.fixture
def _no_network(monkeypatch):
    """Belt and braces: the lookup itself is faked below, but if a test ever
    reaches the real provider fetches they must not open a socket."""
    monkeypatch.setattr(re_mod, "_fetch_tavily", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tavily touched")))
    monkeypatch.setattr(re_mod, "_fetch_ddg", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ddg touched")))


def _fake_lookup(monkeypatch, outcome: WebFallbackOutcome):
    calls: list[tuple[str, int]] = []

    def _fetch(query, max_results=5, timeout_s=8.0):
        calls.append((query, max_results))
        return outcome

    monkeypatch.setattr(system, "fetch_web_fallback", _fetch)
    return calls


def _body(query: str = QUERY, max_results: int = 5):
    return system._WebSearchBody(query=query, max_results=max_results)


# ── flag parsing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["1", "true", "YES", " on "])
def test_truthy_spellings_enable(monkeypatch, raw):
    monkeypatch.setenv(FLAG, raw)
    assert web_search_tool_enabled() is True


@pytest.mark.parametrize("raw", [None, "", "0", "false", "no", "off", "maybe"])
def test_everything_else_is_off(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, raw)
    assert web_search_tool_enabled() is False


# ── endpoint: OFF (negative control) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_off_endpoint_is_404_and_never_calls_the_lookup(monkeypatch, _no_network):
    monkeypatch.delenv(FLAG, raising=False)
    calls = _fake_lookup(monkeypatch, WebFallbackOutcome(WEB_LOOKUP_RESULTS, "tavily", _rows(1)))
    with pytest.raises(HTTPException) as exc:
        await system.web_search(_body(), None)
    assert exc.value.status_code == 404
    assert "ZOE_WEB_SEARCH_TOOL" in exc.value.detail
    assert calls == []


def test_off_status_block_reports_tool_disabled(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert web_lookup_status()["tool_enabled"] is False


# ── endpoint: ON ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_endpoint_calls_the_fallback_and_returns_the_outcome_shape(monkeypatch, _no_network):
    monkeypatch.setenv(FLAG, "1")
    calls = _fake_lookup(monkeypatch, WebFallbackOutcome(WEB_LOOKUP_RESULTS, "tavily", _rows(2), ""))
    out = await system.web_search(_body(), None)
    assert calls == [(QUERY, 5)]
    assert out["status"] == WEB_LOOKUP_RESULTS
    assert out["provider"] == "tavily"
    assert out["result_count"] == 2
    assert out["message"] == ""
    assert out["detail"] == ""
    # Only title/url/snippet cross the seam — never a row's price scrape or extras.
    assert out["results"] == [
        {"title": "t0", "url": "https://example.com/0", "snippet": "s0"},
        {"title": "t1", "url": "https://example.com/1", "snippet": "s1"},
    ]


@pytest.mark.asyncio
async def test_on_results_are_capped_at_five_and_max_results_is_clamped(monkeypatch, _no_network):
    monkeypatch.setenv(FLAG, "1")
    calls = _fake_lookup(monkeypatch, WebFallbackOutcome(WEB_LOOKUP_RESULTS, "duckduckgo", _rows(9)))
    out = await system.web_search(_body(max_results=50), None)
    assert calls == [(QUERY, 5)]
    assert out["result_count"] == 5
    assert len(out["results"]) == 5
    calls.clear()
    await system.web_search(_body(max_results=0), None)
    assert calls == [(QUERY, 5)]
    calls.clear()
    await system.web_search(_body(max_results=2), None)
    assert calls == [(QUERY, 2)]


@pytest.mark.parametrize(
    "status, detail",
    [
        (WEB_LOOKUP_NO_RESULTS, ""),
        (WEB_LOOKUP_BLOCKED, "challenge page (HTTP 202)"),
        (WEB_LOOKUP_ERROR, "unrecognised page (HTTP 200)"),
        (WEB_LOOKUP_OFF, "ZOE_WEB_FALLBACK_PROVIDER=off"),
    ],
)
@pytest.mark.asyncio
async def test_on_non_result_outcomes_surface_verbatim(monkeypatch, _no_network, status, detail):
    monkeypatch.setenv(FLAG, "1")
    outcome = WebFallbackOutcome(status, "tavily>duckduckgo", [], detail)
    _fake_lookup(monkeypatch, outcome)
    out = await system.web_search(_body(), None)
    assert out["status"] == status
    assert out["detail"] == detail
    assert out["message"] == outcome.message
    assert out["results"] == [] and out["result_count"] == 0


@pytest.mark.asyncio
async def test_on_empty_query_is_400_without_a_lookup(monkeypatch, _no_network):
    monkeypatch.setenv(FLAG, "1")
    calls = _fake_lookup(monkeypatch, WebFallbackOutcome(WEB_LOOKUP_RESULTS, "tavily", _rows(1)))
    with pytest.raises(HTTPException) as exc:
        await system.web_search(_body(query="   "), None)
    assert exc.value.status_code == 400
    assert calls == []


def test_on_status_block_reports_tool_enabled(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    assert web_lookup_status()["tool_enabled"] is True


# ── query privacy: through the REAL fetch_web_fallback, provider faked ───────

@pytest.mark.asyncio
async def test_query_text_never_reaches_a_log_record(monkeypatch, caplog):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("ZOE_WEB_FALLBACK_PROVIDER", "duckduckgo")
    monkeypatch.setattr(
        re_mod, "_fetch_ddg", lambda q, n, t: WebFallbackOutcome(WEB_LOOKUP_BLOCKED, "duckduckgo", [], "challenge page (HTTP 202)")
    )
    with caplog.at_level(logging.DEBUG):
        out = await system.web_search(_body(), None)
    assert out["status"] == WEB_LOOKUP_BLOCKED
    lookup_lines = [r.getMessage() for r in caplog.records if "web_fallback:" in r.getMessage()]
    assert lookup_lines, "the one INFO line per lookup must still be emitted"
    assert f"query_len={len(QUERY)}" in lookup_lines[0]
    for rec in caplog.records:
        assert "ZOEPRIVATE" not in rec.getMessage()
        assert "ZOEPRIVATE" not in str(rec.args)


@pytest.mark.asyncio
async def test_negative_control_the_log_tripwire_catches_a_leak(monkeypatch, caplog):
    """An injected leak IS caught, so the tripwire above is not vacuous."""
    monkeypatch.setenv(FLAG, "1")

    def _leaky(query, max_results=5, timeout_s=8.0):
        logging.getLogger("research_evidence").info("web_fallback: query=%s", query)
        return WebFallbackOutcome(WEB_LOOKUP_NO_RESULTS, "duckduckgo", [], "")

    monkeypatch.setattr(system, "fetch_web_fallback", _leaky)
    with caplog.at_level(logging.DEBUG):
        await system.web_search(_body(), None)
    assert any("ZOEPRIVATE" in r.getMessage() for r in caplog.records)


# ── capability prose ─────────────────────────────────────────────────────────

_BUILDERS = (
    lambda: agent_sync._build_capabilities_md([], [], []),
    lambda: agent_sync._build_zoe_self_md([], [], []),
)


def test_off_prose_is_byte_identical_to_the_honest_b0_14_text(monkeypatch):
    """Negative control for the prose: off → no web_search anywhere, and the
    committed CAPABILITIES.md (the B0.14 snapshot) still matches exactly."""
    monkeypatch.delenv(FLAG, raising=False)
    for build in _BUILDERS:
        assert "web_search" not in build()
    from pathlib import Path

    committed = (Path(__file__).resolve().parents[3] / "CAPABILITIES.md").read_text()
    generated = agent_sync._build_capabilities_md([], [], [])
    sections = lambda t: t.split("## Core Capabilities", 1)[1]  # noqa: E731 - prose after the registry listings
    assert sections(committed) == sections(generated)


def test_on_prose_advertises_the_tool_and_names_the_flag(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    for build in _BUILDERS:
        text = build()
        assert "`web_search`" in text
        assert "ZOE_WEB_SEARCH_TOOL=1" in text
        # Advertised in BOTH the capability list and the escalation guide.
        core, guide = text.split("## Escalation Guide", 1)
        assert "web_search" in core and "web_search" in guide
