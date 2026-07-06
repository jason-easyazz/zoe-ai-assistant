"""ui_compose — brain composition path (mocked LLM + flag gating + live probe)."""
import json
import os

import pytest

import ui_compose
from ui_compose import compose_card, compose_enabled


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        return _FakeResponse(self._payload, self._status)


def _llm_payload(tree):
    return {"choices": [{"message": {"content": json.dumps(tree)}}]}


VALID_TREE = {"component": "Stack", "children": [
    {"component": "Text", "text": "19° and clear", "role": "title"},
    {"component": "Badge", "text": "Geraldton", "tone": "accent"},
]}


@pytest.mark.asyncio
async def test_compose_returns_validated_card(monkeypatch):
    monkeypatch.setattr(ui_compose.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_llm_payload(VALID_TREE)))
    card = await compose_card("what's the weather", "19 and clear in Geraldton")
    assert card and card["component"] == "compose"
    assert card["props"]["tree"]["children"][0]["text"] == "19° and clear"


@pytest.mark.asyncio
async def test_invalid_tree_from_model_returns_none(monkeypatch):
    bad = {"component": "ScriptTag", "children": [{"component": "Text", "text": "x"}]}
    monkeypatch.setattr(ui_compose.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_llm_payload(bad)))
    assert await compose_card("q", "a") is None


@pytest.mark.asyncio
async def test_http_error_returns_none(monkeypatch):
    monkeypatch.setattr(ui_compose.httpx, "AsyncClient",
                        lambda **kw: _FakeClient({}, status=500))
    assert await compose_card("q", "a") is None


@pytest.mark.asyncio
async def test_garbage_content_returns_none(monkeypatch):
    monkeypatch.setattr(ui_compose.httpx, "AsyncClient",
                        lambda **kw: _FakeClient({"choices": [{"message": {"content": "not json {"}}]}))
    assert await compose_card("q", "a") is None


def test_flag_gating(monkeypatch):
    monkeypatch.delenv("ZOE_COMPOSE_UI", raising=False)
    assert compose_enabled() is False
    monkeypatch.setenv("ZOE_COMPOSE_UI", "1")
    assert compose_enabled() is True
    monkeypatch.setenv("ZOE_COMPOSE_UI", "off")
    assert compose_enabled() is False


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("ZOE_LIVE_TESTS") != "1",
                    reason="live llama-server probe (set ZOE_LIVE_TESTS=1)")
async def test_live_compose_against_real_brain():
    card = await compose_card("what's the weather like",
                              "It's 19 degrees and clear in Geraldton right now.")
    assert card is not None and card["component"] == "compose"
