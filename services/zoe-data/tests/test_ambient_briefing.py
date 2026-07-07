"""Ambient composed briefing — facts, fallback tree, cache semantics, endpoint."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import asyncio
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ambient_briefing  # noqa: E402
from auth import get_current_user  # noqa: E402
from routers.skybridge import router  # noqa: E402
from ui_catalog import validate_component_tree  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_briefing_state():
    ambient_briefing._cache.clear()
    ambient_briefing._refreshing.clear()
    ambient_briefing._tasks.clear()
    yield
    # Cancel any background refresh tasks a test left in flight so they can't
    # leak across tests or outlive a closing event loop.
    for _t in list(ambient_briefing._tasks):
        _t.cancel()
    ambient_briefing._cache.clear()
    ambient_briefing._refreshing.clear()
    ambient_briefing._tasks.clear()


async def _drain_refresh_tasks():
    for _ in range(50):
        if not ambient_briefing._tasks:
            return
        await asyncio.sleep(0.02)


# ── Facts gathering ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gather_facts_uses_spoken_summaries_and_skips_failures(monkeypatch):
    async def fake_resolve(message, user_id, **kwargs):
        if "calendar" in message:
            return {"handled": True, "spoken_summary": "You have 2 events today."}
        if "shopping" in message:
            raise RuntimeError("lists resolver down")
        if "weather" in message:
            return {"handled": True, "spoken_summary": "It is 19 degrees and clear."}
        raise AssertionError(f"unexpected query {message!r}")

    monkeypatch.setattr(ambient_briefing, "resolve_skybridge_request", fake_resolve)
    facts = await ambient_briefing.gather_facts("u1")
    assert facts == ["You have 2 events today.", "It is 19 degrees and clear."]


@pytest.mark.asyncio
async def test_gather_facts_skips_auth_required_and_unhandled(monkeypatch):
    async def fake_resolve(message, user_id, **kwargs):
        if "calendar" in message:
            return {"handled": True, "auth_required": True,
                    "spoken_summary": "Please authenticate on the touch panel to continue."}
        if "shopping" in message:
            return {"handled": False, "spoken_summary": ""}
        return {"handled": True, "spoken_summary": "Clear, 19 degrees."}

    monkeypatch.setattr(ambient_briefing, "resolve_skybridge_request", fake_resolve)
    facts = await ambient_briefing.gather_facts("voice-guest")
    assert facts == ["Clear, 19 degrees."]  # no sign-in nags on a resting screen


# ── Static fallback tree ─────────────────────────────────────────────────────

def test_static_fallback_card_validates_against_catalog():
    card = ambient_briefing.build_static_card(
        "Good morning", ["2 events today.", "Milk and eggs are on the list.", "19 degrees and clear."]
    )
    assert card is not None
    assert card["component"] == "compose"
    tree = card["props"]["tree"]
    # Shape: Stack[Text kicker greeting, ListRow per fact].
    assert tree["component"] == "Stack"
    assert tree["children"][0] == {"component": "Text", "text": "Good morning", "role": "kicker"}
    rows = tree["children"][1:]
    assert [row["component"] for row in rows] == ["ListRow"] * 3
    assert rows[0]["title"] == "2 events today."
    # And the tree passes the server-side validator (raises on violation).
    validate_component_tree(tree)


def test_static_fallback_card_is_none_without_facts():
    assert ambient_briefing.build_static_card("Good evening", []) is None


def test_greeting_matches_time_of_day():
    assert ambient_briefing.greeting_for_hour(8) == "Good morning"
    assert ambient_briefing.greeting_for_hour(14) == "Good afternoon"
    assert ambient_briefing.greeting_for_hour(19) == "Good evening"
    assert ambient_briefing.greeting_for_hour(2) == "Hello"


# ── Cache: TTL, stale-serve, background refresh (never waits on the LLM) ────

@pytest.mark.asyncio
async def test_first_call_returns_none_fast_and_warms_cache_in_background(monkeypatch):
    card_a = {"component": "compose", "props": {"tree": {"component": "Stack", "children": []}}}

    async def slow_build(user_id):
        await asyncio.sleep(0.2)  # stand-in for a 5-10s compose_card call
        return card_a

    monkeypatch.setattr(ambient_briefing, "build_briefing_card", slow_build)

    start = time.monotonic()
    first = await ambient_briefing.get_briefing_card("u1")
    elapsed = time.monotonic() - start
    assert first is None                 # nothing cached yet — but no waiting
    assert elapsed < 0.1                 # the request path never blocks on the build
    await _drain_refresh_tasks()
    assert await ambient_briefing.get_briefing_card("u1") == card_a


@pytest.mark.asyncio
async def test_stale_cache_serves_stale_immediately_and_refreshes_in_background(monkeypatch):
    card_old = {"component": "compose", "props": {"tree": {"component": "Stack", "children": [{"component": "Text", "text": "old"}]}}}
    card_new = {"component": "compose", "props": {"tree": {"component": "Stack", "children": [{"component": "Text", "text": "new"}]}}}
    monkeypatch.setenv("ZOE_BRIEFING_TTL_S", "0")  # everything is instantly stale
    ambient_briefing._cache["u1"] = (card_old, time.monotonic())

    async def slow_build(user_id):
        await asyncio.sleep(0.1)
        return card_new

    monkeypatch.setattr(ambient_briefing, "build_briefing_card", slow_build)

    start = time.monotonic()
    served = await ambient_briefing.get_briefing_card("u1")
    assert time.monotonic() - start < 0.05
    assert served == card_old            # stale card served instantly
    await _drain_refresh_tasks()
    assert ambient_briefing._cache["u1"][0] == card_new  # background refresh landed


@pytest.mark.asyncio
async def test_fresh_cache_serves_without_spawning_refresh(monkeypatch):
    card = {"component": "compose", "props": {"tree": {"component": "Stack", "children": []}}}
    monkeypatch.setenv("ZOE_BRIEFING_TTL_S", "900")
    ambient_briefing._cache["u1"] = (card, time.monotonic())

    async def must_not_run(user_id):  # pragma: no cover - failure path
        raise AssertionError("fresh cache must not trigger a rebuild")

    monkeypatch.setattr(ambient_briefing, "build_briefing_card", must_not_run)
    assert await ambient_briefing.get_briefing_card("u1") == card
    await _drain_refresh_tasks()


@pytest.mark.asyncio
async def test_concurrent_calls_spawn_a_single_refresh(monkeypatch):
    calls = {"n": 0}

    async def counted_build(user_id):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return None

    monkeypatch.setattr(ambient_briefing, "build_briefing_card", counted_build)
    await asyncio.gather(*[ambient_briefing.get_briefing_card("u1") for _ in range(5)])
    await _drain_refresh_tasks()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_build_uses_compose_when_enabled_and_static_fallback_otherwise(monkeypatch):
    facts = ["2 events today.", "19 degrees and clear."]

    async def fake_gather(user_id):
        return list(facts)

    monkeypatch.setattr(ambient_briefing, "gather_facts", fake_gather)

    # Compose path: enabled + brain answers -> the composed card wins.
    composed = {"component": "compose", "props": {"tree": {"component": "Row", "children": []}}}
    seen = {}

    async def fake_compose(user_message, answer_text, *, user_id=""):
        seen["user_message"] = user_message
        seen["answer_text"] = answer_text
        return composed

    monkeypatch.setattr(ambient_briefing.ui_compose, "compose_enabled", lambda: True)
    monkeypatch.setattr(ambient_briefing.ui_compose, "compose_card", fake_compose)
    assert await ambient_briefing.build_briefing_card("u1") == composed
    assert seen["user_message"] == "compose an ambient briefing card"
    assert all(fact in seen["answer_text"] for fact in facts)

    # Compose unavailable (returns None) -> static fallback tree.
    async def no_compose(user_message, answer_text, *, user_id=""):
        return None

    monkeypatch.setattr(ambient_briefing.ui_compose, "compose_card", no_compose)
    fallback = await ambient_briefing.build_briefing_card("u1")
    assert fallback is not None and fallback["component"] == "compose"
    assert fallback["props"]["tree"]["component"] == "Stack"

    # Flag off -> compose never consulted, static fallback still works.
    async def must_not_compose(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("compose_card must not run with the flag off")

    monkeypatch.setattr(ambient_briefing.ui_compose, "compose_enabled", lambda: False)
    monkeypatch.setattr(ambient_briefing.ui_compose, "compose_card", must_not_compose)
    flag_off = await ambient_briefing.build_briefing_card("u1")
    assert flag_off is not None and flag_off["props"]["tree"]["component"] == "Stack"


# ── Endpoint contract ────────────────────────────────────────────────────────

def _briefing_app(user_id: str = "u-test") -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}
    return app


def test_briefing_endpoint_returns_card_envelope(monkeypatch):
    card = {"component": "compose", "props": {"tree": {"component": "Stack", "children": []}}}
    seen = {}

    async def fake_get(user_id):
        seen["user_id"] = user_id
        return card

    monkeypatch.setattr(ambient_briefing, "get_briefing_card", fake_get)
    resp = TestClient(_briefing_app("jason")).get("/api/skybridge/briefing")
    assert resp.status_code == 200
    assert resp.json() == {"card": card}
    assert seen["user_id"] == "jason"


def test_briefing_endpoint_returns_null_card_when_nothing_cached(monkeypatch):
    async def fake_get(user_id):
        return None

    monkeypatch.setattr(ambient_briefing, "get_briefing_card", fake_get)
    resp = TestClient(_briefing_app()).get("/api/skybridge/briefing")
    assert resp.status_code == 200
    assert resp.json() == {"card": None}


def test_briefing_endpoint_returns_fast_even_when_compose_is_slow(monkeypatch):
    """The endpoint serves the cache and fires the slow build in the background."""
    async def fake_gather(user_id):
        return ["2 events today."]

    async def slow_compose(user_message, answer_text, *, user_id=""):
        await asyncio.sleep(5)  # a real compose takes 5-10s — must never be awaited inline
        return {"component": "compose", "props": {"tree": {"component": "Stack", "children": []}}}

    monkeypatch.setattr(ambient_briefing, "gather_facts", fake_gather)
    monkeypatch.setattr(ambient_briefing.ui_compose, "compose_enabled", lambda: True)
    monkeypatch.setattr(ambient_briefing.ui_compose, "compose_card", slow_compose)

    client = TestClient(_briefing_app())
    start = time.monotonic()
    resp = client.get("/api/skybridge/briefing")
    elapsed = time.monotonic() - start
    assert resp.status_code == 200
    assert resp.json() == {"card": None}   # first hit: nothing cached yet
    assert elapsed < 1.0                   # never waited on the 5s compose
