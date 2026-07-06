"""Panel generative UI — the voice WS compose frame (flag-gated, budget-bounded)."""
import asyncio

import pytest

pytestmark = pytest.mark.ci_safe

import main as main_mod
from main import _voice_compose_cards_frame

VALID_TREE = {"component": "Stack", "children": [
    {"component": "Text", "text": "Evening summary", "role": "title"}]}
VALID_CARD = {"component": "compose", "props": {"tree": VALID_TREE}}


@pytest.mark.asyncio
async def test_frame_shape_matches_panel_cards_contract(monkeypatch):
    monkeypatch.setenv("ZOE_COMPOSE_UI", "1")
    async def fake_compose(*a, **k):
        return VALID_CARD
    monkeypatch.setattr("ui_compose.compose_card", fake_compose)
    frame = await _voice_compose_cards_frame("how was my evening", "It was calm.", "family-admin")
    assert frame["type"] == "cards"
    r = frame["result"]
    assert r["handled"] is True and r["cards"] == [VALID_CARD]
    assert r["intent"]["domain"] == "compose" and r["spoken_summary"] == ""


@pytest.mark.asyncio
async def test_flag_off_returns_none(monkeypatch):
    monkeypatch.delenv("ZOE_COMPOSE_UI", raising=False)
    assert await _voice_compose_cards_frame("q", "a", "u") is None


@pytest.mark.asyncio
async def test_empty_reply_returns_none(monkeypatch):
    monkeypatch.setenv("ZOE_COMPOSE_UI", "1")
    assert await _voice_compose_cards_frame("q", "   ", "u") is None


@pytest.mark.asyncio
async def test_budget_bound(monkeypatch):
    monkeypatch.setenv("ZOE_COMPOSE_UI", "1")
    monkeypatch.setattr(main_mod, "_VOICE_COMPOSE_BUDGET_S", 0.05)
    async def slow(*a, **k):
        await asyncio.sleep(1.0)
        return VALID_CARD
    monkeypatch.setattr("ui_compose.compose_card", slow)
    import time
    t0 = time.monotonic()
    assert await _voice_compose_cards_frame("q", "a", "u") is None
    assert time.monotonic() - t0 < 0.6


@pytest.mark.asyncio
async def test_compose_failure_returns_none(monkeypatch):
    monkeypatch.setenv("ZOE_COMPOSE_UI", "1")
    async def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr("ui_compose.compose_card", boom)
    assert await _voice_compose_cards_frame("q", "a", "u") is None


@pytest.mark.asyncio
async def test_compose_none_returns_none(monkeypatch):
    monkeypatch.setenv("ZOE_COMPOSE_UI", "1")
    async def none_compose(*a, **k):
        return None  # compose_card's own graceful-failure contract
    monkeypatch.setattr("ui_compose.compose_card", none_compose)
    assert await _voice_compose_cards_frame("q", "a", "u") is None
