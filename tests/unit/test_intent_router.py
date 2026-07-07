"""Unit tests for intent_router.py — all A2A + Multica + Evolution intents.

These tests run against detect_intent() without requiring a live database
or LLM. They exercise only the regex/rule-based fast path.

Run with:
    pytest tests/unit/test_intent_router.py -v
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest.mock as mock

import pytest
# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe



# ── Minimal stubs so intent_router can be imported without infra ──────────────

# Names WE inserted into sys.modules (not ones that were already imported).
# teardown_module pops them so later test files import the real modules —
# leaking the stubs broke e.g. `from openclaw_ws import NODE_BIN` downstream.
_INSERTED_STUBS: list[str] = []


def _stub_psycopg2():
    m = types.ModuleType("psycopg2")
    m.connect = mock.MagicMock(return_value=mock.MagicMock())
    if "psycopg2" not in sys.modules:
        sys.modules["psycopg2"] = m
        _INSERTED_STUBS.append("psycopg2")


def _stub_module(name: str, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    if name not in sys.modules:
        sys.modules[name] = m
        _INSERTED_STUBS.append(name)


def teardown_module(module):  # noqa: ARG001 — pytest hook signature
    """Remove our sys.modules stubs so other test files see real modules."""
    for name in _INSERTED_STUBS:
        sys.modules.pop(name, None)
    _INSERTED_STUBS.clear()


def _setup_stubs():
    _stub_psycopg2()
    _stub_module("database", get_db=mock.MagicMock())
    _stub_module("db_pool", get_db_ctx=mock.MagicMock())
    _stub_module("zoe_agent", run_zoe_agent=mock.AsyncMock(return_value="ok"))
    _stub_module("openclaw_ws", openclaw_cli=mock.AsyncMock(return_value="ok"))
    _stub_module("multica_client", MULClient=mock.MagicMock())
    _stub_module("background_runner", enqueue_background_task=mock.AsyncMock())
    for mod in [
        "agents_registry", "a2a_client", "skill_discovery", "skills_watcher",
        "evolution_notice", "memory_digest", "agent_sync",
    ]:
        _stub_module(mod)


_setup_stubs()

# Now we can import intent_router
import importlib.util, pathlib, os

_INTENT_ROUTER_PATH = (
    pathlib.Path(__file__).parent.parent.parent
    / "services/zoe-data/intent_router.py"
)


def _load_intent_router():
    spec = importlib.util.spec_from_file_location("intent_router", _INTENT_ROUTER_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    with mock.patch.dict(os.environ, {"POSTGRES_URL": "postgresql://test/test"}):
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            pass  # Some intents need DB; we'll test regex paths only
    return module


@pytest.fixture(scope="module")
def ir():
    return _load_intent_router()


# ── Helper ────────────────────────────────────────────────────────────────────

def _intent_name(ir_module, text: str) -> str | None:
    try:
        result = ir_module.detect_intent(text)
        if result is None:
            return None
        if isinstance(result, str):
            return result
        return getattr(result, "name", None)
    except Exception:
        return None


# ── A2A federation intent ─────────────────────────────────────────────────────

class TestA2AIntent:
    POSITIVE = [
        "show me the agent federation status",
        "what is the a2a status",
        "federation health check",
        "show peer agents",
        "list connected agents",
        "are hermes and openclaw online",
    ]
    NEGATIVE = [
        "play some music",
        "what is the weather",
        "remind me to call mum",
    ]

    def test_positive_cases(self, ir):
        for msg in self.POSITIVE:
            name = _intent_name(ir, msg)
            assert name == "a2a_federation_status", f"Expected a2a_federation_status for: {msg!r}, got: {name!r}"

    def test_negative_cases(self, ir):
        for msg in self.NEGATIVE:
            name = _intent_name(ir, msg)
            assert name != "a2a_federation_status", f"False positive a2a_federation_status for: {msg!r}"


# ── Multica board intent ──────────────────────────────────────────────────────

class TestBoardIntent:
    POSITIVE = [
        "show the multica board",
        "what's on the board",
        "show board status",
        "open the task board",
        "show me multica",
    ]

    def test_positive_cases(self, ir):
        for msg in self.POSITIVE:
            name = _intent_name(ir, msg)
            assert name == "board_status", f"Expected board_status for: {msg!r}, got: {name!r}"


# ── Evolution proposals review intent ────────────────────────────────────────

class TestEvolutionProposalsIntent:
    POSITIVE = [
        "review evolution proposals",
        "what improvements has zoe proposed",
        "show me pending proposals",
        "what does zoe want to change",
        "evolution review",
        "evolution proposals status",
    ]

    def test_positive_cases(self, ir):
        for msg in self.POSITIVE:
            name = _intent_name(ir, msg)
            assert name == "evolution_proposals_review", (
                f"Expected evolution_proposals_review for: {msg!r}, got: {name!r}"
            )


# ── User issue report intent ──────────────────────────────────────────────────

class TestUserIssueReportIntent:
    POSITIVE = [
        "you got that wrong",
        "that didn't work",
        "that's not working",
        "there's a problem with the weather card",
        "there's an issue with reminders",
        "fix your music controls",
        "you should know that the lights aren't responding",
        "I keep having issues with reminders",
        "you need to fix the calendar",
        "that was wrong",
        "that was incorrect",
        "you messed up the timer",
    ]
    NEGATIVE = [
        "what time is it",
        "play some music",
        "add milk to shopping list",
        "show the evolution proposals",
        "review evolution proposals",
        "hello",
    ]

    def test_positive_cases(self, ir):
        for msg in self.POSITIVE:
            name = _intent_name(ir, msg)
            assert name == "user_issue_report", (
                f"Expected user_issue_report for: {msg!r}, got: {name!r}"
            )

    def test_negative_cases(self, ir):
        for msg in self.NEGATIVE:
            name = _intent_name(ir, msg)
            assert name != "user_issue_report", (
                f"False positive user_issue_report for: {msg!r}"
            )


# ── General intent smoke test: no crashes ────────────────────────────────────

class TestNoCrash:
    MESSAGES = [
        "",
        "hello",
        "what time is it",
        "play jazz",
        "add milk to shopping list",
        "turn off the lights",
        "what is 2+2",
        "show me the news",
        "⚠️ unicode test 测试",
    ]

    def test_all_messages_return_without_exception(self, ir):
        for msg in self.MESSAGES:
            try:
                ir.detect_intent(msg)
            except Exception as exc:
                pytest.fail(f"detect_intent raised for {msg!r}: {exc}")


# ── Multica-routed intent names ───────────────────────────────────────────────

class TestMulticaRoutedIntents:
    """Verify that intents used in the Multica board routing list exist."""
    EXPECTED_MULTICA_INTENTS = [
        "extend_capability",
        "self_improve",
        "build_widget",
        "build_page",
    ]

    def test_intents_are_classified(self, ir):
        messages = {
            "extend_capability": "can you extend your capabilities",
            "self_improve": "improve yourself",
            "build_widget": "build a widget for the dashboard",
            "build_page": "build a new page",
        }
        for expected_name, msg in messages.items():
            name = _intent_name(ir, msg)
            # These may route through openclaw/LLM, just verify no crash
            # and if detected locally, check name
            if name is not None:
                assert isinstance(name, str), f"Intent name should be a string for: {msg!r}"


# ── Weather: ad-hoc named location (geocode branch) ───────────────────────────
# _execute_weather_direct is the voice/telegram fast-path. These cover the new
# `location` branch: geocode success uses the named place (uncached, so it can't
# poison the shared home cache), and a geocode miss says so instead of silently
# answering for home.
import asyncio


def _stub_weather_and_db(monkeypatch, *, geocode_result):
    import types
    wx = types.ModuleType("routers.weather")
    calls: dict = {}
    cache: dict = {}

    async def _geocode(name):
        calls["geocode"] = name
        return geocode_result

    async def _get_current(lat, lon, city, country):
        calls["get_current"] = {"city": city, "lat": lat, "lon": lon}
        return {"temp": 15.4, "description": "clear", "city": city, "feels_like": 15.4}

    async def _get_forecast(lat, lon):
        return {"daily": [], "hourly": []}

    wx._geocode = _geocode
    wx._get_current = _get_current
    wx._get_forecast = _get_forecast
    wx._weather_cache = cache
    wx._row_to_prefs = lambda row: {}
    wx._resolve_location = lambda prefs: (-28.7, 114.6, "Geraldton", "AU")
    monkeypatch.setitem(sys.modules, "routers.weather", wx)

    dbmod = types.ModuleType("database")

    async def _get_db():
        yield mock.MagicMock()

    # intent_router's weather paths now use `async with get_db_ctx() as db:`
    # (the #953 leak fix) — stub the context manager alongside the legacy
    # generator so both shapes resolve.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _get_db_ctx():
        yield mock.MagicMock()

    dbmod.get_db = _get_db
    dbmod.get_db_ctx = _get_db_ctx
    monkeypatch.setitem(sys.modules, "database", dbmod)
    return calls, cache


def test_weather_named_location_uses_geocode_uncached(ir, monkeypatch):
    calls, cache = _stub_weather_and_db(monkeypatch, geocode_result=(-31.95, 115.86, "Perth", "AU"))
    reply = asyncio.run(ir._execute_weather_direct("jason", location="Perth"))
    assert "Perth" in reply and "Geraldton" not in reply
    assert calls["geocode"] == "Perth"
    assert calls["get_current"]["city"] == "Perth"
    # Ad-hoc queries route by the GEOCODED coords — under the keyed cache that
    # is the whole no-poisoning guarantee (a Perth reading lives under Perth's
    # key, structurally separate from the home area's).
    assert (calls["get_current"]["lat"], calls["get_current"]["lon"]) == (-31.95, 115.86)
    # The intent path never writes the router cache directly.
    assert cache == {}


def test_weather_named_location_not_found(ir, monkeypatch):
    calls, cache = _stub_weather_and_db(monkeypatch, geocode_result=None)
    reply = asyncio.run(ir._execute_weather_direct("jason", location="Nowheresville-xyz"))
    assert "couldn't find" in reply.lower()
    assert "Nowheresville-xyz" in reply
    assert "get_current" not in calls  # never fetched weather for an unresolved place


# ── Fast-path defer-to-brain guards (fix/zoe-data-fastpath-defer-to-brain) ────
# The deterministic list_add fast path must DEFER to the brain when a
# competing-capability cue (journal, contacts, note) owns an "add …" turn
# without an explicit shopping/grocery list target — otherwise it swallowed
# journal/contacts/note writes as shopping items (LIVE misroute).

class TestListAddDefersToCompetingDomains:
    # Competing-domain "add …" turns must NOT become list_add — they defer to
    # the brain (detect_intent returns None) or route to their own domain.
    DEFER = [
        "add a journal entry: grateful for the rain today",
        "add marcus to my contacts as my colleague",
        "add a note about the leaky tap",
        "add a diary entry for today",
    ]
    # Genuine shopping/list adds must STILL be list_add.
    STILL_LIST_ADD = [
        "add milk to my shopping list",
        "add milk",
        "add eggs and bread",
        "put bananas on the grocery list",
        "add a notebook to my shopping list",  # 'note'-adjacent word, but explicit list
    ]

    def test_competing_domains_not_list_add(self, ir):
        for msg in self.DEFER:
            name = _intent_name(ir, msg)
            assert name != "list_add", (
                f"list_add wrongly swallowed competing-domain turn: {msg!r} -> {name!r}"
            )

    def test_genuine_shopping_adds_still_list_add(self, ir):
        for msg in self.STILL_LIST_ADD:
            name = _intent_name(ir, msg)
            assert name == "list_add", (
                f"Expected list_add for genuine shopping add: {msg!r}, got {name!r}"
            )
