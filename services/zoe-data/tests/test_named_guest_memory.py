"""A named guest's own memory and erasing a guest (``named_guest_memory.py``). Invented names only (the repo is public)."""
from __future__ import annotations


import pytest

import named_guest_enrol as nge
import named_guest_memory as ngm
import named_guests as ng
from named_guest_support import (  # noqa: F401 - env is a fixture
    DEVICE, MID, OTHER, OWNER, PANEL, add_guest, claim, env, run, unit,
)

pytestmark = pytest.mark.ci_safe

enrol = add_guest


def test_a_guest_turn_is_remembered_and_recalled_for_that_guest_only(env):
    q, r = enrol(env), enrol(env, name="Rowan", vec=unit(8))
    quinn = ng.Guest(q, "Quinn", OWNER)
    rowan = ng.Guest(r, "Rowan", OWNER)
    assert run(ngm.remember(quinn, "s1", "I'm training for a marathon", "Good luck with it!"))
    assert run(ngm.remember(rowan, "s2", "my cat is called Biscuit", "What a name."))
    lines = run(ngm.recall_lines(quinn))
    assert len(lines) == 1 and "marathon" in lines[0] and "Biscuit" not in " ".join(lines)
    ng.bind_turn(quinn)
    block = run(ng.brain_block("guest"))
    assert "talking with Quinn, a guest" in block and "marathon" in block and "Biscuit" not in block
    assert run(ng.brain_block(OWNER)) == ""                         # never on a member's turn, the owner's included
    ng.bind_turn(None)
    assert run(ng.brain_block("guest")) == ""                       # an unknown voice gets nothing

def test_memory_flag_off_keeps_and_serves_nothing(env, monkeypatch):
    q = enrol(env)
    monkeypatch.setenv("ZOE_NAMED_GUEST_MEMORY", "0")
    quinn = ng.Guest(q, "Quinn", OWNER)
    assert not run(ngm.remember(quinn, "s1", "hello", "hi"))
    assert env.q("SELECT COUNT(*) FROM named_guest_turns") == [(0,)]
    ng.bind_turn(quinn)
    block = run(ng.brain_block("guest"))
    assert "Quinn" in block and "Earlier conversations" not in block

def test_forget_quinn_erases_the_voice_and_every_turn(env):
    """The entity forget's real step ("forget everything about Quinn" -> contacts_store.forget_voiceprints)."""
    import contacts_store
    import re

    q = enrol(env)
    run(ngm.remember(ng.Guest(q, "Quinn", OWNER), "s1", "hello", "hi"))
    n = run(contacts_store.forget_voiceprints(env, OWNER, re.compile(r"\bquinn\b", re.I)))
    assert n >= 2
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)] and env.q("SELECT COUNT(*) FROM named_guest_turns") == [(0,)]

def test_forget_quinns_voice_by_voice_and_its_control(env, monkeypatch):
    q = enrol(env)
    run(ngm.remember(ng.Guest(q, "Quinn", OWNER), "s1", "hello", "hi"))
    assert "forgotten Quinn's voice" in run(nge.tier("forget quinn's voice", OWNER, "s", channel="voice", speaker_verified=True,
                                                    panel_id=PANEL)).reply
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)] and env.q("SELECT COUNT(*) FROM named_guest_turns") == [(0,)]
    assert env.q("SELECT COUNT(*) FROM people") == [(1,)]           # the member's contact stays
    assert run(nge.tier("forget quinn", OWNER, "s", channel="voice", speaker_verified=True, panel_id=PANEL)) is None
    # another member cannot erase this member's guest by name
    q = enrol(env)
    assert "don't know a voice" in run(nge.tier("forget quinn's voice", OTHER, "s", channel="voice", speaker_verified=True,
                                               panel_id=PANEL)).reply
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(1,)]

def test_a_failed_erase_says_so(env, monkeypatch):
    enrol(env)

    async def boom(owner, pid):
        raise RuntimeError("db down")
    monkeypatch.setattr(ngm, "erase", boom)
    assert "couldn't forget" in run(nge.tier("forget quinn's voice", OWNER, "s", channel="voice", speaker_verified=True,
                                            panel_id=PANEL)).reply

def test_list_is_own_unless_admin(env):
    enrol(env)
    enrol(env, name="Rowan", owner=OTHER, vec=unit(8))
    assert [g["name"] for g in run(ngm.list_for(OWNER))] == ["Quinn"]
    assert [g["name"] for g in run(ngm.list_for(OWNER, admin=True))] == ["Quinn", "Rowan"]
    assert all("embedding" not in g for g in run(ngm.list_for(OWNER, admin=True)))
