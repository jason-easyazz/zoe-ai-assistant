"""Voice WS instant fast-path: listed read-only intents answer + speak without
waking the brain; unlisted utterances fall through to the brain; and a fast-path
TTS failure (after the transcript is sent) must NOT fall through (no duplicate
transcript / double brain run).

Drives main.websocket_voice with a fake WebSocket so the full app lifespan never
runs (matches the existing voice tests' approach).
"""
from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

import main
import brain_dispatch
import routers.voice_tts as voice_tts

pytestmark = pytest.mark.ci_safe


class _FakeWS:
    def __init__(self, inbound):
        self._inbound = list(inbound)
        self.sent: list[dict] = []
        # No Origin header = non-browser client; the CSWSH guard
        # (main._ws_origin_allowed) allows it by policy.
        self.headers: dict[str, str] = {}

    async def accept(self):
        return None

    async def send_json(self, obj):
        self.sent.append(obj)

    async def receive(self):
        if self._inbound:
            return self._inbound.pop(0)
        raise WebSocketDisconnect()

    def types(self):
        return [m.get("type") for m in self.sent]

    def transcripts(self):
        return [m for m in self.sent if m.get("type") == "transcript"]


@pytest.fixture
def voice_env(monkeypatch):
    async def _user(_sid):
        return "family-admin"

    async def _cards(_msg, _uid, context=None):
        return {"handled": False, "cards": [], "spoken_summary": ""}

    async def _tts_ok(_text):
        return b"WAVDATA"

    monkeypatch.setattr(main, "_resolve_ws_user", _user)
    monkeypatch.setattr(main, "_resolve_voice_cards", _cards)
    monkeypatch.setattr(voice_tts, "_synthesize_kokoro_sidecar", _tts_ok)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    return monkeypatch


@pytest.mark.asyncio
async def test_listed_intent_bypasses_brain(voice_env):
    called = {"brain": False}

    async def _brain(*_a, **_k):
        called["brain"] = True
        if False:
            yield ""  # pragma: no cover — make it an async generator

    voice_env.setattr(brain_dispatch, "brain_streaming", _brain)

    ws = _FakeWS([{"text": json.dumps({"type": "text", "message": "what time is it"})}])
    await main.websocket_voice(ws, session_id="t1")

    assert called["brain"] is False, "instant intent must NOT wake the brain"
    tr = ws.transcripts()
    assert len(tr) == 1, f"exactly one transcript expected, got {ws.types()}"
    assert "done" in ws.types()


@pytest.mark.asyncio
async def test_unlisted_utterance_falls_through_to_brain(voice_env):
    called = {"brain": False}

    async def _brain(*_a, **_k):
        called["brain"] = True
        yield "Once upon a time."

    voice_env.setattr(brain_dispatch, "brain_streaming", _brain)
    # brain path synthesizes per sentence; keep it simple/fast
    voice_env.setattr(voice_tts, "_extract_complete_sentences", lambda buf: ([buf], "") if buf.strip() else ([], buf))

    ws = _FakeWS([{"text": json.dumps({"type": "text", "message": "tell me a story about dragons"})}])
    await main.websocket_voice(ws, session_id="t2")

    assert called["brain"] is True, "unlisted utterance must reach the brain"


@pytest.mark.asyncio
async def test_fastpath_tts_failure_does_not_fall_through(voice_env):
    """If TTS raises AFTER the transcript is sent, we must still finish on the
    fast-path (one transcript + done) and never run the brain (no duplicate)."""
    called = {"brain": False}

    async def _brain(*_a, **_k):
        called["brain"] = True
        if False:
            yield ""

    async def _tts_boom(_text):
        raise RuntimeError("kokoro down")

    voice_env.setattr(brain_dispatch, "brain_streaming", _brain)
    voice_env.setattr(voice_tts, "_synthesize_kokoro_sidecar", _tts_boom)

    ws = _FakeWS([{"text": json.dumps({"type": "text", "message": "what time is it"})}])
    await main.websocket_voice(ws, session_id="t3")

    assert called["brain"] is False, "must not fall through to brain after committing"
    assert len(ws.transcripts()) == 1, f"exactly one transcript (no duplicate), got {ws.types()}"
    assert "done" in ws.types()
