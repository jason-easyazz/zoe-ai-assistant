"""Caregiver curation BY VOICE (memories-space P7): "Zoe, remember that Juno\x27s swimming is on Tuesdays" from a curator in their OWN
panel identity (``memories_voice._curate`` -> ``memory_curation.by_voice``). VOICE-PATH (inside ``fast_tiers._provenance_tier``).

Synthetic members only (the fixture of ``test_memory_curation``). Walls, each with a control: a child, a guest, a rejected speaker,
the chat lane and a dry turn never curate (the existing teach path runs: ``None``); a sensitive note is refused aloud without the
opt-in; the confirmation never repeats the note; flag off is inert.
"""
from __future__ import annotations

import asyncio

import pytest

import memories_voice as mv
import memory_curation as mc
from test_memory_curation import ADULT, KID, KID2, PARENT, H, as_, env, rows_of  # noqa: F401 - env is the shared fixture

pytestmark = pytest.mark.ci_safe
PANEL = "panel-kitchen"


def say(text, member, *, verified=None, channel="voice", allow_writes=True, panel=PANEL):
    ctx = dict(panel_id=panel)
    if member is not None:
        ctx["scope_user"] = member
    if verified is not None:
        ctx["speaker_verified"] = verified
    out = asyncio.run(mv.tier(text, "guest", "s-voice", channel=channel, extra_ctx=ctx, allow_writes=allow_writes))
    return out.reply if out is not None else None


def test_a_curator_adds_a_note_for_a_child_by_voice(env):
    assert say("Zoe, remember that Juno\x27s swimming is on Tuesdays", PARENT) == "Okay, I\x27ll remember that for Juno."
    (row,) = rows_of(env, KID)
    md = row.metadata
    assert row.text == "User\x27s swimming is on Tuesdays" and md["origin"] == "curator"
    assert md["curated_via"] == "voice" and md["curated_category"] == "routines" and md["curated_by"] == PARENT
    assert not rows_of(env, PARENT), "never filed in the curator own memory"


def test_a_child_a_guest_a_rejected_voice_chat_and_a_dry_turn_never_curate(env):
    text = "remember that Rafe\x27s swimming is on Tuesdays"
    assert say(text, KID) is None                                     # a child: their own teach path, unchanged
    assert say(text, None) is None                                    # nobody identified on the panel
    assert say(text, PARENT, verified=False) is None                  # the speaker gate said not the curator
    assert say(text, PARENT, channel="chat") is None                  # chat is not this tier
    assert say(text, PARENT, allow_writes=False) is None              # a dry turn writes nothing
    assert say(text, ADULT) is None                                   # an adult, not an admin
    assert not rows_of(env, KID2)
    assert say(text, PARENT) == "Okay, I\x27ll remember that for Rafe."  # control: the curator
    assert len(rows_of(env, KID2)) == 1


def test_a_sensitive_note_is_refused_aloud_without_the_opt_in(env):
    out = say("remember that Juno is allergic to peanuts", PARENT)
    assert out == "Health notes for Juno are off. Turn them on first."
    assert not rows_of(env, KID)
    as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    out = say("remember that Juno is allergic to peanuts", PARENT)
    assert out == "Okay, I\x27ll remember that for Juno." and "peanut" not in out
    assert rows_of(env, KID)[0].metadata["curated_category"] == "health"


def test_not_a_curation_turn_falls_through(env):
    for text in ("remember that I have a dentist appointment on Friday", "remember to buy milk",
                 "remember that Mum\x27s birthday is in May", "what do you know about me?"):
        assert say(text, PARENT) is None, text
    assert not rows_of(env, KID) and not rows_of(env, PARENT)


def test_an_adult_only_by_their_own_opt_in(env):
    assert say("remember that Wren\x27s bins go out on Monday night", PARENT) is None      # not curatable: the teach path runs
    as_(env, ADULT, "user").put("/api/memories/curation/me/routines", json=dict(on=True), headers=H)
    assert say("remember that Wren\x27s bins go out every Monday night", PARENT) == "Okay, I\x27ll remember that for Wren."
    # opted in to routines only: a note of another category is not curation at all (the teach path runs, nothing spoken)
    assert say("remember that Wren\x27s teacher is Ms Lee", PARENT) is None
    assert say("remember that Wren is allergic to peanuts", PARENT) is None
    assert len(rows_of(env, ADULT)) == 1


def test_flag_off_is_inert(env, monkeypatch):
    monkeypatch.delenv("ZOE_MEMORIES_CURATORS")
    assert say("remember that Juno\x27s swimming is on Tuesdays", PARENT) is None
    assert not rows_of(env, KID)


def test_parse_and_category_are_pure():
    assert mc.parse_voice("Hey Zoe, can you remember that Juno’s teacher is Ms Lee.") == ("Juno", "Juno’s teacher is Ms Lee")
    assert mc.parse_voice("remember that I like tea") is None
    pol = mc.Policy(mc.Account("k", "Juno", "child", "child"), frozenset(mc.CATEGORIES))
    assert mc.guess_category("Juno\x27s teacher is Ms Lee", pol) == "school"
    assert mc.guess_category("Juno takes ventolin before sport", pol) == "medication"
    assert mc.guess_category("Juno is allergic to peanuts", pol) == "health"


