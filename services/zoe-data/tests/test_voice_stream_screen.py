"""What the panel SCREEN is told at the end of a streamed voice turn (2026-10-12).

D4 — truncation: the stream path sent its final ``show_card`` with ``final_reply[:300]`` (and each
per-sentence ``voice:responding`` with ``s[:200]``); the estate treats a card's text as REPLACING
the accumulated answer, so every long reply showed cut at 300 characters. The Ask surface
scrolls — the full text must go out.

D5 — a turn must ALWAYS end on screen: ``voice:done`` (and the card) were only sent on the
happy path, so a barge-in / client disconnect / error mid-stream left the orb on "thinking"
until the next turn.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

import skybridge_service
import voice_presence
import routers.voice_tts as vt

pytestmark = pytest.mark.ci_safe

PANEL = "panel-stream"


class _Broadcaster:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    async def broadcast(self, channel, event, payload):
        self.events.append((channel, event, payload))

    def of(self, event):
        return [p for (_c, e, p) in self.events if e == event]

    def names(self):
        return [e for (_c, e, _p) in self.events]


def _wire(monkeypatch, agent):
    bc = _Broadcaster()

    async def none(*_a, **_k):
        return None

    async def panel_user(*_a, **_k):
        return "panel-user"

    async def history(*_a, **_k):
        return []

    async def tts(*_a, **_k):
        return b"RIFF-wav"

    monkeypatch.setattr(voice_presence, "_AUDIO_CACHE", {})
    monkeypatch.setattr(vt, "_resolve_recent_panel_session_user", panel_user)
    monkeypatch.setattr(vt, "_resolve_panel_default_user", none)
    monkeypatch.setattr(vt, "_load_voice_history", history)
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", tts)
    monkeypatch.setattr(vt, "_VOICE_SESSIONS", {})
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=bc))
    monkeypatch.setitem(sys.modules, "skybridge_service", types.SimpleNamespace(resolve_skybridge_request=none))
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", none)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")   # route to the mocked zoe_agent
    monkeypatch.setenv("ZOE_PROCESSING_ACK_PHRASE", "Let me check.")
    monkeypatch.setitem(sys.modules, "zoe_agent", types.SimpleNamespace(run_zoe_agent_streaming=agent))
    return bc


async def _turn(text="could you think about this for me", panel=PANEL):
    return await vt.voice_command(
        {"text": text, "panel_id": panel, "session_id": "s-" + panel},
        caller={"user_id": "guest", "panel_id": panel}, stream=True, db=object(),
    )


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0)


# 6 x ~100-char sentences + one ~260-char sentence (> the old 200 per-sentence slice) = ~870 chars.
_SENTENCES = [f"This is sentence number {i} of a deliberately long answer that the panel has to show in full today." for i in range(6)]
_SENTENCES.append("And this last sentence is much longer than two hundred characters on purpose, " + "padding the line out so a per-sentence slice would visibly cut it in the middle of a word " * 2 + "end.")
_LONG_REPLY = " ".join(_SENTENCES)


@pytest.mark.asyncio
async def test_long_reply_goes_to_the_screen_in_full(monkeypatch):
    assert len(_LONG_REPLY) > 600 and len(_SENTENCES[-1]) > 200

    async def agent(*_a, **_k):
        for s in _SENTENCES:
            yield s + " "

    bc = _wire(monkeypatch, agent)
    resp = await _turn()
    chunks = [c async for c in resp.body_iterator]
    done_line = json.loads(chunks[-1].decode())

    assert done_line["reply"] == _LONG_REPLY
    cards = [p for p in bc.of("ui_action") if p["action"]["action_type"] == "show_card"]
    assert len(cards) == 1
    card_text = cards[0]["action"]["payload"]["data"]["text"]
    assert card_text == _LONG_REPLY, "the estate replaces the answer with the card text — it must be the WHOLE reply, not [:300]"
    spoken = [p["text"] for p in bc.of("voice:responding") if not p.get("processing_ack")]
    assert " ".join(spoken) == _LONG_REPLY, "per-sentence responding text is not sliced either (was s[:200])"
    names = bc.names()
    assert names.index("voice:done") > names.index("ui_action"), "the card lands before voice:done"
    assert names.count("voice:done") == 1


@pytest.mark.asyncio
async def test_done_is_sent_when_the_client_disconnects_mid_stream(monkeypatch):
    """Barge-in / disconnect: the generator is closed after the first spoken sentence."""
    async def agent(*_a, **_k):
        yield "First sentence, spoken. "
        await asyncio.sleep(30)          # the rest never arrives: the client left
        yield "never reached"

    bc = _wire(monkeypatch, agent)
    resp = await _turn()
    it = resp.body_iterator
    seen_audio = False
    while not seen_audio:
        line = await it.__anext__()
        seen_audio = line.startswith(b"UklGR")   # base64 of "RIFF": the first sentence's audio went out
    assert "voice:done" not in bc.names(), "precondition: the turn is still in flight"
    await it.aclose()                          # the client went away (Starlette closes/cancels the generator)
    await _settle()
    assert bc.names().count("voice:done") == 1, "a barge-in must still end the 'thinking' state on screen"
    cards = [p for p in bc.of("ui_action") if p["action"]["action_type"] == "show_card"]
    assert cards and cards[0]["action"]["payload"]["data"]["text"] == "First sentence, spoken.", "the card carries what was actually heard"


@pytest.mark.asyncio
async def test_done_is_sent_when_the_brain_errors_mid_stream(monkeypatch):
    async def agent(*_a, **_k):
        yield "Started to answer. "
        raise RuntimeError("brain fell over")

    bc = _wire(monkeypatch, agent)
    resp = await _turn()
    chunks = [c async for c in resp.body_iterator]
    await _settle()
    assert any(b"voice command stream failure" in c for c in chunks), "the client still gets its error frame"
    assert bc.names().count("voice:done") == 1, "an error mid-stream must still end the thinking state"


@pytest.mark.asyncio
async def test_done_is_sent_once_on_the_happy_path(monkeypatch):
    async def agent(*_a, **_k):
        yield "Short answer."

    bc = _wire(monkeypatch, agent)
    resp = await _turn()
    [c async for c in resp.body_iterator]
    await _settle()
    assert bc.names().count("voice:done") == 1, "the finally must not double-send after a normal finish"
    assert len([p for p in bc.of("ui_action") if p["action"]["action_type"] == "show_card"]) == 1
