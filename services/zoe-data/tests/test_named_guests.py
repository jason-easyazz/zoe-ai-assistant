"""Named guests - who a guest is (``named_guests.py``, migration 0071): the registry, the panel feed, the verdict, and the rule that a
guest is NEVER a member. Invented names only (``Quinn``, ``Rowan``): the repo is public.

Each guarantee has a break-the-fix control (``*_control``): the same check with the guard removed must FAIL the way the guard
prevents, so a passing guard is not an instrument that cannot see.
"""
from __future__ import annotations


import pytest

import named_guests as ng
from named_guest_support import (  # noqa: F401 - env is a fixture
    DEVICE, MID, OTHER, OWNER, PANEL, add_guest, claim, env, run, unit,
)

pytestmark = pytest.mark.ci_safe
import sqlite3

import omi_voices as ov

enrol = add_guest


def test_one_person_holds_a_pendant_and_a_panel_print_but_never_two_of_one_channel(env):
    gid = enrol(env)
    env.conn.execute("INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, created_at, updated_at, "
                     "expires_at) VALUES ('p1', ?, ?, ?, 'pendant', ?, 'x', 'x', 'x')", (OWNER, gid, MID, unit(9).tobytes()))
    with pytest.raises(sqlite3.IntegrityError):
        env.conn.execute("INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, created_at, "
                         "updated_at, expires_at) VALUES ('p2', ?, ?, ?, 'pendant', ?, 'x', 'x', 'x')", (OWNER, gid, MID, b"x"))

def test_the_pendant_never_reads_a_panel_print(env):
    """Microphone drift: Omi's suggestions and its builder read only the pendant's own prints."""
    gid = enrol(env)
    assert run(ov._voiceprints_for_suggest(env, OWNER, MID)) == []
    pendant = run(ov._voiceprint(env, OWNER, gid, MID, create=True))
    panel = env.q("SELECT id FROM learned_voiceprints WHERE channel = 'panel'")[0][0]
    assert pendant["id"] != panel

def test_the_feed_serves_panel_guests_only_and_nothing_when_off(env, monkeypatch):
    gid = enrol(env)
    env.conn.execute("INSERT INTO people (id, user_id, name, visibility) VALUES ('rowan', ?, 'Rowan', 'personal')", (OWNER,))
    env.conn.execute("INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, created_at, updated_at, "
                     "expires_at) VALUES ('p1', ?, 'rowan', ?, 'pendant', ?, 'x', 'x', 'x')", (OWNER, MID, unit(9).tobytes()))
    feed = run(ng.sync_entries({MID}))
    assert [(g["guest_id"], g["display_name"]) for g in feed] == [(gid, "Quinn")]
    assert run(ng.sync_entries({"resemblyzer"})) == []
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "off")
    assert run(ng.sync_entries({MID})) == []

def test_a_guest_match_names_the_guest_and_only_on_the_lan_in_the_acting_model(env):
    gid = enrol(env)
    g = run(ng.accept(claim(gid, 0.62), DEVICE, member_identified=False))
    assert g is not None and (g.person_id, g.name, g.owner_user_id) == (gid, "Quinn", OWNER)
    for caller in ({**DEVICE, "via_relay": True}, {**DEVICE, "via_tunnel": True}, {"source": "session", "user_id": OWNER}):
        assert run(ng.accept(claim(gid, 0.62), caller, member_identified=False)) is None
    bad_model = claim(gid, 0.62)
    bad_model["speaker"]["model_id"] = "resemblyzer"
    assert run(ng.accept(bad_model, DEVICE, member_identified=False)) is None
    assert run(ng.accept(claim(gid, 0.05), DEVICE, member_identified=False)) is None           # under the bar
    assert run(ng.accept(claim(gid, 0.62, member_score=0.70), DEVICE, member_identified=False)) is None   # the member is closer
    assert run(ng.accept(claim(gid, 0.62), DEVICE, member_identified=True)) is None            # a member already identified
    assert run(ng.accept(claim("nobody", 0.9), DEVICE, member_identified=False)) is None        # not enrolled

def test_off_and_shadow_never_bind_a_guest(env, monkeypatch):
    gid = enrol(env)
    for m in ("off", "shadow"):
        monkeypatch.setenv("ZOE_NAMED_GUESTS", m)
        assert run(ng.accept(claim(gid, 0.62), DEVICE, member_identified=False)) is None

def test_a_child_or_a_deleted_person_is_never_matched(env):
    gid = enrol(env)
    env.conn.execute("UPDATE people SET relationship = 'son' WHERE id = ?", (gid,))
    assert run(ng.accept(claim(gid, 0.62), DEVICE, member_identified=False)) is None
    env.conn.execute("UPDATE people SET relationship = NULL, deleted = 1 WHERE id = ?", (gid,))
    assert run(ng.accept(claim(gid, 0.62), DEVICE, member_identified=False)) is None

