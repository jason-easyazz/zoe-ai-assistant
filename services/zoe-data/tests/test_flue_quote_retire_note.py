"""The Flue seam notes each brain turn for quote-backed retirement (ZOE_QUOTE_RETIRE) and changes NOT ONE BYTE of what goes out.

``zoe_flue_client`` is a voice-path file (the spoken turns reach the brain through it): the whole contribution of this feature to the
turn is one dict write - the owner's own message, and whether the turn is a spoken one - so the brain's ``memory_retire`` tool, which
only sends a number, is about the sentence the server put there and never about anything the model says. The outbound request must be
byte-identical whatever the flag (the replay gate's invariant).
"""
from __future__ import annotations

import json

import pytest

import memory_retire as mr
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _wire1(monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")        # the post-only (?wait=result) doubles below
    mr.reset_state()
    yield
    mr.reset_state()


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": "ok"}}


class _Client:
    sent: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).sent.append(content)
        return _Resp()


async def _turn(monkeypatch, message, **kw):
    import httpx
    monkeypatch.setattr(_Client, "sent", [])
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    async def no_packet(uid, msg):
        return ""
    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", no_packet)
    out = [c async for c in zc.run_flue_brain_streaming(message, "s1", "jason", **kw)]
    assert out == ["ok"]
    return _Client.sent[0]


async def test_a_chat_turn_is_noted_as_chat(monkeypatch):
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "shadow")
    await _turn(monkeypatch, "I gave up the cello.")
    assert mr._turns["jason"][:2] == ("I gave up the cello.", False)


async def test_a_spoken_turn_is_noted_as_voice(monkeypatch):
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "shadow")
    await _turn(monkeypatch, "I gave up the cello.", voice_mode=True)
    assert mr._turns["jason"][:2] == ("I gave up the cello.", True)


async def test_off_notes_nothing(monkeypatch):
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "off")
    await _turn(monkeypatch, "I gave up the cello.")
    assert mr._turns == {}


@pytest.mark.parametrize("voice", [False, True])
async def test_the_outbound_request_is_byte_identical_whatever_the_flag(monkeypatch, voice):
    sent = {}
    for mode in ("off", "shadow", "enforce"):
        monkeypatch.setenv("ZOE_QUOTE_RETIRE", mode)
        mr.reset_state()
        sent[mode] = await _turn(monkeypatch, "I gave up the cello.", voice_mode=voice)
    assert sent["off"] == sent["shadow"] == sent["enforce"]
    assert json.loads(sent["off"])["message"].endswith("I gave up the cello.")


async def test_a_failing_note_never_breaks_the_turn(monkeypatch):
    def boom():
        raise RuntimeError("env")
    monkeypatch.setattr(mr, "mode", boom)
    out = await _turn(monkeypatch, "I gave up the cello.")
    assert out