def test_plural_and_colloquial_medication_words_are_medication_never_health():
    """Codex #2159 P1: plurals slipped past _MEDS_RE and were filed as health when Health was on and Medication off."""
    pol = mc.Policy(mc.Account("k", "Juno", "child", "child"), frozenset(mc.CATEGORIES))
    for s in ("Juno takes two medications every day", "Juno has some medicines in her bag", "Juno needs her meds at lunch",
              "Juno has her pills at night", "Juno has three prescriptions", "Juno forgot her inhalers"):
        assert mc.guess_category(s, pol) == "medication", s
    # negative control: a non-medication health note still files as health, and "medal" is not "meds"
    assert mc.guess_category("Juno is allergic to peanuts", pol) == "health"
    assert mc.guess_category("Juno won a medal at sports day", pol) != "medication"


# -- person-centred memory D6 / D7 / forgotten-means-forever (accepted 2026-11-03) ---------------------------------------------

def _raw(text, ctx):
    out = asyncio.run(mv.tier(text, "guest", "s-voice", channel="voice", extra_ctx=ctx, allow_writes=True))
    return out.reply if out is not None else None


def test_d6_an_unverified_or_guest_voice_gets_nothing(env):
    text = "remember that Juno\x27s swimming is on Tuesdays"
    assert _raw(text, dict(panel_id=PANEL, scope_user=None)) is None              # the lane's shape: key present, nobody verified
    assert _raw(text, dict(panel_id=PANEL, scope_user="guest")) is None
    assert _raw(text, dict(panel_id=PANEL, scope_user=PANEL)) is None             # the panel itself is never a member
    assert _raw(text, dict(scope_user=PARENT)) is None                            # not a panel turn
    assert not rows_of(env, KID)
    assert _raw(text, dict(panel_id=PANEL, scope_user=PARENT, speaker_verified=True)) == "Okay, I\x27ll remember that for Juno."


@pytest.fixture
def ledger(env, monkeypatch):
    import memory_forgotten as mf
    import memory_tombstones

    monkeypatch.setenv(mf.SALT_ENV, "curation-voice-test-salt-0123456789")
    monkeypatch.delenv(mf.SHIELD_DAYS_ENV, raising=False)
    mf.set_backend(mf.MemoryBackend())
    memory_tombstones.clear_all()
    yield mf
    mf.set_backend(None)
    memory_tombstones.clear_all()


def test_a_forgotten_name_is_not_re_added_by_voice_and_the_reply_never_repeats_it(env, ledger):
    asyncio.run(ledger.add(KID, "Ms Lee", actor=KID))
    out = say("remember that Juno\x27s teacher is Ms Lee", PARENT)
    assert out == mc.FORGOTTEN_DETAIL and "Lee" not in out
    assert not rows_of(env, KID) and not rows_of(env, PARENT)
    asyncio.run(ledger.add(PARENT, "Juno", actor=PARENT))                          # the curator forgot the child
    assert say("remember that Juno\x27s swimming is on Tuesdays", PARENT) == mc.FORGOTTEN_DETAIL
    assert not rows_of(env, KID)
    assert say("remember that Rafe\x27s swimming is on Tuesdays", PARENT) == "Okay, I\x27ll remember that for Rafe."  # control


def test_a_medication_note_is_never_filed_under_an_enabled_health_switch(env):
    c = as_(env, PARENT, "admin")
    c.put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    out = say("remember that Juno takes her inhaler before sport", PARENT)
    assert out == "Medication notes for Juno are off. Turn them on first." and "inhaler" not in out
    assert not rows_of(env, KID)
    c.put(f"/api/memories/curation/members/{KID}/sensitive/medication", json=dict(on=True), headers=H)
    assert say("remember that Juno takes her inhaler before sport", PARENT) == "Okay, I\x27ll remember that for Juno."  # control
    assert rows_of(env, KID)[0].metadata["curated_category"] == "medication"


def test_two_children_with_one_first_name_are_asked_about_never_guessed(env):
    for sql, args in (("INSERT INTO users VALUES (?, ?, ?)", ("demo_bar_0000000f", "member", "Juno Other")),
                      ("INSERT INTO auth_users VALUES (?, ?)", ("demo_bar_0000000f", "child"))):
        env.db.conn.execute(sql, args)
    env.db.conn.commit()
    assert say("remember that Juno\x27s swimming is on Tuesdays", PARENT) == mc.AMBIGUOUS_REPLY.format(name="Juno")
    assert not rows_of(env, KID) and not rows_of(env, "demo_bar_0000000f")
    assert say("remember that Rafe\x27s swimming is on Tuesdays", PARENT) == "Okay, I\x27ll remember that for Rafe."  # control