def test_a_guest_id_sent_as_a_member_claim_gets_no_member_scope(env, monkeypatch):
    """The negative control the brief asks for: a guest's id in the MEMBER claim clears the threshold but has no consented
    speaker profile, so the member gate drops it - the guest is never a member, whatever the panel says."""
    from routers import voice_tts

    gid = enrol(env)
    forged = {"voice_user_id": gid, "voice_score": 0.9, "speaker": {"member": gid, "score": 0.9, "model_id": MID, "speech_s": 3.0}}
    assert voice_tts._accept_panel_voice_claim(forged, DEVICE) == gid          # the threshold alone would pass it ...
    assert run(voice_tts._accepted_voice_claim(forged, DEVICE)) is None        # ... the consent gate does not

def test_a_guest_who_outscores_the_member_drops_the_member_claim(env, monkeypatch):
    from routers import voice_tts

    gid = enrol(env)
    p = claim(gid, 0.80, member_score=0.55)
    assert voice_tts._accept_panel_voice_claim(p, DEVICE) is None
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "shadow")
    assert voice_tts._accept_panel_voice_claim(p, DEVICE) == "member-a"        # shadow changes nothing
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "on")
    assert voice_tts._accept_panel_voice_claim(claim(gid, 0.50, member_score=0.55), DEVICE) == "member-a"

def test_member_claim_guard_control(env, monkeypatch):
    """Break the fix: without the guard the member claim stands although the guest's voice was closer."""
    from routers import voice_tts

    gid = enrol(env)
    monkeypatch.setattr(ng, "member_claim_beaten", lambda payload: False)
    assert voice_tts._accept_panel_voice_claim(claim(gid, 0.80, member_score=0.55), DEVICE) == "member-a"


def test_the_brain_hears_the_guests_name_on_their_turn_only(env):
    gid = enrol(env)
    g = run(ng.accept(claim(gid, 0.62), DEVICE, member_identified=False))
    ng.bind_turn(g)
    block = run(ng.brain_block("guest"))
    assert "talking with Quinn, a guest" in block and "not the owner" in block
    assert run(ng.brain_block(OWNER)) == ""                         # a member's turn never carries it
    ng.bind_turn(None)
    assert run(ng.brain_block("guest")) == ""                       # an unknown voice gets nothing


def test_a_forgotten_name_is_never_matched_or_fed_and_an_outage_feeds_nobody(env, monkeypatch):
    import memory_forgotten as mf

    gid = enrol(env)

    async def forgotten(uid, text, **_k):
        return uid == OWNER and "Quinn" in text
    monkeypatch.setattr(mf, "matches", forgotten)
    assert run(ng.accept(claim(gid, 0.62), DEVICE, member_identified=False)) is None
    assert run(ng.sync_entries({MID})) == []

    async def outage(*_a, **_k):
        raise mf.LedgerUnavailable("down") if hasattr(mf, "LedgerUnavailable") else RuntimeError("down")
    monkeypatch.setattr(mf, "matches", outage)
    assert run(ng.accept(claim(gid, 0.62), DEVICE, member_identified=False)) is None
    with pytest.raises(Exception):
        run(ng.sync_entries({MID}))                                 # the route turns this into an empty feed


def test_omis_own_api_never_lists_toggles_or_forgets_a_panel_print(env):
    gid = enrol(env)
    vp = env.q("SELECT id FROM learned_voiceprints WHERE channel = 'panel'")[0][0]
    assert run(ov.list_learned(env, OWNER)) == []
    assert run(ov.set_voice_learning(env, OWNER, vp, False)) is False
    assert run(ov.forget_voice(env, OWNER, vp)) is False
    assert env.q("SELECT learning_enabled FROM learned_voiceprints WHERE id = ?", (vp,)) == [(1,)]
    assert gid


def test_a_matched_guest_never_inherits_the_panels_member_session():
    """Static pin on voice_command (review #2339): once a named guest is bound, the scope and the attribution are the guest's,
    whatever fresh PIN session or binding the panel holds - and the override sits AFTER every place they are assigned."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "routers" / "voice_tts.py").read_text()
    body = src[src.index("async def voice_command("):]
    body = body[:body.index("\n@router.")]
    guard = body.index("if _named_guests.current() is not None:\n")
    assert '_scope_identity_user, _has_scope_identity, effective_user = None, False, "guest"' in body[guard:guard + 400]
    for assign in ("_has_scope_identity = bool(_scope_identity_user)", "effective_user = _panel_bound_user"):
        assert body.index(assign) < guard, assign

