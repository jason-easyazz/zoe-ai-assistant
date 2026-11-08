"""Enrolling a named guest (``named_guest_enrol.py``): only a verified adult member at the panel, through a single-use ticket.
Invented names only (the repo is public)."""
from __future__ import annotations

import base64

import pytest

import named_guest_enrol as nge
from named_guest_support import (  # noqa: F401 - env is a fixture
    DEVICE, MID, OTHER, OWNER, PANEL, env, run, unit,
)

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _tickets():
    nge.reset()
    yield
    nge.reset()


def enrol(db, name="Quinn", owner=OWNER, vec=None, model=MID) -> str:
    """The real enrolment path: a server ticket, then the panel's upload."""
    t = nge.issue_ticket(owner, PANEL, name)
    out = run(nge.store_profile({"ticket": t, "model_id": model, "sample_count": 5,
                                 "embedding_base64": base64.b64encode((vec if vec is not None else unit(5)).tobytes()).decode()},
                                DEVICE))
    return out["guest_id"]


def test_a_profile_without_a_live_ticket_for_this_panel_is_refused(env):
    body = {"model_id": MID, "embedding_base64": base64.b64encode(unit(5).tobytes()).decode()}
    for ticket, caller in (("", DEVICE), ("forged", DEVICE)):
        with pytest.raises(nge.GuestError) as e:
            run(nge.store_profile({**body, "ticket": ticket}, caller))
        assert e.value.status == 403
    t = nge.issue_ticket(OWNER, PANEL, "Quinn")
    with pytest.raises(nge.GuestError):
        run(nge.store_profile({**body, "ticket": t}, {**DEVICE, "panel_id": "panel-2"}))     # another panel
    with pytest.raises(nge.GuestError):
        run(nge.store_profile({**body, "ticket": t}, DEVICE))                                # single use: already taken
    assert env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]

def test_enrolment_links_the_members_existing_person_and_replaces_their_old_print(env):
    env.conn.execute("INSERT INTO people (id, user_id, name, visibility) VALUES ('q1', ?, 'Quinn', 'personal')", (OWNER,))
    assert enrol(env) == "q1" and enrol(env, vec=unit(6)) == "q1"
    rows = env.q("SELECT people_id, channel, embedding FROM learned_voiceprints")
    assert len(rows) == 1 and rows[0][:2] == ("q1", "panel") and rows[0][2] == unit(6).tobytes()
    assert env.q("SELECT COUNT(*) FROM people") == [(1,)]
    other = enrol(env, owner=OTHER)                                  # another member's Quinn is THEIR person, in THEIR layer
    assert other != "q1" and env.q("SELECT user_id, visibility FROM people WHERE id = ?", (other,)) == [(OTHER, "personal")]

def test_a_member_or_a_child_is_never_enrolled_as_a_guest(env):
    with pytest.raises(nge.GuestError):
        enrol(env, name="Blake")                                      # a household member enrols themselves
    env.conn.execute("INSERT INTO people (id, user_id, name, relationship, visibility) VALUES ('k', ?, 'Kit', 'son', 'personal')",
                     (OWNER,))
    with pytest.raises(nge.GuestError):
        enrol(env, name="Kit")

def test_the_spoken_tier_needs_a_verified_adult_member(env, monkeypatch):
    queued = []

    async def fake_start(owner, name, panel):
        queued.append((owner, name, panel))
        return True
    monkeypatch.setattr(nge, "start_enrolment", fake_start)
    say = lambda uid, verified, text="hey zoe learn quinn's voice": run(nge.tier(  # noqa: E731
        text, uid, "s", channel="voice", speaker_verified=verified, panel_id=PANEL))
    assert "Only someone who lives here" in say("guest", None).reply
    assert "grown-up" in say("member-kid", True).reply
    assert "not sure who's asking" in say(OWNER, False).reply
    assert queued == []
    assert say(OWNER, True).intent == "named_guest_learn" and queued == [(OWNER, "Quinn", PANEL)]
    assert say(OWNER, None).intent == "named_guest_learn"           # a fresh PIN: the member, no voice verdict
    assert "their own account" in say(OWNER, True, "learn blake's voice").reply
    assert say(OWNER, True, "what's the weather") is None
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "shadow")
    assert say(OWNER, True) is None

def test_classify_reads_the_name_and_never_a_pronoun():
    assert nge.classify("Hey Zoe, learn Quinn's voice.") == ("learn", "Quinn")
    assert nge.classify("can you remember the voice of quinn") == ("learn", "Quinn")
    assert nge.classify("forget Quinn's voice") is None                   # forgetting is the memory PR's
    assert nge.classify("learn my voice") is None and nge.classify("forget her voice") is None


def test_a_profile_that_sounds_like_a_member_is_refused(env):
    """The member (not Quinn) said the phrases: stored, it would make the member's own voice a guest that drops their claims."""
    member = unit(70)
    env.conn.execute("INSERT INTO speaker_profiles (id, user_id, embedding_blob, consent_at, model_id) VALUES ('p', ?, ?, 'x', ?)",
                     (OWNER, member.tobytes(), MID))
    env.conn.commit()
    with pytest.raises(nge.GuestError) as e:
        enrol(env, vec=member)
    assert e.value.status == 409 and env.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]
    assert enrol(env, vec=unit(71))                                  # another voice is fine

