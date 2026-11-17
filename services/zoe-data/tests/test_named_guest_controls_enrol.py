"""Named guests - negative controls the 2026-11-14 mutation audit found missing: enrolment, passive learning, saved clips.

Same method as ``test_named_guest_controls.py``: each test fails when the guard it names is removed. Invented names only.
"""
from __future__ import annotations

import base64
from datetime import timedelta

import pytest

import memory_forgotten as mf
import named_guest_clips as ngc
import named_guest_enrol as nge
import named_guest_learning as ngl
import named_guest_memory as ngm
import named_guests as ng
from named_guest_support import (  # noqa: F401 - env is a fixture
    DEVICE, MID, OTHER, OWNER, PANEL, add_guest, env, run, unit,
)
from test_named_guest_clips import ask as clip_ask, clip, clips  # noqa: F401 - clips is a fixture
from test_named_guest_controls import strict_outage
from test_named_guest_learning import T0, learn, near, say, turn  # noqa: F401 - learn is a fixture

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _tickets():
    nge.reset()
    yield
    nge.reset()


def body(ticket, vec=None, model=MID):
    return {"ticket": ticket, "model_id": model, "sample_count": 5,
            "embedding_base64": base64.b64encode((vec if vec is not None else unit(5)).tobytes()).decode()}


def refused(payload, status):
    with pytest.raises(nge.GuestError) as e:
        run(nge.store_profile(payload, DEVICE))
    assert e.value.status == status


def prints(db):
    return db.q("SELECT COUNT(*) FROM learned_voiceprints")[0][0]


def tier(text, uid=OWNER, verified=True, channel="voice", **kw):
    return run(nge.tier(text, uid, "s", channel=channel, speaker_verified=verified, panel_id=PANEL, **kw))


@pytest.fixture
def pin(monkeypatch):
    """Who holds a fresh PIN sign-in on the panel (``state["who"]``; an exception class makes the lookup fail)."""
    import biometric_scope

    state = {"who": None}

    async def fresh(panel):
        if isinstance(state["who"], type):
            raise state["who"]("session store down")
        return state["who"]
    monkeypatch.setattr(biometric_scope, "_fresh_panel_member", fresh)
    return state


# ── the ticket and the store ──────────────────────────────────────────────────────────────────────────────────────────────

def test_a_ticket_expires_and_a_new_ask_at_the_panel_replaces_the_old_one(env):
    refused(body(nge.issue_ticket(OWNER, PANEL, "Quinn", ttl_s=-1)), 403)
    first = nge.issue_ticket(OWNER, PANEL, "Quinn")
    second = nge.issue_ticket(OTHER, PANEL, "Rowan")
    refused(body(first), 403)                                        # one live ask per panel: the earlier one is void
    assert prints(env) == 0
    assert run(nge.store_profile(body(second), DEVICE))["display_name"] == "Rowan"


def test_the_store_refuses_when_off_and_anything_that_is_not_this_models_voiceprint(env, monkeypatch):
    refused(body(nge.issue_ticket(OWNER, PANEL, "Quinn"), model="another-model"), 400)
    refused({**body(nge.issue_ticket(OWNER, PANEL, "Quinn")), "embedding_base64": base64.b64encode(b"\x00" * 1024).decode()}, 400)
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "off")
    refused(body(nge.issue_ticket(OWNER, PANEL, "Quinn")), 404)
    assert prints(env) == 0


def test_a_member_likeness_check_that_cannot_run_refuses_the_profile(env):
    env.conn.execute("DROP TABLE speaker_profiles")                  # the check cannot read the members' profiles
    refused(body(nge.issue_ticket(OWNER, PANEL, "Quinn")), 409)
    assert prints(env) == 0


def test_a_ledger_outage_stores_nothing_and_starts_nothing(env, monkeypatch):
    import voice_announce
    import voice_enrollment as ve

    queued = []

    async def panels(db):
        return {PANEL}

    async def enqueue(db, **kw):
        queued.append(kw)
    monkeypatch.setattr(ve, "_device_panels", panels)
    monkeypatch.setattr(voice_announce, "enqueue_command", enqueue)
    assert run(nge.start_enrolment(OWNER, "Quinn", "panel-unknown")) is False and queued == [] and not nge._TICKETS
    assert run(nge.start_enrolment(OWNER, "Quinn", PANEL)) is True and len(queued) == 1      # control: this rig can start one
    ticket = queued[0]["command"]["guest"]["ticket"]
    strict_outage(monkeypatch)
    refused(body(ticket), 503)
    assert run(nge.start_enrolment(OWNER, "Rowan", PANEL)) is False and len(queued) == 1 and prints(env) == 0


def test_an_ordinary_enrolment_never_releases_a_forgotten_name(env, monkeypatch):
    released = []

    async def release(uid, text):
        released.append(text)
    monkeypatch.setattr(mf, "release", release)
    assert run(nge.store_profile(body(nge.issue_ticket(OWNER, PANEL, "Quinn")), DEVICE))["ok"] and released == []


# ── the spoken tier ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_rejected_voice_is_refused_even_at_the_members_signed_in_panel(env, monkeypatch, pin):
    started = []

    async def start(owner, name, panel):
        started.append(name)
        return True
    monkeypatch.setattr(nge, "start_enrolment", start)
    add_guest(env)
    pin["who"] = OWNER                                               # the member's fresh PIN is open; someone ELSE is speaking
    assert "not sure who's asking" in tier("learn rowan's voice", verified=False).reply
    assert "not sure who's asking" in tier("forget quinn's voice", verified=False).reply
    pin["who"] = None
    assert "not sure who's asking" in tier("forget quinn's voice", verified=None).reply      # no verdict and no PIN
    pin["who"] = RuntimeError                                        # the proof cannot be read: refused, never assumed
    assert "not sure who's asking" in tier("learn rowan's voice", verified=None).reply
    assert started == [] and prints(env) == 1
    pin["who"] = OWNER
    assert "forgotten Quinn's voice" in tier("forget quinn's voice", verified=None).reply and prints(env) == 0     # control


