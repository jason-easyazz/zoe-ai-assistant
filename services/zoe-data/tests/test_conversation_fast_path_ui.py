"""Panel text for "let's talk" turns on /api/voice/turn_stream.

The opener/ender fast path returns before voice_command, which is where every
other turn broadcasts voice:responding + voice:done. So the panel used to get
only the transcript: the warm ack never appeared, and the estate never learned
that a conversation had opened or closed. These pin that:

  * the opener and the ender broadcast transcript -> responding(ack) -> done,
    with the conversation flag on both panel events (the estate holds the text
    for the life of the conversation from it);
  * a turn INSIDE a conversation is answered exactly like a regular turn: the
    same transcript broadcast and the same voice_command hand-off, which is what
    broadcasts its reply.

Broadcaster, STT, TTS and the brain are all mocked.
"""
import base64
import json

import pytest

pytestmark = pytest.mark.ci_safe  # TestClient + mocks; no models/DB/network

from fastapi import FastAPI
from fastapi.testclient import TestClient

import time

import conversation_opener
import push
import routers.voice_tts as vt
import voice_speculation as vs

PANEL = "test-panel"


def _app(monkeypatch, transcript, brain_calls):
    monkeypatch.setenv("ZOE_CONVERSATION_OPENER_ENABLED", "1")
    monkeypatch.delenv("ZOE_SPECULATIVE_TURN", raising=False)
    monkeypatch.delenv("ZOE_VOICE_FILLER_ENABLED", raising=False)
    # Never start the real LiveKit container from a unit test.
    monkeypatch.setattr(conversation_opener, "_warm_livekit", lambda: None)

    async def _fake_stt(_path):
        return transcript
    monkeypatch.setattr(vt, "_transcribe_audio", _fake_stt)

    async def _fake_tts(_text):
        return b"RIFFfakewav"
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", _fake_tts)

    async def _brain(payload, caller=None, stream=True, db=None):
        brain_calls.append(payload)
        return {"reply": "Brain answer.", "audio_base64": base64.b64encode(b"RIFFreal").decode()}
    monkeypatch.setattr(vt, "voice_command", _brain)

    app = FastAPI()
    app.include_router(vt.router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {
        "source": "device", "panel_id": PANEL, "user_id": "voice-daemon",
    }
    from database import get_db as _real_get_db
    app.dependency_overrides[_real_get_db] = lambda: None
    return app


@pytest.fixture
def events(monkeypatch):
    sent = []

    async def _broadcast(channel, event_type, data, *a, **kw):
        sent.append((event_type, dict(data)))
        return 1
    monkeypatch.setattr(push.broadcaster, "broadcast", _broadcast)
    return sent


def _turn(app, *, conversation=False):
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": PANEL}
    if conversation:
        payload["conversation"] = True
    with TestClient(app) as client:
        r = client.post("/api/voice/turn_stream", json=payload)
        assert r.status_code == 200
        frames = []
        for line in r.iter_lines():
            if not line:
                continue
            try:
                frames.append(json.loads(line))
            except Exception:
                frames.append({"_raw_audio": True})
    return frames


def _voice(events):
    return [(t, d) for t, d in events if t.startswith("voice:")]


def test_opener_broadcasts_ack_and_conversation_mode(monkeypatch, events):
    calls = []
    frames = _turn(_app(monkeypatch, "Let's talk", calls))

    done_frame = next(f for f in frames if f.get("done"))
    assert done_frame.get("conversation_mode") is True, frames
    ack = done_frame["reply"]
    assert ack
    assert calls == [], "the opener is a fast path — it must never reach the brain"

    ui = _voice(events)
    assert [t for t, _ in ui] == ["voice:transcript", "voice:responding", "voice:done"], ui
    assert ui[0][1]["text"] == "Let's talk"
    assert ui[1][1] == {"panel_id": PANEL, "text": ack, "conversation_mode": True}
    assert ui[2][1] == {"panel_id": PANEL, "conversation_mode": True}


def test_ender_broadcasts_ack_and_conversation_end(monkeypatch, events):
    calls = []
    frames = _turn(_app(monkeypatch, "That's all, thanks.", calls), conversation=True)

    done_frame = next(f for f in frames if f.get("done"))
    assert done_frame.get("conversation_end") is True, frames
    assert calls == []

    ui = _voice(events)
    assert [t for t, _ in ui] == ["voice:transcript", "voice:responding", "voice:done"], ui
    assert ui[1][1] == {"panel_id": PANEL, "text": done_frame["reply"], "conversation_end": True}
    assert ui[2][1] == {"panel_id": PANEL, "conversation_end": True}


def test_ender_outside_a_conversation_is_an_ordinary_turn(monkeypatch, events):
    """"that's all" with no open conversation is a normal utterance — no flags."""
    calls = []
    _turn(_app(monkeypatch, "That's all", calls), conversation=False)
    assert len(calls) == 1
    assert not any("conversation_end" in d for _, d in events), events


def test_turn_inside_a_conversation_is_answered_like_a_regular_turn(monkeypatch, events):
    """Mid-conversation turns carry conversation=True; they must reach the same
    broadcasting pipeline (voice_command) with the same transcript broadcast."""
    utterance = "What should we talk about?"

    reg_calls = []
    _turn(_app(monkeypatch, utterance, reg_calls), conversation=False)
    regular = list(_voice(events))
    events.clear()

    conv_calls = []
    _turn(_app(monkeypatch, utterance, conv_calls), conversation=True)
    in_conv = list(_voice(events))

    assert [c["text"] for c in conv_calls] == [utterance]
    assert [c["text"] for c in reg_calls] == [utterance]
    assert in_conv == regular == [("voice:transcript", {"panel_id": PANEL, "text": utterance})]


def test_flag_off_opener_is_not_a_fast_path(monkeypatch, events):
    """Negative control: with the opener flag OFF nothing is fast-pathed, so no
    ack/flags are broadcast from here (voice_command owns the turn)."""
    calls = []
    app = _app(monkeypatch, "Let's talk", calls)
    monkeypatch.setenv("ZOE_CONVERSATION_OPENER_ENABLED", "0")
    frames = _turn(app)
    assert len(calls) == 1
    assert not any(f.get("conversation_mode") for f in frames)
    assert [t for t, _ in _voice(events)] == ["voice:transcript"]


@pytest.mark.parametrize("verdict,shown", [("commit", True), ("cancel", False)])
def test_speculative_opener_push_waits_for_the_verdict(monkeypatch, events, verdict, shown):
    """B1.1: a speculative turn's side effects wait for the verdict. The ack push
    runs on commit and is dropped on cancel (the user was still talking)."""
    calls = []
    app = _app(monkeypatch, "Let's talk", calls)
    monkeypatch.setenv(vs.FLAG, "1")
    monkeypatch.setenv(vs.MAX_HOLD_FLAG, "300")
    turn_id = "conv-" + verdict

    async def _stt_then_verdict(_path, capture=True):
        vs.get_gate(turn_id).resolve(verdict)
        return "Let's talk"
    monkeypatch.setattr(vt, "_transcribe_audio", _stt_then_verdict)

    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": PANEL,
               "speculative": True, "turn_id": turn_id}
    with TestClient(app) as client:
        assert client.post("/api/voice/turn_stream", json=payload).status_code == 200
        time.sleep(0.3)  # let the deferred push run on the app loop
    kinds = [t for t, _ in _voice(events)]
    assert ("voice:responding" in kinds) is shown, kinds
    assert ("voice:done" in kinds) is shown, kinds


@pytest.mark.asyncio
async def test_broadcast_helper_survives_a_push_failure(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("push down")
    monkeypatch.setattr(push.broadcaster, "broadcast", _boom)
    # Must not raise — the UI push can never break the turn.
    await conversation_opener.broadcast_conversation_turn(PANEL, "Okay.", {"conversation_end": True})
