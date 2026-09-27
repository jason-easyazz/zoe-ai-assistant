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
import re
import time
from pathlib import Path

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

    def _fetch(query, max_results=5, timeout_s=8.0, **kwargs):
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
        re_mod, "_fetch_ddg", lambda q, n, t, **kw: WebFallbackOutcome(WEB_LOOKUP_BLOCKED, "duckduckgo", [], "challenge page (HTTP 202)")
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

    def _leaky(query, max_results=5, timeout_s=8.0, **kwargs):
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


_LIVE_BUILDERS = (
    lambda: agent_sync._build_capabilities_md([], [], [], web_search_live=True),
    lambda: agent_sync._build_zoe_self_md([], [], [], web_search_live=True),
)


def test_on_prose_advertises_the_tool_and_names_the_flag(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    for build in _LIVE_BUILDERS:
        text = build()
        assert "`web_search`" in text
        assert "ZOE_WEB_SEARCH_TOOL=1" in text
        # Advertised in BOTH the capability list and the escalation guide.
        core, guide = text.split("## Escalation Guide", 1)
        assert "web_search" in core and "web_search" in guide



@pytest.mark.parametrize("zoe_data_on,brain_live", [(True, False), (False, True)])
def test_partial_rollout_makes_no_claim(monkeypatch, zoe_data_on, brain_live):
    """Greptile #1702: either half alone (zoe-data flag / sidecar confirmation)
    is a partial rollout — no claim."""
    monkeypatch.setenv(FLAG, "1") if zoe_data_on else monkeypatch.delenv(FLAG, raising=False)
    assert "web_search" not in agent_sync._build_capabilities_md([], [], [], web_search_live=brain_live)
    assert "web_search" not in agent_sync._build_zoe_self_md([], [], [], web_search_live=brain_live)


def _fake_brain_health(monkeypatch, reply):
    """Brain /health GET answers `reply` — (status, JSON body | raw bytes) — or raises it."""
    import httpx

    seen: list[str] = []

    async def _get(self, url, *a, **k):
        seen.append(url)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, content=body) if isinstance(body, bytes) else httpx.Response(status, json=body)

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    return seen


@pytest.mark.asyncio
async def test_brain_probe_is_never_made_while_zoe_data_flag_is_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    seen = _fake_brain_health(monkeypatch, AssertionError("probed with the flag off"))
    assert await agent_sync._brain_registers_web_search() is False and seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply,expected",
    [
        ((200, {"ok": True, "optional_tools": ["web_search"]}), True),
        ((200, {"ok": True, "service": "flue-zoe-brain"}), False),  # sidecar flag off: field omitted
        ((200, {"optional_tools": []}), False),
        ((503, {"optional_tools": ["web_search"]}), False),
        ((200, b"not json"), False),
        ((200, ["web_search"]), False),
        (OSError("connection refused"), False),
    ],
)
async def test_brain_probe_confirms_only_a_reported_tool(monkeypatch, reply, expected):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3579/")
    seen = _fake_brain_health(monkeypatch, reply)
    assert await agent_sync._brain_registers_web_search() is expected
    assert seen == ["http://127.0.0.1:3579/health"]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", [None, "core", "", "FLUE-ish"])
async def test_no_claim_unless_the_flue_lane_is_the_selected_brain(monkeypatch, backend):
    """Codex #1702: a healthy sidecar that registered web_search is NOT enough —
    with ZOE_BRAIN_BACKEND on `core` (the default, or a rollback) the brain that
    answers cannot call it, so no claim and no probe."""
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", backend) if backend is not None else monkeypatch.delenv("ZOE_BRAIN_BACKEND", raising=False)
    seen = _fake_brain_health(monkeypatch, (200, {"ok": True, "optional_tools": ["web_search"]}))
    assert await agent_sync._brain_registers_web_search() is False
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["flue", " FLUE "])
async def test_flue_lane_selected_and_healthy_sidecar_claims(monkeypatch, backend):
    """Control: the same healthy sidecar IS confirmed once the flue lane is selected."""
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", backend)
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3579/")
    seen = _fake_brain_health(monkeypatch, (200, {"ok": True, "optional_tools": ["web_search"]}))
    assert await agent_sync._brain_registers_web_search() is True
    assert seen == ["http://127.0.0.1:3579/health"]


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_run_agent_sync_gates_the_written_claim_on_the_brain_probe(monkeypatch, tmp_path, confirmed):
    monkeypatch.setenv(FLAG, "1")
    for attr in ("_OPENCLAW_ZOE_SELF", "_HERMES_SOUL", "_ZOE_COMPACT", "_CAPABILITIES_MD"):
        monkeypatch.setattr(agent_sync, attr, tmp_path / attr.strip("_"))

    async def _const(value):
        return value

    for name in ("_collect_mcp_tools", "_collect_openclaw_skills", "_collect_ui_pages"):
        monkeypatch.setattr(agent_sync, name, lambda: _const([]))
    monkeypatch.setattr(agent_sync, "_brain_registers_web_search", lambda: _const(confirmed))
    await agent_sync.run_agent_sync()
    assert ("`web_search`" in (tmp_path / "CAPABILITIES_MD").read_text()) is confirmed