def test_a_read_only_turn_or_a_typed_channel_changes_nothing(env, monkeypatch):
    started = []

    async def start(owner, name, panel):
        started.append(name)
        return True
    monkeypatch.setattr(nge, "start_enrolment", start)
    add_guest(env)
    assert tier("forget quinn's voice", allow_writes=False) is None and tier("learn rowan's voice", allow_writes=False) is None
    assert "only learn a voice at the panel" in tier("learn rowan's voice", channel="chat").reply
    assert started == [] and prints(env) == 1


def test_forget_and_a_name_from_a_guest_or_a_child_is_ordinary_speech(env):
    add_guest(env)
    for uid in ("guest", "member-kid", OTHER):
        assert tier("forget quinn", uid=uid) is None                 # not theirs to forget: the turn goes on as it always did
    assert prints(env) == 1


# ── passive learning ──────────────────────────────────────────────────────────────────────────────────────────────────────

def asked(v):
    say(v, 6, session="s1")
    assert say(v, 2, session="s2", start=T0 + timedelta(hours=3))["action"] == "ask"


def answer(v, text, minutes=3, seed=77):
    return run(ngl.observe(turn(near(v, 0.85, seed)), DEVICE, session_id="s2", wake=False, text=text,
                           now=T0 + timedelta(hours=3, minutes=minutes)))


def test_only_the_voice_that_was_asked_can_answer_with_a_name(learn):
    v = unit(36)
    asked(v)
    other = run(ngl.observe(turn(unit(99)), DEVICE, session_id="s2", wake=False, text="I'm Rowan", now=T0 + timedelta(hours=3, minutes=2)))
    assert other["action"] == "kept" and prints(learn) == 0          # another voice in the room names nobody
    assert answer(v, "I'm Quinn")["action"] == "named"               # control: the asked voice can


def test_shadow_or_a_ledger_outage_never_makes_a_guest_from_an_answer(learn, monkeypatch):
    v = unit(36)
    asked(v)
    monkeypatch.setenv("ZOE_GUEST_VOICE_LEARN", "shadow")
    assert answer(v, "I'm Quinn")["action"] != "named"
    monkeypatch.setenv("ZOE_GUEST_VOICE_LEARN", "on")
    strict_outage(monkeypatch)
    assert answer(v, "I'm Quinn", minutes=4, seed=78)["action"] != "named" and prints(learn) == 0


def test_zoe_asks_once_and_an_answer_is_always_a_new_person(learn):
    learn.conn.execute("INSERT INTO people (id, user_id, name, visibility) VALUES ('old', ?, 'Quinn', 'personal')", (OWNER,))
    v = unit(36)
    asked(v)
    assert answer(v, "what's the weather")["action"] == "kept"       # asked already: not asked again on the next turn
    named = answer(v, "I'm Quinn", minutes=5, seed=78)
    assert named["action"] == "named" and named["guest"].person_id != "old"      # never linked to the member's own Quinn


def test_a_voice_from_another_model_or_with_nobody_to_hold_it_is_not_kept(learn):
    foreign = turn(unit(31))
    foreign["speaker"]["model_id"] = "another-model"
    assert run(ngl.observe(foreign, DEVICE, session_id="s", wake=True))["action"] == "skip"
    learn.conn.execute("UPDATE users SET role = 'member'")           # no admin: nobody to hold an unknown voice for
    assert run(ngl.observe(turn(unit(31)), DEVICE, session_id="s", wake=True))["action"] == "skip"
    assert learn.q("SELECT COUNT(*) FROM panel_voice_clusters") == [(0,)] and learn.q("SELECT COUNT(*) FROM panel_voice_samples") == [(0,)]


def test_a_voice_keeps_at_most_thirty_samples_and_erase_all_takes_every_one(learn):
    say(unit(41), ngl.MAX_SAMPLES + 4)
    assert learn.q("SELECT COUNT(*) FROM panel_voice_samples") == [(ngl.MAX_SAMPLES,)]
    assert run(ngl.erase_unknown()) == 1 and learn.q("SELECT COUNT(*) FROM panel_voice_samples") == [(0,)]


def test_the_ask_tells_the_brain_to_share_nothing_private():
    assert "share nothing private about the household" in ngl.ask_line()


# ── saved clips ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_clip_is_not_played_on_a_read_only_turn_or_to_a_rejected_voice_at_a_signed_in_panel(clips, pin):
    clip(clips.dir, "a", clips.cluster)
    pin["who"] = OWNER
    assert clip_ask("who was that this morning", verified=False) is None
    assert run(ngc.tier("who was that this morning", OWNER, "s", channel="voice", speaker_verified=True, panel_id=PANEL,
                        allow_writes=False)) is None
    assert clips.queued == []


def test_a_name_said_after_the_clip_has_lapsed_names_nobody(clips):
    clip(clips.dir, "a", clips.cluster)
    assert clip_ask("who was that this morning").intent == "named_guest_clip_play"
    ngc._PENDING[PANEL].expires = 0.0
    assert clip_ask("that's Quinn") is None and prints(clips) == 0
