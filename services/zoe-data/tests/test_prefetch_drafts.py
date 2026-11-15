"""Prefetched blocks that change state commit only at the POST (ZOE_FLUE_PREPOST_CONCURRENT; review round 2).

``conversation_notes`` advances the session turn count (``_YOUNG``), starts the 6 h cooldown (``_SERVED_AT``) and
persists ``served_count`` (``_bump``) when it builds its block; ``voice_session_bridge`` advances its own ``_YOUNG``.
Prefetched, a turn that later aborts must leave that state byte-identical; a turn that reaches the POST commits exactly
once. Synthetic data only.
"""
from __future__ import annotations

import asyncio
import copy

import pytest

import conversation_notes as cn
import flue_turn_rig as rig
import flue_prompt_corpus as corpus
import voice_session_bridge as vsb
import zoe_flue_client as zfc
from turn_draft import TurnDraft

pytestmark = pytest.mark.ci_safe

ON = "ZOE_FLUE_PREPOST_CONCURRENT"
UID = "synthetic-member"


def _note(i: str) -> "cn.Stored":
    return cn.Stored(id=f"note-{i}", with_name="", started_at="2026-11-13T10:00:00", ended_at="2026-11-13T10:20:00",
                     summary=f"synthetic talk {i}", topics=["garden"], mood="calm", open_threads=["the fence"],
                     follow_up="", sensitive=False, served_count=0, source="synthetic")


@pytest.fixture
def notes_on(monkeypatch):
    bumped: list = []

    async def load_active(*_a, **_k):
        return [_note("a"), _note("b")]

    async def same(_uid, notes):
        return notes

    async def bump(ids):
        bumped.append(list(ids))

    monkeypatch.setattr(cn, "mode", lambda: "on")
    monkeypatch.setattr(cn, "_speaker_ok", lambda _sid: True)
    monkeypatch.setattr(cn, "load_active", load_active)
    monkeypatch.setattr(cn, "_unforgotten", same)
    monkeypatch.setattr(cn, "_unmuted", same)
    monkeypatch.setattr(cn, "_defer_to_facts", same)
    monkeypatch.setattr(cn, "_bump", bump)
    monkeypatch.setattr(cn, "_YOUNG", type(cn._YOUNG)())
    monkeypatch.setattr(cn, "_SERVED_AT", type(cn._SERVED_AT)())
    return bumped


def _notes_state(bumped):
    return copy.deepcopy((dict(cn._YOUNG), dict(cn._SERVED_AT), bumped))


async def test_notes_prepare_changes_nothing_until_commit(notes_on):
    before = _notes_state(notes_on)
    draft = await cn.prepare_block(UID, "voice-panel-n1")
    assert draft.block and "synthetic talk a" in draft.block
    assert _notes_state(notes_on) == before                       # a cancelled turn: byte-identical state
    await draft.commit()
    after = _notes_state(notes_on)
    assert after != before
    assert cn._YOUNG["voice-panel-n1"][0] == 1 and set(cn._SERVED_AT) == {f"{UID}|note-a", f"{UID}|note-b"}
    assert notes_on == [["note-a", "note-b"]]
    await draft.commit()                                          # exactly once
    assert _notes_state(notes_on) == after


async def test_negative_control_block_for_still_commits_at_once(notes_on):
    """The sequential path (block_for) is prepare + commit: the same state change, at once. Proves the check above can
    see a mutation."""
    before = _notes_state(notes_on)
    block = await cn.block_for(UID, "voice-panel-n2")
    assert block and _notes_state(notes_on) != before and notes_on == [["note-a", "note-b"]]


async def test_notes_block_is_the_same_either_way(notes_on):
    draft = await cn.prepare_block(UID, "voice-panel-n3")
    cn._YOUNG.clear()
    cn._SERVED_AT.clear()
    assert await cn.block_for(UID, "voice-panel-n4") == draft.block


async def test_bridge_prepare_changes_nothing_until_commit(monkeypatch):
    monkeypatch.setattr(vsb, "enabled", lambda: True)
    monkeypatch.setattr(vsb, "_real", lambda u: u)
    monkeypatch.setattr(vsb, "_verdict", lambda: True)
    monkeypatch.setattr(vsb, "select", lambda *_a, **_k: [])
    monkeypatch.setattr(vsb, "_PRED", type(vsb._PRED)({"voice-panel-b1": "voice-panel-b0"}))
    monkeypatch.setattr(vsb, "_YOUNG", type(vsb._YOUNG)())
    draft = await vsb.prepare_block(UID, "voice-panel-b1")
    assert dict(vsb._YOUNG) == {}
    await draft.commit()
    await draft.commit()
    assert dict(vsb._YOUNG) == {"voice-panel-b1": 1}


# ── in the turn: commit at the POST, never for a turn that aborts ────────────────────────────────────────────────────
async def test_flag_on_an_aborted_turn_commits_nothing(monkeypatch):
    monkeypatch.setenv(ON, "1")
    cap = rig.install(monkeypatch, {"notes": corpus.mem("n", "notes from earlier conversations with this person"),
                                    "bridge": corpus.mem("b", "where you and this user left off")})

    async def boom(*_a, **_k):
        await asyncio.sleep(0.02)                                 # the bridge / notes prefetches have finished by now
        raise ValueError("synthetic recall failure")

    monkeypatch.setattr(zfc, "_recall_context_block", boom)
    with pytest.raises(ValueError):
        await rig.outbound(cap, "hi")
    await zfc._PREPOST.get().settle_tasks()
    assert "notes" in cap["calls"] and "bridge" in cap["calls"]  # the reads ran (prefetched)...
    assert "notes_commit" not in cap["calls"] and "bridge_commit" not in cap["calls"]   # ...and burned nothing


async def test_flag_on_a_turn_that_reaches_the_post_commits_exactly_once(monkeypatch):
    monkeypatch.setenv(ON, "1")
    notes = corpus.mem("n", "notes from earlier conversations with this person")
    cap = rig.install(monkeypatch, {"notes": notes})
    sent = await rig.outbound(cap, "hi")
    assert notes in sent
    assert cap["calls"].count("notes_commit") == 1 and cap["calls"].count("bridge_commit") == 1


async def test_flag_off_uses_the_sequential_block_for(monkeypatch):
    monkeypatch.delenv(ON, raising=False)
    cap = rig.install(monkeypatch, {})
    await rig.outbound(cap, "hi")
    assert "notes" in cap["calls"] and "notes_commit" not in cap["calls"]   # block_for (prepare+commit inside), no draft


async def test_turn_draft_runs_every_step_once_and_never_raises():
    seen = []

    async def later():
        seen.append("async")

    d = TurnDraft("x")
    d.on_commit(lambda: seen.append("a"))
    d.on_commit(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    d.on_commit(later)
    await d.commit()
    await d.commit()
    assert seen == ["a", "async"] and d.committed