# ── time budget (Codex #1702): the backend must finish inside the tool timeout ──

def _provider_harness(monkeypatch):
    """Tavily errors, DDG answers; each records the timeout + kwargs it got."""
    monkeypatch.setattr(re_mod, "web_fallback_provider", lambda: "auto")
    monkeypatch.setattr(re_mod, "_tavily_configured", lambda: True)
    seen: dict[str, dict] = {}

    def _provider(name, status):
        def _fetch(q, n, timeout_s, **kw):
            seen[name] = {"timeout_s": timeout_s, **kw}
            return WebFallbackOutcome(status, name, _rows(1) if status == WEB_LOOKUP_RESULTS else [])

        return _fetch

    monkeypatch.setattr(re_mod, "_fetch_tavily", _provider("tavily", WEB_LOOKUP_ERROR))
    monkeypatch.setattr(re_mod, "_fetch_ddg", _provider("ddg", WEB_LOOKUP_RESULTS))
    return seen


def test_tool_settings_reach_both_providers(monkeypatch):
    seen = _provider_harness(monkeypatch)
    re_mod.fetch_web_fallback(QUERY, 5, re_mod.WEB_SEARCH_TOOL_PROVIDER_TIMEOUT_S, enrich_prices=False)
    for name in ("tavily", "ddg"):
        assert seen[name] == {"timeout_s": re_mod.WEB_SEARCH_TOOL_PROVIDER_TIMEOUT_S, "enrich_prices": False}


def test_default_callers_keep_the_b10_0_behaviour(monkeypatch):
    """Negative control (chat's B10.0 path): full timeout per provider, prices on."""
    seen = _provider_harness(monkeypatch)
    re_mod.fetch_web_fallback(QUERY, 5)
    for name in ("tavily", "ddg"):
        assert seen[name]["timeout_s"] == 8.0 and seen[name].get("enrich_prices", True) is True


def test_verify_rows_without_enrichment_never_fetches_a_page(monkeypatch):
    monkeypatch.setattr(re_mod, "_fetch_page_price", lambda *a, **k: (_ for _ in ()).throw(AssertionError("page fetched")))
    rows = [{"title": "Bali tickets", "url": "https://example.com/bali", "snippet": "tickets to Bali", "price": ""}]
    assert [r["url"] for r in re_mod._verify_rows("tickets to Bali", rows, 8.0, enrich_prices=False)] == [rows[0]["url"]]


@pytest.mark.asyncio
async def test_endpoint_runs_the_lookup_on_the_tool_budget(monkeypatch, _no_network):
    monkeypatch.setenv(FLAG, "1")
    calls: list[tuple] = []
    monkeypatch.setattr(system, "fetch_web_fallback", lambda *a, **k: calls.append((a, k)) or WebFallbackOutcome(WEB_LOOKUP_RESULTS, "tavily", _rows(1)))
    await system.web_search(_body(), None)
    assert calls == [
        (
            (QUERY, 5, re_mod.WEB_SEARCH_TOOL_PROVIDER_TIMEOUT_S),
            {"enrich_prices": False, "deadline_s": re_mod.WEB_SEARCH_TOOL_DEADLINE_S},
        )
    ]


# ── one end-to-end deadline (Greptile + Codex #1702) ─────────────────────────
# The per-provider timeout is a per-OPERATION socket timeout, and Tavily + DDG
# run in sequence, so a slow-drip provider could outlast the sidecar. One
# monotonic budget is shared across both attempts and the endpoint answers
# honestly when it is spent. Real (short) sleeps, patched-down budgets.


