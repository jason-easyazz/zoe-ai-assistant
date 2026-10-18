"""A11 (architecture deep dive 2026-10-18): a voice turn's UI reaches ONLY the panel it came from.

``voice_tts`` used to broadcast ~48 per-turn UI events (``voice:transcript`` / ``thinking`` / ``responding`` / ``done``,
the answer card, forms, navigation) on the ``"all"`` channel. Every panel socket subscribes to ``"all"`` AND to its own
``panel_<id>``, and so does every signed-in desktop socket (``"all"``). With a second panel, panel B showed panel A's
transcript and answer, and a member's laptop received the household's voice turns.

Now every per-turn push goes to ``push.panel_channel(panel_id)`` (the server-decided panel of the turn), with no
fallback to ``"all"``. These tests drive REAL turns through a REAL ``PushBroadcaster`` with three sockets: panel A,
panel B, a desktop member. A turn on A must reach A and nobody else. The negative control re-runs a turn with the
channel helper forced back to ``"all"`` (the pre-fix behaviour) and shows B and the desktop receiving it, so the
assertion is proven able to see the leak.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

import push
import routers.voice_tts as vt
import skybridge_service
import voice_presence

pytestmark = pytest.mark.ci_safe

PANEL_A = "panel-a"
PANEL_B = "panel-b"


class _WS:
    """A fake accepted WebSocket that records every JSON frame."""

    def __init__(self, name: str):
        self.name = name
        self.frames: list[dict] = []

    async def accept(self):
        return None

    async def send_json(self, msg):
        self.frames.append(msg)

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames if f.get("type") != "connected"]


async def _rig():
    bc = push.PushBroadcaster()
    a, b, desk = _WS("panel-a"), _WS("panel-b"), _WS("desktop")
    await bc.connect_panel(a, PANEL_A)
    await bc.connect_panel(b, PANEL_B)
    await bc.connect(desk, "all", user_id="member-1")
    return bc, a, b, desk


def _wire(monkeypatch, bc, agent):
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
    monkeypatch.setattr(vt, "_load_voice_history", history)
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", tts)
    monkeypatch.setattr(vt, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(push, "broadcaster", bc)
    monkeypatch.setitem(sys.modules, "skybridge_service", types.SimpleNamespace(resolve_skybridge_request=none))
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", none)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")
    monkeypatch.setenv("ZOE_PROCESSING_ACK_PHRASE", "Let me check.")
    monkeypatch.setitem(sys.modules, "zoe_agent", types.SimpleNamespace(run_zoe_agent_streaming=agent))


async def _agent(*_a, **_k):
    yield "Here is the answer for panel A. "
    yield "It has two sentences."


async def _stream_turn_on_a():
    resp = await vt.voice_command(
        {"text": "could you think about this for me", "panel_id": PANEL_A, "session_id": "s-a"},
        caller={"user_id": "guest", "panel_id": PANEL_A, "source": "device"}, stream=True, db=object(),
    )
    chunks = [c async for c in resp.body_iterator]
    for _ in range(10):
        await asyncio.sleep(0)
    return json.loads(chunks[-1].decode())


@pytest.mark.asyncio
async def test_a_turn_on_panel_a_reaches_panel_a_only(monkeypatch):
    bc, a, b, desk = await _rig()
    _wire(monkeypatch, bc, _agent)
    done = await _stream_turn_on_a()
    assert done["reply"].startswith("Here is the answer")

    got = a.types()
    assert "voice:thinking" in got and "voice:responding" in got and got.count("voice:done") == 1, got
    assert "ui_action" in got, "the answer card reaches the panel that asked"
    assert all(f["channel"] == f"panel_{PANEL_A}" for f in a.frames if f.get("type") != "connected"), \
        "every per-turn frame travels on panel A's own channel"
    assert all((f.get("data") or {}).get("panel_id") in (None, PANEL_A) for f in a.frames)
    assert b.types() == [], f"panel B must receive NOTHING from a turn on panel A, got {b.types()}"
    assert desk.types() == [], f"a desktop socket on 'all' must receive no household voice turn, got {desk.types()}"


@pytest.mark.asyncio
async def test_negative_control_the_old_all_channel_leaks_to_panel_b(monkeypatch):
    """The rig sees the leak: with the channel forced back to "all", panel B and the desktop get panel A's turn."""
    bc, a, b, desk = await _rig()
    _wire(monkeypatch, bc, _agent)
    monkeypatch.setattr(vt, "_panel_channel", lambda _pid: "all")
    await _stream_turn_on_a()
    assert "voice:responding" in b.types() and "voice:responding" in desk.types()


@pytest.mark.asyncio
async def test_transcribe_and_wake_target_the_originating_panel(monkeypatch):
    """The two other entry points that push per-turn UI: /transcribe (transcript + thinking) and /wake (listening)."""
    bc, a, b, desk = await _rig()
    monkeypatch.setattr(push, "broadcaster", bc)

    async def stt(*_a, **_k):
        return "turn on the kitchen light"

    monkeypatch.setattr(vt, "_transcribe_audio", stt)
    monkeypatch.setattr(vt, "_log_voice_stt_sample", lambda **_k: None)
    import base64
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    wav = buf.getvalue()
    out = await vt.voice_transcribe(
        {"audio_base64": base64.b64encode(wav).decode(), "panel_id": PANEL_A},
        caller={"source": "device", "panel_id": PANEL_A, "user_id": "voice-daemon"},
    )
    assert out.get("ok") is True, out
    assert a.types() == ["voice:transcript", "voice:thinking"], a.types()
    assert b.types() == [] and desk.types() == []


@pytest.mark.asyncio
async def test_a_device_token_cannot_aim_its_turn_at_another_panel(monkeypatch):
    """The panel is server-decided (#2097): a payload naming panel B from panel A's device token still lands on A."""
    bc, a, b, desk = await _rig()
    _wire(monkeypatch, bc, _agent)
    assert vt._lane_panel_id({"panel_id": PANEL_B}, {"source": "device", "panel_id": PANEL_A}, "command") == PANEL_A
    from conversation_opener import broadcast_conversation_turn

    await broadcast_conversation_turn(vt._lane_panel_id({"panel_id": PANEL_B},
                                                        {"source": "device", "panel_id": PANEL_A}, "command"),
                                      "Okay, let's talk.", {"conversation_mode": True})
    assert a.types() == ["voice:responding", "voice:done"] and b.types() == [] and desk.types() == []


@pytest.mark.parametrize("pid", ["", "unknown", "None", " ", None, "all"])
def test_a_turn_with_no_usable_panel_reaches_nobody(pid):
    assert push.panel_channel(pid) == push.NO_PANEL_CHANNEL


@pytest.mark.asyncio
async def test_no_panel_channel_is_unjoinable():
    bc, a, b, desk = await _rig()
    assert await bc.broadcast(push.panel_channel("unknown"), "voice:responding", {"text": "x"}) == 0
    assert a.types() == b.types() == desk.types() == []
    assert push.panel_channel(PANEL_A) == f"panel_{PANEL_A}"


def test_no_voice_ui_event_is_broadcast_on_all():
    """Static pin: voice_tts never broadcasts on the shared "all" channel again."""
    from pathlib import Path

    src = (Path(vt.__file__)).read_text(encoding="utf-8")
    assert '.broadcast("all"' not in src and "broadcast_to_panel(" not in src
    src = (Path(vt.__file__).parents[1] / "conversation_opener.py").read_text(encoding="utf-8")
    assert '.broadcast("all"' not in src