def test_flag_off_then_on_with_the_same_words(env, monkeypatch):
    text = "remember that Juno\x27s swimming is on Tuesdays"
    monkeypatch.delenv("ZOE_MEMORIES_CURATORS")
    assert say(text, PARENT) is None and not rows_of(env, KID)
    monkeypatch.setenv("ZOE_MEMORIES_CURATORS", "1")
    assert say(text, PARENT) == "Okay, I\x27ll remember that for Juno." and len(rows_of(env, KID)) == 1   # control


def test_the_curator_off_the_record_turn_writes_nothing(env):
    """Codex #2159: the turn is marked off the record against the CURATOR's id (fast_tiers._provenance_tier), but the note is written
    under the CHILD's id, so the ingest wall (which reads the child's marks) let it through. The curator path asks the speaker."""
    import memory_provenance as mp

    text = "remember that Juno\x27s swimming is on Tuesdays"
    mp.reset()
    try:
        assert mp.claim_turn(PARENT, "off the record") is False                   # a bare cue arms the curator's next turn
        assert mp.claim_turn(PARENT, text) is True                                 # what fast_tiers does before this tier
        assert say(text, PARENT) == mc.OFF_RECORD_REPLY
        assert not rows_of(env, KID) and not rows_of(env, PARENT)
        mp.reset()
        # a cue in the same words counts too
        assert say(text + ", off the record", PARENT) == mc.OFF_RECORD_REPLY
        assert not rows_of(env, KID)
        mp.reset()
        # control: a curator turn nobody put off the record is saved
        assert say(text, PARENT) == "Okay, I\x27ll remember that for Juno." and len(rows_of(env, KID)) == 1
    finally:
        mp.reset()


def _passes_after(monkeypatch, text, user, reply="Okay."):
    """Run the REAL post-turn pass scheduler with its writers recorded; returns the writers that ran."""
    import latent_intent_detector
    import memory_digest
    import memory_extractor
    import person_extractor
    import person_extractor_llm
    import routers.voice_tts as v
    import struggle_detector

    called: list = []

    def rec(name):
        async def fn(*_a, **_k):
            called.append(name)
            return 0
        return fn

    monkeypatch.setattr(memory_extractor, "extract_and_ingest", rec("extract"))
    monkeypatch.setattr(memory_digest, "run_turn_digest", rec("digest"))
    monkeypatch.setattr(person_extractor, "process_text", rec("person"))
    monkeypatch.setattr(person_extractor_llm, "process_text_llm", rec("person_llm"))
    monkeypatch.setattr(latent_intent_detector, "detect_and_store", rec("suggest"))
    monkeypatch.setattr(v, "_spawn_bg", lambda coro: asyncio.get_event_loop().create_task(coro))
    monkeypatch.setattr(struggle_detector, "observe", lambda *a, **k: False)

    async def go():
        await v._run_voice_memory_passes(text, reply, user, "voice-panel-s1")
        await asyncio.sleep(0)

    asyncio.run(go())
    return called


def test_a_handled_curation_turn_is_never_mined_into_the_curators_own_store(env, monkeypatch):
    """Codex #2159: the panel path schedules the memory passes for EVERY answered turn, so a curation write - or its refusal - would
    otherwise be mined as "User asked me to remember ..." under the curator's account (bypassing the sensitive opt-in)."""
    mv._CURATION_TURNS.clear()
    refused = "remember that Juno is allergic to peanuts"            # Health is off for Juno: refused aloud
    assert say(refused, PARENT) == "Health notes for Juno are off. Turn them on first."
    assert _passes_after(monkeypatch, refused, PARENT) == []
    done = "remember that Juno\x27s swimming is on Tuesdays"         # a successful add
    assert say(done, PARENT) == "Okay, I\x27ll remember that for Juno."
    assert _passes_after(monkeypatch, done, PARENT) == []
    # negative control: the same rig DOES mine an ordinary turn of the same member
    ordinary = "remember that I have a dentist appointment on Friday"
    assert "extract" in _passes_after(monkeypatch, ordinary, PARENT)
    mv._CURATION_TURNS.clear()
    assert "extract" in _passes_after(monkeypatch, done, PARENT)      # and the marker expires / clears: without it the pass runs


def test_the_curation_marker_is_exact_and_short_lived():
    mv._CURATION_TURNS.clear()
    mv.note_curation_handled("u1", "Remember that Juno's swimming is on Tuesdays.", now=100.0)
    assert mv.curation_handled("u1", "remember that juno's swimming is on tuesdays", now=101.0)
    assert not mv.curation_handled("u2", "remember that juno's swimming is on tuesdays", now=101.0)      # another member
    assert not mv.curation_handled("u1", "remember that juno's swimming is on wednesdays", now=101.0)    # other words
    assert not mv.curation_handled("u1", "remember that juno's swimming is on tuesdays", now=100.0 + mv._CURATION_TTL_S + 1)
    mv._CURATION_TURNS.clear()
