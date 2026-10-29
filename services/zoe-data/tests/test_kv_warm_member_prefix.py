"""The wake KV warm must warm the system prompt the real turn will send (2026-10-27 cold first turn).

Evidence: the warm was an explicit guest (system + tools, ~2670 tokens); the identified member's turn adds a manner
block and a user-model card to the system prompt AHEAD of the tool block, so the two prompts shared only ~2150
tokens and ~520 tokens (tool block + message) followed the divergence in the warm's slot - more than the SWA window
llama-server can resume from - so the turn re-prefilled all 3244 tokens (first_cache_n=0, 5.96 s to first token).
These pin that the warm for a member carries the SAME envelope lines (identity, manner, persona) as that member's
turn, that a guest still warms as a plain guest, and that the prefix key line explains a future miss.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import routers.voice_tts as v
import zoe_flue_client as zfc

pytestmark = pytest.mark.ci_safe

MANNER = "Reflect before advising. One question at a time."


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def blocks(monkeypatch):
    async def _manner(uid, message):
        return MANNER if uid == "jason" else ""   # adults only, like manner_block.block_for

    async def _persona(uid):
        return ""

    async def _card(uid):
        return ""

    monkeypatch.setattr(zfc, "_LAST_TURN", {})   # other tests' real turns must not leak a memo in
    monkeypatch.setattr(zfc, "_SESSION_UID", {})
    monkeypatch.setattr(zfc, "_card_version", _card)
    monkeypatch.setattr(zfc, "_manner_context_block", _manner)
    monkeypatch.setattr(zfc, "_persona_context_block", _persona)


def _turn_envelope(uid, manner, persona="", message="hello"):
    """The envelope the real turn path builds (_run_flue_brain_streaming_turn), by the same wrap helpers."""
    out = zfc._wrap_message_with_identity(message, uid)
    out = zfc._wrap_message_with_manner(out, manner)
    return zfc._wrap_message_with_persona(out, persona)


def _head(msg: str, body: str) -> str:
    return msg[: msg.rindex(body)]


def test_member_warm_shares_the_turns_system_prompt_inputs(blocks):
    msg, key = _run(zfc._kv_warm_outbound("jason"))
    turn = _turn_envelope("jason", MANNER)
    # the replay marker is the only extra line (stripped by the sidecar; it does not touch the prompt)
    assert msg.startswith(" zoe-replay:1\n")
    assert _head(msg, zfc._KV_WARM_MESSAGE).split("\n", 1)[1] == _head(turn, "hello")
    assert key == zfc.prefix_key(_turn_envelope("jason", MANNER))


def test_guest_warm_is_unchanged_and_has_a_distinct_key(blocks):
    msg, key = _run(zfc._kv_warm_outbound(""))
    assert msg == " zoe-uid:guest\n" + zfc._KV_WARM_MESSAGE
    assert "zoe-replay" not in msg and "zoe-manner" not in msg
    assert key == zfc.prefix_key(zfc._wrap_message_with_identity("x", "guest"))
    assert key != zfc.prefix_key(_turn_envelope("jason", MANNER))
    assert _run(zfc._kv_warm_outbound("voice-guest"))[0] == msg


def test_negative_control_a_guest_warm_does_not_match_a_members_turn(blocks):
    """Without the fix every warm was the guest warm: it must NOT satisfy the member invariant above."""
    guest_msg, guest_key = _run(zfc._kv_warm_outbound(""))
    turn = _turn_envelope("jason", MANNER)
    assert _head(guest_msg, zfc._KV_WARM_MESSAGE) != _head(turn, "hello")
    assert guest_key != zfc.prefix_key(turn)


def test_warm_for_a_member_with_no_manner_block_matches_that_turn_too(blocks):
    msg, key = _run(zfc._kv_warm_outbound("kid"))   # a minor / flag-off member: no manner line on either side
    assert "zoe-manner" not in msg
    assert key == zfc.prefix_key(_turn_envelope("kid", ""))


def test_prefix_key_line_names_both_sides(caplog):
    caplog.set_level("INFO", logger="zoe_flue_client")
    k = zfc._log_prefix_key("turn", "voice-panel-x-1", _turn_envelope("jason", MANNER))
    line = [r.getMessage() for r in caplog.records if "FLUE_PREFIX_KEY" in r.getMessage()]
    assert line and f"kind=turn session=voice-panel-x-1 key={k} requested_card=- slot=auto" in line[0]
    assert k != zfc.prefix_key(_turn_envelope("guest", ""))


def test_wake_warms_the_last_verified_speaker_not_a_guest(monkeypatch):
    seen = []

    async def _kv(member=""):
        seen.append(member)
        return "warmed"

    async def _none(panel_id, fallback_db=None):
        return None

    monkeypatch.setattr(v, "_brain_prewarm_on_wake_enabled", lambda: True)
    monkeypatch.setattr(v, "_get_or_create_voice_session", lambda p: "voice-panel-t-1")
    monkeypatch.setattr(v, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(v, "_resolve_fresh_panel_member", _none)
    import warm_speaker
    monkeypatch.setattr(warm_speaker, "_mem", {})
    monkeypatch.setattr(warm_speaker, "_last_written", {})
    monkeypatch.setattr(warm_speaker, "_loaded", False)
    warm_speaker.reset()
    monkeypatch.setattr(zfc, "warm_kv_prefix", _kv)
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)

    _run(v._prewarm_brain_for_panel("p1"))
    assert seen == [""], "nobody spoke yet: guest warm"
    v._note_voice_speaker("p1", "jason")
    v._note_voice_speaker("p1", "voice-guest")   # a sentinel never overwrites
    v._note_voice_speaker("", "jason")
    _run(v._prewarm_brain_for_panel("p1"))
    assert seen[-1] == "jason"
    _run(v._prewarm_brain_for_panel("p2"))
    assert seen[-1] == "", "another panel's speaker is not borrowed"


def test_a_slow_memo_read_never_holds_up_the_wake_warm(monkeypatch):
    """The first wake after a restart reads the memo file; a stalled disk must not delay the warm while the user speaks.
    Past the bound the warm starts with the fallback (here: guest) instead of waiting."""
    import threading
    import time as _time
    seen, gate = [], threading.Event()

    async def _kv(member=""):
        seen.append((member, _time.monotonic()))
        return "warmed"

    async def _none(panel_id, fallback_db=None):
        return None

    import warm_speaker
    monkeypatch.setattr(v, "_brain_prewarm_on_wake_enabled", lambda: True)
    monkeypatch.setattr(v, "_get_or_create_voice_session", lambda p: "voice-panel-t-1")
    monkeypatch.setattr(v, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(v, "_resolve_fresh_panel_member", _none)
    monkeypatch.setattr(warm_speaker, "get", lambda panel, **k: gate.wait(3.0) and "jason")   # the disk is stuck
    monkeypatch.setattr(zfc, "warm_kv_prefix", _kv)
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    t0 = _time.monotonic()
    try:
        _run(v._prewarm_brain_for_panel("p1"))
    finally:
        gate.set()
    assert seen and seen[0][0] == "", "fell back to a guest warm instead of waiting for the stuck memo"
    assert seen[0][1] - t0 < 1.0, f"the warm waited {seen[0][1] - t0:.2f}s on the memo read"


def test_warm_wire_body_carries_the_member_envelope(monkeypatch, blocks):
    import httpx

    import brain_slot_guard

    seen = []

    async def handler(request):
        seen.append(json.loads(request.content)["body"])
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"},
                              content=b'"ready"\n{"done": true, "prompt_cache": [{"prompt_n": 13, "cache_n": 2657}]}\n')

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://sidecar.test")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "t")
    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)
    monkeypatch.delenv("ZOE_BRAIN_KV_WARM_ON_WAKE", raising=False)
    brain_slot_guard.reset()
    monkeypatch.setattr(zfc, "_kv_warm_inflight", False)
    monkeypatch.setattr(zfc, "_kv_warm_session", {"id": "", "uses": 0, "seq": 0})
    assert _run(zfc.warm_kv_prefix("jason")) == "warmed"
    assert " zoe-uid:jason\n" in seen[0] and " zoe-manner:" in seen[0] and seen[0].startswith(" zoe-replay:1\n")
    brain_slot_guard.reset()
