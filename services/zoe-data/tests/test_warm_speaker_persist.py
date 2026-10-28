"""The wake KV warm's likely-speaker memo survives a zoe-data restart (2026-10-28: after a deploy the memo was empty, the warm
rendered the GUEST envelope, and the member's first turn re-prefilled 1087 tokens: brain TTFT 3.8 s instead of ~1.2 s).

It is a cache-shaping hint only: ids + a time in a state file, 12 h age cap, never a guest, removed by forget.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import routers.voice_tts as v
import warm_speaker as ws
import zoe_flue_client as zfc
from test_kv_warm_prod_path import prod_turn  # noqa: F401  (fixture: the production turn path with a stubbed sidecar)

pytestmark = pytest.mark.ci_safe

T0 = 1_790_000_000.0


@pytest.fixture
def state(monkeypatch, tmp_path):
    p = tmp_path / "warm-speaker.json"
    monkeypatch.setenv("ZOE_WARM_SPEAKER_STATE", str(p))
    monkeypatch.delenv("ZOE_WARM_SPEAKER_MAX_AGE_S", raising=False)
    ws.reset()
    yield p
    ws.reset()


def _restart():
    """A fresh process: the in-process memos are empty, only the state file remains."""
    ws.reset()
    zfc._LAST_TURN.clear()
    zfc._SESSION_UID.clear()


def _prewarm_member(monkeypatch, panel="p1"):
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
    monkeypatch.setattr(zfc, "warm_kv_prefix", _kv)
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    asyncio.run(v._prewarm_brain_for_panel(panel))
    return seen[-1]


def test_memo_round_trips_through_the_file_with_ids_only(state):
    ws.note("p1", "casey", now=T0, sync=True)
    raw = json.loads(state.read_text())
    assert raw == {"p1": ["casey", T0]}, "ids and a time, nothing else"
    assert (state.stat().st_mode & 0o777) == 0o600
    _restart()
    assert ws.get("p1", now=T0 + 60) == "casey"
    assert ws.get("p2", now=T0 + 60) == "", "another panel's speaker is not borrowed"


def test_age_cap_degrades_a_stale_guess_to_the_guest_warm(state):
    ws.note("p1", "casey", now=T0, sync=True)
    _restart()
    assert ws.get("p1", now=T0 + 11.9 * 3600) == "casey"
    assert ws.get("p1", now=T0 + 12.1 * 3600) == "", "older than 12 h: no entry"
    assert ws.get("p1", now=T0 - 3600) == "", "stamped in the future (clock jump): no entry"


def test_a_guest_or_sentinel_is_never_recorded(state):
    for who in ("guest", "voice-guest", "", "  "):
        ws.note("p1", who, now=T0, sync=True)
    assert not state.exists()
    v._note_voice_speaker("p1", "voice-guest")
    v._note_voice_speaker("p1", None)
    assert ws.get("p1") == ""


def test_an_unrecognised_voice_does_not_replace_the_remembered_member(state):
    """'Voice is a password': a guest turn records nothing, and the warm's envelope is not a guest turn's envelope either."""
    ws.note("p1", "casey", now=T0, sync=True)
    v._note_voice_speaker("p1", "voice-guest")
    _restart()
    assert ws.get("p1", now=T0 + 5) == "casey"


def test_forget_removes_the_member_from_memory_and_file(state):
    ws.note("p1", "casey", now=T0, sync=True)
    ws.note("p2", "dana", now=T0, sync=True)
    assert ws.forget_user("casey") == 1
    assert ws.get("p1", now=T0 + 1) == "" and ws.get("p2", now=T0 + 1) == "dana"
    assert "casey" not in state.read_text()
    _restart()
    assert ws.get("p1", now=T0 + 1) == "", "gone after a restart too"
    assert ws.forget_user("nobody") == 0


def test_revoke_member_reaches_the_memo(state):
    import voice_session

    ws.note("p1", "casey", now=T0, sync=True)
    asyncio.run(voice_session.revoke_member("casey", "voice_profile_deleted"))
    assert "casey" not in state.read_text()
    ws.note("p1", "casey", now=T0, sync=True)
    asyncio.run(voice_session.revoke_member("dana", "voice_profile_deleted"))     # control: another member's revoke keeps it
    assert "casey" in state.read_text()


def test_corrupt_or_foreign_state_is_no_memo(state):
    state.write_text("{not json")
    ws.reset()
    assert ws.get("p1", now=T0) == ""
    state.write_text(json.dumps({"p1": ["voice-guest", T0], "p2": "casey", "p3": ["dana", T0]}))
    ws.reset()
    assert ws.get("p1", now=T0) == "" and ws.get("p2", now=T0) == "" and ws.get("p3", now=T0) == "dana"


def test_unchanged_entry_is_not_rewritten_every_turn(state, monkeypatch):
    saves = []
    monkeypatch.setattr(ws, "_save", lambda sync: saves.append(sync))
    ws.note("p1", "casey", now=T0)
    ws._last_written["p1"] = ("casey", T0)    # what the save would have recorded
    ws.note("p1", "casey", now=T0 + 30)       # same member, seconds later: no write
    assert len(saves) == 1
    ws.note("p1", "dana", now=T0 + 31)        # a different member: write
    ws._last_written["p1"] = ("dana", T0 + 31)
    ws.note("p1", "dana", now=T0 + 31 + 601)  # refreshed after the rewrite interval
    assert len(saves) == 3


# -- the end-to-end restart: the real turn's key == the warm's key, from the persisted memo alone -----------------------

def test_after_a_restart_the_wake_warm_renders_the_members_envelope(monkeypatch, state, prod_turn):  # noqa: F811
    wire = prod_turn("How are you going tonight?")                 # the member's real turn (also records the in-process memos)
    ws.note("panel-1", "zed_uid", sync=True)                       # ... verified by voice on this panel (the save made synchronous)
    _restart()                                                     # deploy: every in-process memo is gone, the file remains
    assert not zfc._LAST_TURN

    assert _prewarm_member(monkeypatch, "panel-1") == "zed_uid", "the warm is asked for the member, not the guest"
    msg, key = asyncio.run(zfc._kv_warm_outbound("zed_uid"))       # no _LAST_TURN memo either: rebuilt from the member alone
    assert key == zfc.prefix_key(wire, "cardv1"), "the warm's key matches the member's real turn"
    assert "zoe-manner:" in msg


def test_negative_control_without_the_persisted_memo_the_warm_is_the_guest_one(monkeypatch, state, prod_turn):  # noqa: F811
    """What 2026-10-28 did: nothing survived the restart -> guest warm -> a different key from the member's turn."""
    wire = prod_turn("How are you going tonight?")
    _restart()                                                     # the state file was never written
    assert not state.exists()
    assert _prewarm_member(monkeypatch, "panel-1") == ""
    _msg, guest_key = asyncio.run(zfc._kv_warm_outbound(""))
    assert guest_key != zfc.prefix_key(wire, "cardv1")


def test_negative_control_an_expired_memo_is_the_guest_warm(monkeypatch, state, prod_turn):  # noqa: F811
    prod_turn("How are you going tonight?")
    ws.note("panel-1", "zed_uid", now=T0, sync=True)               # a past instant: far older than 12 h by the real clock
    _restart()
    assert _prewarm_member(monkeypatch, "panel-1") == ""