def _timed_provider_harness(monkeypatch, *, tavily_sleep: float, ddg_sleep: float = 0.0):
    monkeypatch.setattr(re_mod, "web_fallback_provider", lambda: "auto")
    monkeypatch.setattr(re_mod, "_tavily_configured", lambda: True)
    seen: dict[str, dict] = {}

    def _provider(name, status, sleep_s):
        def _fetch(q, n, timeout_s, **kw):
            seen[name] = {"timeout_s": timeout_s, **kw}
            time.sleep(sleep_s)
            return WebFallbackOutcome(status, name, _rows(1) if status == WEB_LOOKUP_RESULTS else [])

        return _fetch

    monkeypatch.setattr(re_mod, "_fetch_tavily", _provider("tavily", WEB_LOOKUP_ERROR, tavily_sleep))
    monkeypatch.setattr(re_mod, "_fetch_ddg", _provider("ddg", WEB_LOOKUP_RESULTS, ddg_sleep))
    return seen


def test_spent_budget_skips_the_second_provider_and_says_so(monkeypatch):
    seen = _timed_provider_harness(monkeypatch, tavily_sleep=0.4)
    t0 = time.monotonic()
    out = re_mod.fetch_web_fallback(QUERY, 5, 3.0, enrich_prices=False, deadline_s=0.3)
    assert time.monotonic() - t0 < 1.0
    assert "ddg" not in seen, "DDG must not start once the shared budget is spent"
    assert out.status == WEB_LOOKUP_ERROR and out.results == []
    assert "deadline" in out.detail and out.provider == "tavily"


def test_second_provider_gets_only_the_remaining_budget(monkeypatch):
    seen = _timed_provider_harness(monkeypatch, tavily_sleep=0.3)
    out = re_mod.fetch_web_fallback(QUERY, 5, 3.0, enrich_prices=False, deadline_s=1.5)
    assert out.status == WEB_LOOKUP_RESULTS
    assert seen["tavily"]["timeout_s"] <= 1.5
    assert 0 < seen["ddg"]["timeout_s"] <= 1.5 - 0.3 + 0.05


def test_fast_providers_are_unchanged_by_the_deadline(monkeypatch):
    """Control: well inside the budget both providers get the full per-op timeout."""
    seen = _timed_provider_harness(monkeypatch, tavily_sleep=0.0)
    out = re_mod.fetch_web_fallback(QUERY, 5, 3.0, enrich_prices=False, deadline_s=re_mod.WEB_SEARCH_TOOL_DEADLINE_S)
    assert out.status == WEB_LOOKUP_RESULTS and out.provider == "ddg"
    assert seen["tavily"]["timeout_s"] == 3.0 and seen["ddg"]["timeout_s"] == 3.0


@pytest.mark.asyncio
async def test_endpoint_answers_within_the_deadline_when_a_provider_hangs(monkeypatch, _no_network):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setattr(system, "WEB_SEARCH_TOOL_DEADLINE_S", 0.3)
    monkeypatch.setattr(system, "fetch_web_fallback", lambda *a, **k: time.sleep(1.0) or WebFallbackOutcome(WEB_LOOKUP_RESULTS, "tavily", _rows(1)))
    t0 = time.monotonic()
    out = await system.web_search(_body(), None)
    assert time.monotonic() - t0 < 0.8
    assert out["status"] == WEB_LOOKUP_ERROR and out["results"] == [] and out["result_count"] == 0
    assert "timeout" in out["detail"]


@pytest.mark.asyncio
async def test_endpoint_returns_a_fast_lookup_untouched(monkeypatch, _no_network):
    """Control: a lookup inside the budget is returned verbatim, no timeout."""
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setattr(system, "WEB_SEARCH_TOOL_DEADLINE_S", 0.5)
    _fake_lookup(monkeypatch, WebFallbackOutcome(WEB_LOOKUP_RESULTS, "tavily", _rows(2)))
    out = await system.web_search(_body(), None)
    assert out["status"] == WEB_LOOKUP_RESULTS and out["result_count"] == 2 and out["detail"] == ""


def test_brain_tool_deadline_outlasts_the_backend_worst_case():
    """Cross-language pin: the sidecar's web_search deadline floor must exceed
    the backend's end-to-end deadline (+ margin), or the brain says
    "unreachable" while the lookup still runs."""
    ts = (Path(__file__).resolve().parents[3] / "labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts").read_text()
    m = re.search(r"const WEB_SEARCH_TIMEOUT_FLOOR_MS = (\d+);", ts)
    assert m and int(m.group(1)) >= (re_mod.WEB_SEARCH_TOOL_DEADLINE_S + 1.5) * 1000
    assert re_mod.WEB_SEARCH_TOOL_PROVIDER_TIMEOUT_S <= re_mod.WEB_SEARCH_TOOL_DEADLINE_S
    assert "fetchSignal(signal, Math.max(httpTimeoutMs(), WEB_SEARCH_TIMEOUT_FLOOR_MS))" in ts
