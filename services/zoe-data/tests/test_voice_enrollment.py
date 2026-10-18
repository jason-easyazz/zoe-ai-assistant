"""Zoe-driven voice enrolment (``enrollment_state.py``, ``voice_enrollment.py``, ``routers/enrollment.py``; ``ZOE_ENROLL_OFFER``).

The owner's requirement: "if enrolment hasn't been done, zoe needs to know this, and prompt the user, if they decline that is fine but they
aren't going to be running a piece of code". Groups (each has a break-the-fix control, run by editing the module - see the PR):

  policy       who is offered (adult members only; never guests, synthetic ids, children by default, unknown roles), when (not enrolled, not
               declined inside the quiet period, nothing in 24 h, not in quiet hours), and that every refusal names its reason
  state        enrolment is READ from the profile tables, a failed read is "unknown" (never "no"), one offer slot per state read
  decline      a no is remembered for the quiet period, three ignored offers count as a no, asking for it yourself clears it
  the talk     the spoken yes / no (the repo's confirm machinery + the language file), "learn my voice", "forget my voice", "do you know my voice?"
  the offer    spoken through the announcement queue with the privacy statement; the daemon is told to listen for the answer
  commands     the enrol command carries the member's language script; forgetting queues a purge for EVERY panel; old daemons never read JSON aloud
  the read API own state / admin household / device result; a device token owns nothing
  language     English and Spanish files have the same keys; nothing is offered in a language with no file

Synthetic data only (invented members). The DB edge is a real SQLite file built by migration 0046 behind ``db_compat``.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import contextlib
import importlib.util
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db_compat
import enrollment_state as es
import voice_announce
import voice_enrollment as ve

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
ADULT, KID, MINOR_FLAG, ODD = "ann", "kiddo", "mia", "odd"
PANEL = "zoe-touch-pi"          # a panel that holds a live device token in the fixture (as does zoe-touch-hall)

_SCHEMA = [
    """CREATE TABLE speaker_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL, embedding_blob BLOB NOT NULL,
       enrolled_at TEXT DEFAULT CURRENT_TIMESTAMP, sample_count INTEGER DEFAULT 0, panel_id TEXT, consent_at TEXT, model_id TEXT)""",  # 0051
    "CREATE TABLE face_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, active INTEGER DEFAULT 1)",
    "CREATE TABLE device_tokens (panel_id TEXT, revoked INTEGER DEFAULT 0)",
    """CREATE TABLE voice_announcements (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, panel_id TEXT, message TEXT NOT NULL,
       trigger_type TEXT DEFAULT '', created_at TEXT NOT NULL, expires_at TEXT NOT NULL, delivered_at TEXT, delivered_to TEXT,
       expired INTEGER NOT NULL DEFAULT 0, played_at TEXT)""",
    "CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, username TEXT, role TEXT NOT NULL)",
]
_ACCOUNTS = {ADULT: (False, "user"), "bob": (False, "admin"), KID: (False, "child"), MINOR_FLAG: (True, "user"), ODD: (False, "weirdrole")}


def run(coro):
    return asyncio.run(coro)


def _migrate(engine, fname):
    spec = importlib.util.spec_from_file_location("mig_" + fname[:4], SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    _migrate(engine, "0046_enrollment_offers.py")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(sa.text(ddl))
        for uid, (_m, role) in _ACCOUNTS.items():
            conn.execute(sa.text("INSERT INTO auth_users VALUES (:u, :u, :r)"), {"u": uid, "r": role})
        for panel in ("zoe-touch-pi", "zoe-touch-hall"):
            conn.execute(sa.text("INSERT INTO device_tokens VALUES (:p, 0)"), {"p": panel})
        conn.execute(sa.text("INSERT INTO device_tokens VALUES ('zoe-touch-old', 1)"))

    @contextlib.asynccontextmanager
    async def fake_db():
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    async def facts(uid, db=None):
        if uid not in _ACCOUNTS:
            return False, ""
        return _ACCOUNTS[uid]

    async def name(uid):
        return "Ann"

    import memory_provenance as mp

    state = {"now": NOW, "path": path, "noted": []}
    # The provenance ledger persists a reply from a fire-and-forget task on its own DB connection; outside this test's concern.
    monkeypatch.setattr(mp, "note_direct_reply", lambda uid, tier, session_id="", **kw: state["noted"].append((uid, tier)))
    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(es, "_account_facts", facts)
    monkeypatch.setattr(es, "_now", lambda: state["now"])
    monkeypatch.setattr(ve, "_display_name", name)
    monkeypatch.setattr(ve, "_in_quiet_hours", lambda now: False)
    monkeypatch.setattr(ve, "CLOSE_OFFER_DELAY_S", 0.0)
    for key in ("ZOE_ENROLL_OFFER", "ZOE_ENROLL_OFFER_KIDS", "ZOE_ENROLL_DECLINE_DAYS", "ZOE_ENROLL_OFFER_PIN_DELAY_S",
                "ZOE_SYNTHETIC_USER_ALLOWLIST"):
        monkeypatch.delenv(key, raising=False)
    ve.reset()
    yield state
    ve.reset()


def on(monkeypatch, kids=False):
    monkeypatch.setenv("ZOE_ENROLL_OFFER", "on")
    if kids:
        monkeypatch.setenv("ZOE_ENROLL_OFFER_KIDS", "1")


def sql(env, query, params=()):
    import sqlite3

    con = sqlite3.connect(env["path"])
    try:
        rows = con.execute(query, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def enrol(env, uid, samples=3, consent=True):
    sql(env, "INSERT INTO speaker_profiles (id, user_id, display_name, embedding_blob, sample_count, consent_at) VALUES (?,?,?,?,?,?)",
        (f"p-{uid}", uid, uid, b"\x00" * 1024, samples, "2026-09-01 00:00:00" if consent else None))


def queued(env, trigger=None):
    q = "SELECT trigger_type, message, panel_id, user_id FROM voice_announcements"
    rows = sql(env, q + (" WHERE trigger_type = ?" if trigger else ""), (trigger,) if trigger else ())
    return rows


async def say(text, uid=ADULT, *, channel="voice", panel=PANEL, verified=None, writes=True):
    res = await ve.tier(text, uid, "s1", channel=channel, speaker_verified=verified, panel_id=panel, allow_writes=writes)
    return None if res is None else res.reply


# ── policy ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _state(**kw):
    base = dict(user_id=ADULT, voice_enrolled=False)
    base.update(kw)
    return es.SetupState(**base)


def decide(state=None, **kw):
    args = dict(audience="adult", moment="pin_stepup", now=NOW, flag="on", kids=False, quiet_hours=False)
    args.update(kw)
    return es.decide_offer(state or _state(), **args)


def test_an_unenrolled_adult_is_offered_at_both_moments():
    assert decide().offer and decide(moment="conversation_end").offer


def test_the_flag_gates_everything_and_shadow_decides_without_saying():
    assert decide(flag="off").reason == "flag_off"
    assert decide(flag="shadow").offer          # shadow decides; voice_enrollment.maybe_offer then queues nothing


@pytest.mark.parametrize("audience,reason", [("guest", "guest"), ("synthetic", "synthetic"), ("unknown", "audience_unknown"),
                                            ("minor", "minor_not_enabled")])
def test_only_a_positively_known_adult_is_offered(audience, reason):
    d = decide(audience=audience)
    assert (d.offer, d.reason) == (False, reason)


def test_a_child_is_offered_only_when_the_owner_switched_it_on():
    assert decide(audience="minor", kids=True).offer
    assert not decide(audience="minor", kids=False).offer


def test_already_enrolled_and_unknown_state_are_never_offered():
    assert decide(_state(voice_enrolled=True)).reason == "already_enrolled"
    assert decide(_state(voice_enrolled=None)).reason == "state_unknown"      # a failed read is not "no"


def test_a_decline_holds_for_the_quiet_period_then_lapses():
    declined = _state(declined_at=es.fmt(NOW - timedelta(days=1)), declined_until=es.fmt(NOW + timedelta(days=59)))
    assert decide(declined).reason == "declined"
    lapsed = _state(declined_at=es.fmt(NOW - timedelta(days=61)), declined_until=es.fmt(NOW - timedelta(days=1)))
    assert decide(lapsed).offer


def test_at_most_one_offer_a_day_whichever_moment_made_it():
    assert decide(_state(last_offered_at=es.fmt(NOW - timedelta(hours=23)))).reason == "offered_recently"
    assert decide(_state(last_offered_at=es.fmt(NOW - timedelta(hours=25)))).offer


def test_never_in_quiet_hours_and_never_at_an_unknown_moment():
    assert decide(quiet_hours=True).reason == "quiet_hours"
    assert decide(moment="mid_task").reason == "bad_moment"


def test_asking_for_it_yourself_needs_only_the_person_rules():
    assert es.can_self_request("adult", kids=False).offer
    assert not es.can_self_request("guest", kids=True).offer
    assert not es.can_self_request("synthetic", kids=True).offer
    assert not es.can_self_request("minor", kids=False).offer and es.can_self_request("minor", kids=True).offer
    assert not es.can_self_request("unknown", kids=True).offer


def test_audience_of_reads_the_account_not_the_id(env):
    async def go():
        got = {}
        for uid in (ADULT, "bob", KID, MINOR_FLAG, ODD, "nobody", "guest", "voice-daemon", "guest-77", "test-probe-1", "demo_user", ""):
            got[uid] = await es.audience_of(uid)
        return got

    got = run(go())
    assert got == {ADULT: "adult", "bob": "adult", KID: "minor", MINOR_FLAG: "minor", ODD: "unknown", "nobody": "unknown", "guest": "guest",
                   "voice-daemon": "guest", "guest-77": "guest", "test-probe-1": "synthetic", "demo_user": "synthetic", "": "guest"}


def test_a_failing_account_lookup_means_unknown_so_no_offer(env, monkeypatch):
    async def boom(uid, db=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(es, "_account_facts", boom)
    assert run(es.audience_of(ADULT)) == "unknown"


# ── state ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_enrolment_is_read_from_the_profile_tables(env):
    assert run(es.get_state(ADULT)).voice_enrolled is False
    enrol(env, ADULT, samples=3)
    st = run(es.get_state(ADULT))
    assert (st.voice_enrolled, st.voice_samples, st.voice_consented) == (True, 3, True)
    sql(env, "INSERT INTO face_profiles VALUES ('f1', ?, 1)", (ADULT,))
    assert run(es.get_state(ADULT)).face_enrolled is True
    pub = st.to_public(NOW)
    assert pub["voice"]["samples"] == 3 and "embedding" not in json.dumps(pub)


def test_an_unconsented_profile_is_not_enrolled(env):
    enrol(env, ADULT, consent=False)
    assert run(es.get_state(ADULT)).voice_enrolled is False


def test_a_failed_read_is_unknown_never_no(env):
    sql(env, "DROP TABLE speaker_profiles")
    st = run(es.get_state(ADULT))
    assert st.voice_enrolled is None and st.face_enrolled is False       # the other sources are still read
    assert not decide(st).offer


def test_only_one_of_two_racing_offers_gets_the_slot(env):
    async def go():
        st = await es.get_state(ADULT)
        return await asyncio.gather(es.claim_offer(ADULT, st, now=NOW), es.claim_offer(ADULT, st, now=NOW))

    assert sorted(run(go())) == [False, True]
    assert sql(env, "SELECT offer_count FROM enrollment_offers WHERE user_id = ?", (ADULT,)) == [(1,)]


def test_three_offers_nobody_answered_end_in_the_quiet_period_not_a_fourth_offer(env):
    async def go():
        claimed = []
        for i in range(4):
            st = await es.get_state(ADULT)
            claimed.append(await es.claim_offer(ADULT, st, now=NOW + timedelta(days=i + 1)))
        return claimed, await es.get_state(ADULT)

    claimed, st = run(go())
    assert claimed == [True, True, True, False]                           # the fourth is refused ...
    assert st.declined_now(NOW + timedelta(days=5)) and not st.declined_now(NOW + timedelta(days=70))   # ... and starts the quiet period
    assert st.declined_at is None                                         # it was never an explicit no
    assert sql(env, "SELECT offer_count FROM enrollment_offers")[0][0] == 3


def test_an_answer_resets_the_ignored_count(env):
    async def go():
        for i in range(2):
            await es.claim_offer(ADULT, await es.get_state(ADULT), now=NOW + timedelta(days=i + 1))
        await es.record_answer(ADULT, True, now=NOW + timedelta(days=3))
        return await es.get_state(ADULT)

    st = run(go())
    assert st.ignored_count == 0 and st.pending_since is None


def test_a_member_who_asked_first_can_still_be_offered_later_if_not_enrolled(env):
    run(es.record_answer(ADULT, True, now=NOW))                           # a row exists with no offer on it
    assert run(es.claim_offer(ADULT, run(es.get_state(ADULT)), now=NOW + timedelta(days=1))) is True


def test_a_no_starts_the_quiet_period_and_asking_clears_it(env):
    run(es.record_answer(ADULT, False, now=NOW))
    st = run(es.get_state(ADULT))
    assert st.declined_now(NOW + timedelta(days=59)) and not st.declined_now(NOW + timedelta(days=61))
    assert st.pending_since is None and st.declined_at
    run(es.record_answer(ADULT, True, now=NOW + timedelta(days=1)))
    st = run(es.get_state(ADULT))
    assert not st.declined_now(NOW + timedelta(days=2)) and st.accepted_at and st.declined_at is None


def test_the_quiet_period_is_configurable(env, monkeypatch):
    monkeypatch.setenv("ZOE_ENROLL_DECLINE_DAYS", "7")
    run(es.record_answer(ADULT, False, now=NOW))
    assert not run(es.get_state(ADULT)).declined_now(NOW + timedelta(days=8))


# ── the offer ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_offer_is_spoken_through_the_queue_with_the_privacy_statement(env, monkeypatch):
    on(monkeypatch)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "offered"
    rows = queued(env, "voice_enroll_offer")
    assert len(rows) == 1 and rows[0][2] == PANEL
    text = rows[0][1]
    assert "PIN" in text and "voice signature, not recordings" in text and "delete it" in text and text.endswith("Want to?")
    assert not ve.pending_for(ADULT)                                       # queued is not heard: nothing is pending yet
    assert "follow_up" in run(_claim(env))[0]                              # the daemon opens a listening window for the answer


def _claim(env):
    async def go():
        async with db_compat.get_compat_db() as db:
            return await voice_announce.claim_announcements(db, panel_id=PANEL)

    return go()


def test_shadow_decides_and_logs_but_says_and_queues_nothing(env, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_ENROLL_OFFER", "shadow")
    with caplog.at_level(logging.INFO, logger=ve.logger.name):
        assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "shadow"
    assert queued(env) == [] and not ve.pending_for(ADULT)
    assert any("would_offer" in r.getMessage() for r in caplog.records)


def test_off_does_no_work_at_all(env, monkeypatch):
    async def boom():
        raise AssertionError("the flag is off: nothing may touch the database")

    monkeypatch.setattr(db_compat, "get_compat_db", boom)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "flag_off"
    assert run(ve.tier("learn my voice", ADULT, "s", channel="voice", panel_id=PANEL)) is None
    ve.schedule_offer_after_pin(ADULT, PANEL)


@pytest.mark.parametrize("uid,why", [("guest", "guest"), ("test-probe-1", "synthetic"), (KID, "minor_not_enabled"), (MINOR_FLAG, "minor_not_enabled"),
                                    (ODD, "audience_unknown"), ("nobody", "audience_unknown")])
def test_nobody_but_an_adult_member_is_ever_offered(env, monkeypatch, uid, why):
    on(monkeypatch)
    assert run(ve.maybe_offer(uid, "conversation_end", PANEL)) == why
    assert queued(env) == []


def test_a_child_is_offered_when_the_owner_allows_it(env, monkeypatch):
    on(monkeypatch, kids=True)
    assert run(ve.maybe_offer(KID, "pin_stepup", PANEL)) == "offered"


def test_once_a_day_across_both_moments(env, monkeypatch):
    on(monkeypatch)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "offered"
    assert run(ve.maybe_offer(ADULT, "conversation_end", PANEL)) == "offered_recently"
    env["now"] = NOW + timedelta(hours=25)
    assert run(ve.maybe_offer(ADULT, "conversation_end", PANEL)) == "offered"
    assert len(queued(env, "voice_enroll_offer")) == 2


def test_no_panel_no_offer(env, monkeypatch):
    on(monkeypatch)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", "")) == "no_panel"
    assert queued(env) == []


def test_the_offer_after_a_pin_waits_and_then_goes_out(env, monkeypatch):
    on(monkeypatch)
    monkeypatch.setenv("ZOE_ENROLL_OFFER_PIN_DELAY_S", "0")

    async def go():
        ve.schedule_offer_after_pin(ADULT, PANEL)
        await asyncio.gather(*list(ve._TASKS))

    run(go())
    assert len(queued(env, "voice_enroll_offer")) == 1


def test_the_close_of_a_conversation_offers_but_a_goodbye_or_a_typed_chat_does_not(env, monkeypatch):
    on(monkeypatch)

    async def go(text, channel="voice"):
        assert await say(text, channel=channel) is None            # the closing turn's own reply is never replaced
        await asyncio.gather(*list(ve._TASKS))

    run(go("thanks"))
    assert len(queued(env, "voice_enroll_offer")) == 1
    env["now"] = NOW + timedelta(days=2)
    run(go("thanks, bye"))
    run(go("goodnight zoe"))
    run(go("thank you", channel="chat"))
    assert len(queued(env, "voice_enroll_offer")) == 1


# ── the spoken yes / no ───────────────────────────────────────────────────────────────────────────────────────────────────

def _heard(panel=PANEL):
    """The daemon claims the queued announcement, plays it and ACKs: the server's played route runs, which is what arms the yes/no."""
    async def go():
        async with db_compat.get_compat_db() as db:
            items = await voice_announce.claim_announcements(db, panel_id=panel)
            assert await voice_announce.mark_played(db, announcement_id=items[0]["id"], panel_id=panel)
        return await ve.on_announcement_played(items[0]["id"])

    return run(go())


def _offer(env, monkeypatch):
    on(monkeypatch)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "offered"
    assert not ve.pending_for(ADULT)                    # queued is not heard
    assert _heard() is True
    assert ve.pending_for(ADULT)


def test_yes_starts_the_enrolment_on_the_panel(env, monkeypatch):
    _offer(env, monkeypatch)
    reply = run(say("yes"))
    assert reply == "Okay, let's do it. Stay by the panel."
    cmd = json.loads(queued(env, "voice_enroll")[0][1])
    assert cmd["kind"] == "voice_enroll" and cmd["user_id"] == ADULT and cmd["name"] == "Ann" and cmd["max_total_s"] == 90
    script = cmd["script"]
    assert "voice signature, never the recordings" in script["intro"] and script["intro"].startswith("Hi Ann.")
    assert "{n}" in script["prompt"] and "{phrase}" in script["prompt"] and len(script["phrases"]) == 3 and "stop" in script["cancel_words"]
    assert sql(env, "SELECT accepted_at FROM enrollment_offers WHERE user_id = ?", (ADULT,))[0][0]
    assert not ve.pending_for(ADULT)


@pytest.mark.parametrize("text", ["yes please", "yeah sure", "go ahead", "okay", "sounds good"])
def test_the_usual_yeses_start_it(env, monkeypatch, text):
    _offer(env, monkeypatch)
    assert run(say(text)).startswith("Okay, let's do it")


@pytest.mark.parametrize("text", ["no", "no thanks", "not now", "maybe later", "nope", "not today"])
def test_a_no_is_remembered_and_the_next_offer_waits_sixty_days(env, monkeypatch, text):
    _offer(env, monkeypatch)
    assert run(say(text)).startswith("No problem. I won't ask again for a while.")
    assert queued(env, "voice_enroll") == []
    until = es.parse(sql(env, "SELECT declined_until FROM enrollment_offers WHERE user_id = ?", (ADULT,))[0][0])
    assert (until - NOW).days in (59, 60)
    env["now"] = NOW + timedelta(days=30)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "declined"
    env["now"] = NOW + timedelta(days=61)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "offered"


@pytest.mark.parametrize("text", ["sure?", "what's the weather", "are you sure", "yes, and remind me to call mum", "sure is cold in here"])
def test_anything_that_is_not_a_clear_answer_is_not_a_yes_and_ends_the_offer(env, monkeypatch, text):
    _offer(env, monkeypatch)
    assert run(say(text)) is None
    assert queued(env, "voice_enroll") == [] and not ve.pending_for(ADULT)
    assert sql(env, "SELECT declined_at FROM enrollment_offers WHERE user_id = ?", (ADULT,))[0][0] is None     # ignoring is not declining


def test_an_unanswered_offer_expires(env, monkeypatch):
    _offer(env, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(ve.time, "monotonic", lambda: clock[0])
    ve.arm(ADULT, "en", PANEL)
    clock[0] += ve.PENDING_TTL_S + 1
    assert run(say("yes")) is None and queued(env, "voice_enroll") == []


def test_a_yes_from_a_voice_that_is_not_the_account_does_nothing(env, monkeypatch):
    _offer(env, monkeypatch)
    assert run(say("yes", verified=False)) is None
    assert queued(env, "voice_enroll") == []


def test_spanish_is_understood_from_its_file_alone(env, monkeypatch):
    _offer(env, monkeypatch)
    ve.arm(ADULT, "es", PANEL)
    assert run(say("claro")).startswith("Vale, vamos alla")
    ve.arm(ADULT, "es", PANEL)
    assert run(say("ahora no")).startswith("Sin problema")
    assert run(say("olvida mi voz")).startswith("No tengo tu voz")


# ── spoken requests ───────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["learn my voice", "hey zoe, learn my voice", "set up voice recognition", "can you learn my voice please",
                                  "I want to set up my voice"])
def test_learn_my_voice_starts_it_at_any_time_and_clears_a_decline(env, monkeypatch, text):
    on(monkeypatch)
    run(es.record_answer(ADULT, False, now=NOW))
    assert run(say(text)).startswith("Okay, let's do it")
    assert len(queued(env, "voice_enroll")) == 1
    assert not run(es.get_state(ADULT)).declined_now(NOW)


def test_learn_my_voice_is_refused_for_guests_children_and_unverified_voices_and_off_the_panel(env, monkeypatch):
    on(monkeypatch)
    assert "Sign in with your PIN" in run(say("learn my voice", "guest"))
    assert "grown-ups" in run(say("learn my voice", KID))
    assert "couldn't tell who was speaking" in run(say("learn my voice", verified=False))
    assert "panel speaker" in run(say("learn my voice", channel="chat", panel=""))
    assert queued(env) == []


def test_a_child_can_ask_when_the_owner_allows_it(env, monkeypatch):
    on(monkeypatch, kids=True)
    assert run(say("learn my voice", KID)).startswith("Okay")


def test_already_enrolled_is_said_not_redone(env, monkeypatch):
    on(monkeypatch)
    enrol(env, ADULT)
    assert "I already know your voice" in run(say("learn my voice"))
    assert queued(env) == []


def test_do_you_know_my_voice_is_answered_from_the_real_state(env, monkeypatch):
    on(monkeypatch)
    assert run(say("do you know my voice?")) == "I don't know your voice yet. Want me to learn it?"
    assert ve.pending_for(ADULT)
    assert run(say("yes")).startswith("Okay")                                   # the question itself arms the answer
    ve.reset()
    enrol(env, ADULT, samples=3)
    assert run(say("do you know my voice")) == "Yes, I know your voice. I have 3 samples of it."
    assert not ve.pending_for(ADULT)


def test_the_honest_answers_for_a_guest_a_child_a_decline_and_a_blind_spot(env, monkeypatch):
    on(monkeypatch)
    assert "can't tell" in run(say("do you know my voice", "guest"))
    assert "grown-up" in run(say("do you know my voice", KID))
    run(es.record_answer(ADULT, False, now=NOW))
    assert "You said no earlier" in run(say("do you know my voice"))
    sql(env, "DROP TABLE speaker_profiles")
    assert "can't check that right now" in run(say("do you know my voice", "bob"))


def test_shadow_answers_nothing_and_changes_nothing(env, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_ENROLL_OFFER", "shadow")
    enrol(env, ADULT)
    with caplog.at_level(logging.INFO, logger=ve.logger.name):
        assert run(say("forget my voice")) is None
    assert sql(env, "SELECT COUNT(*) FROM speaker_profiles") == [(1,)]
    assert any("would_answer=1" in r.getMessage() for r in caplog.records)


# ── forgetting reaches the box ────────────────────────────────────────────────────────────────────────────────────────────

def test_forget_my_voice_deletes_the_profile_and_tells_every_panel(env, monkeypatch):
    on(monkeypatch)
    enrol(env, ADULT)
    enrol(env, "bob")
    reply = run(say("forget my voice"))
    assert reply == "Done. I've deleted your voice signature, and I've told the panel to drop its copy."
    assert sql(env, "SELECT user_id FROM speaker_profiles") == [("bob",)]       # only hers
    rows = queued(env, "voice_profile_forget")
    assert sorted(r[2] for r in rows) == ["zoe-touch-hall", "zoe-touch-pi"]     # one per LIVE device token, none for the revoked one
    assert all(json.loads(r[1])["user_id"] == ADULT for r in rows)
    assert run(es.get_state(ADULT)).declined_now(NOW)                           # and Zoe does not offer again tomorrow


@pytest.mark.parametrize("text", ["forget my voice", "delete my voice profile", "erase my voice data", "stop recognising my voice", "remove my voiceprint"])
def test_the_forget_phrasings(env, monkeypatch, text):
    on(monkeypatch)
    enrol(env, ADULT)
    assert run(say(text)).startswith("Done. I've deleted")


def test_forget_is_refused_for_a_guest_and_for_a_voice_that_is_not_the_account(env, monkeypatch):
    on(monkeypatch)
    enrol(env, ADULT)
    assert "no voice of yours" in run(say("forget my voice", "guest"))
    assert "couldn't tell who was speaking" in run(say("forget my voice", verified=False))
    assert sql(env, "SELECT COUNT(*) FROM speaker_profiles") == [(1,)]
    assert queued(env, "voice_profile_forget") == []


def test_forget_with_nothing_enrolled_says_so_and_a_dry_run_changes_nothing(env, monkeypatch):
    on(monkeypatch)
    assert "nothing to delete" in run(say("forget my voice"))
    enrol(env, ADULT)
    assert run(say("forget my voice", writes=False)) is None
    assert sql(env, "SELECT COUNT(*) FROM speaker_profiles") == [(1,)]


def test_forgetting_is_allowed_for_a_child_even_with_the_kids_switch_off(env, monkeypatch):
    on(monkeypatch)
    enrol(env, KID)
    assert run(say("forget my voice", KID)).startswith("Done")


def test_the_settings_delete_route_pushes_the_purge_too(env, monkeypatch):
    import routers.voice_tts as voice_tts

    enrol(env, ADULT)
    caller = {"source": "session", "user_id": ADULT, "role": "user"}
    assert run(voice_tts.voice_profile_delete(f"p-{ADULT}", caller))["ok"]
    assert sorted(r[2] for r in queued(env, "voice_profile_forget")) == ["zoe-touch-hall", "zoe-touch-pi"]


# ── the announcement lane carries commands safely ─────────────────────────────────────────────────────────────────────────

def test_a_command_comes_back_as_a_command_with_no_text_to_read_aloud(env):
    async def go():
        async with db_compat.get_compat_db() as db:
            await voice_announce.enqueue_command(db, user_id=ADULT, panel_id="zoe-touch-pi", trigger_type="voice_enroll",
                                                 command={"name": "Ann", "script": {"intro": "Hi"}})
            await voice_announce.enqueue_announcement(db, user_id=ADULT, panel_id="zoe-touch-pi", message="Good morning",
                                                      trigger_type="morning_checkin")
            return await voice_announce.claim_announcements(db, panel_id="zoe-touch-pi")

    items = {i["trigger_type"]: i for i in run(go())}
    cmd = items["voice_enroll"]
    assert cmd["text"] == "" and cmd["command"]["user_id"] == ADULT and cmd["command"]["script"] == {"intro": "Hi"}
    assert items["morning_checkin"]["text"] == "Good morning" and "command" not in items["morning_checkin"]


def test_only_a_command_trigger_may_be_enqueued_as_a_command(env):
    async def go():
        async with db_compat.get_compat_db() as db:
            await voice_announce.enqueue_command(db, user_id=ADULT, trigger_type="morning_checkin", command={})

    with pytest.raises(ValueError):
        run(go())


def test_a_purge_row_is_claimed_only_by_the_panel_it_names(env):
    async def go():
        async with db_compat.get_compat_db() as db:
            await voice_announce.enqueue_command(db, user_id=ADULT, panel_id="zoe-touch-hall", trigger_type="voice_profile_forget", command={})
            other = await voice_announce.claim_announcements(db, panel_id="zoe-touch-pi")
            mine = await voice_announce.claim_announcements(db, panel_id="zoe-touch-hall")
            return other, mine

    other, mine = run(go())
    assert other == [] and len(mine) == 1 and mine[0]["command"]["kind"] == "voice_profile_forget"


def test_a_malformed_command_row_is_spent_without_a_command_or_text(env):
    sql(env, "INSERT INTO voice_announcements (id,user_id,panel_id,message,trigger_type,created_at,expires_at) VALUES "
             "('x',?,?,?,?,?,?)", (ADULT, "zoe-touch-pi", "{not json", "voice_enroll", "2026-10-12T00:00:00Z", "2099-01-01T00:00:00Z"))
    items = run(_claim_for(env, "zoe-touch-pi"))
    assert items[0]["text"] == "" and items[0]["command"] == {}


def _claim_for(env, panel):
    async def go():
        async with db_compat.get_compat_db() as db:
            return await voice_announce.claim_announcements(db, panel_id=panel)

    return go()


# ── the read API ──────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(env, monkeypatch):
    import routers.voice_tts as voice_tts
    from routers import enrollment

    app = FastAPI()
    app.include_router(enrollment.router)
    who = {"caller": {"source": "session", "user_id": ADULT, "role": "user"}}
    app.dependency_overrides[voice_tts._require_voice_auth] = lambda: who["caller"]
    c = TestClient(app)
    c.who = who
    return c


def test_a_member_sees_their_own_state_and_nobody_elses(client, env):
    enrol(env, ADULT, samples=2)
    body = client.get("/api/enrollment/state").json()
    assert body["voice"]["enrolled"] is True and body["voice"]["samples"] == 2 and body["audience"] == "adult"
    assert client.get("/api/enrollment/state", params={"user_id": "bob"}).status_code == 403
    assert client.get("/api/enrollment/household").status_code == 403


def test_an_admin_sees_the_household_without_synthetic_ids(client, env):
    sql(env, "INSERT INTO auth_users VALUES ('test-probe-9','x','user')")
    client.who["caller"] = {"source": "session", "user_id": "bob", "role": "admin"}
    enrol(env, ADULT)
    members = {m["user_id"]: m for m in client.get("/api/enrollment/household").json()["members"]}
    assert members[ADULT]["voice"]["enrolled"] is True and members[KID]["audience"] == "minor" and "test-probe-9" not in members
    assert client.get("/api/enrollment/state", params={"user_id": ADULT}).json()["user_id"] == ADULT


def test_a_device_token_owns_nothing_but_may_report_a_result(client, env):
    client.who["caller"] = {"source": "device", "user_id": "voice-daemon", "panel_id": "zoe-touch-pi"}
    assert client.get("/api/enrollment/state").status_code == 403
    assert client.get("/api/enrollment/household").status_code == 403
    _grant()
    assert client.post("/api/enrollment/result", json={"status": "done", "user_id": ADULT, "samples": 3}).json() == {"ok": True}
    assert not run(es.get_state(ADULT)).declined_now(NOW)                       # only "cancelled" changes anything
    _grant()
    assert client.post("/api/enrollment/result", json={"status": "cancelled", "user_id": ADULT, "reason": "said_stop"}).status_code == 200
    assert run(es.get_state(ADULT)).declined_now(NOW)                           # the person said stop: no re-offer tomorrow
    assert client.post("/api/enrollment/result", json={"status": "weird", "user_id": ADULT}).status_code == 400


def _grant(member=ADULT, panel="zoe-touch-pi"):
    import biometric_scope

    biometric_scope.grant_enrolment(panel, member, ttl_s=300)


def test_a_result_without_a_grant_records_nothing_and_each_result_closes_the_grant(client, env):
    """2026-10-19: a copied panel token cannot put a member into the 60-day quiet period. Only the member Zoe granted at
    THAT panel can be recorded as having said stop, and the run's result closes the grant (a second report is ignored)."""
    import biometric_scope

    biometric_scope.reset_enrolment_grants()
    client.who["caller"] = {"source": "device", "user_id": "voice-daemon", "panel_id": "zoe-touch-pi"}
    stop = {"status": "cancelled", "user_id": ADULT, "reason": "said_stop"}
    assert client.post("/api/enrollment/result", json=stop).status_code == 200
    assert not run(es.get_state(ADULT)).declined_now(NOW)                       # no grant: nothing recorded
    _grant(panel="zoe-touch-hall")                                              # a grant at ANOTHER panel does not count
    assert client.post("/api/enrollment/result", json=stop).status_code == 200
    assert not run(es.get_state(ADULT)).declined_now(NOW)
    _grant(member="someone-else")                                               # nor a grant for another member
    assert client.post("/api/enrollment/result", json=stop).status_code == 200
    assert not run(es.get_state(ADULT)).declined_now(NOW)
    assert biometric_scope.granted_member("zoe-touch-pi") is None               # ...and that report closed the grant
    _grant()
    assert client.post("/api/enrollment/result", json=stop).status_code == 200
    assert run(es.get_state(ADULT)).declined_now(NOW)                           # positive control: the granted member's stop
    assert biometric_scope.granted_member("zoe-touch-pi") is None


def test_start_enrollment_grants_only_that_member_at_that_panel(env, monkeypatch):
    """The enrol command's server-side half: queuing it for ADULT at zoe-touch-pi opens a grant for exactly that pair."""
    import biometric_scope

    biometric_scope.reset_enrolment_grants()
    on(monkeypatch)
    assert run(ve.start_enrollment(ADULT, "zoe-touch-pi", "en")) is True
    assert biometric_scope.granted_member("zoe-touch-pi") == ADULT
    assert biometric_scope.granted_member("zoe-touch-hall") is None
    assert biometric_scope.granted_member("zoe-touch-pi", "face") is None      # a voice command never grants a face
    assert run(ve.start_enrollment(ADULT, "zoe-touch-old", "en")) is False      # nothing queued -> no grant
    assert biometric_scope.granted_member("zoe-touch-old") is None


def test_a_session_cannot_post_a_result(client):
    assert client.post("/api/enrollment/result", json={"status": "cancelled", "user_id": ADULT}).status_code == 403


# ── review round 1 (Codex): scoping, heard-before-armed, rollback, shutdown is not a no ─────────────────────────────────────

def test_the_enrol_command_is_claimable_only_by_the_panel_it_targeted(env, monkeypatch):
    on(monkeypatch)
    assert "voice_enroll" in voice_announce.PANEL_SCOPED_TRIGGERS
    assert run(ve.start_enrollment(ADULT, "zoe-touch-pi", "en")) is True
    other = run(_claim_for(env, "zoe-touch-hall"))                               # another panel's daemon polls first
    assert other == []                                                           # ... and gets nothing: no name spoken, nobody enrolled
    mine = run(_claim_for(env, "zoe-touch-pi"))
    assert len(mine) == 1 and mine[0]["command"]["kind"] == "voice_enroll" and mine[0]["command"]["name"] == "Ann"


def test_two_members_at_two_panels_each_get_their_own_panel_only(env, monkeypatch):
    on(monkeypatch)
    assert run(ve.start_enrollment(ADULT, "zoe-touch-pi", "en"))
    assert run(ve.start_enrollment("bob", "zoe-touch-hall", "en"))
    pi, hall = run(_claim_for(env, "zoe-touch-pi")), run(_claim_for(env, "zoe-touch-hall"))
    assert [i["command"]["user_id"] for i in pi] == [ADULT] and [i["command"]["user_id"] for i in hall] == ["bob"]


@pytest.mark.parametrize("panel", ["", "browser-panel-xyz", "zoe-touch-old"])
def test_no_command_is_queued_for_a_panel_that_holds_no_live_device_token(env, monkeypatch, panel):
    on(monkeypatch)
    assert run(ve.start_enrollment(ADULT, panel, "en")) is False
    assert queued(env, "voice_enroll") == []


def test_a_yes_from_a_panel_without_a_device_token_says_it_could_not_start(env, monkeypatch):
    on(monkeypatch)
    assert "I couldn't reach the panel speaker" in run(say("learn my voice", panel="browser-panel-xyz"))
    assert queued(env, "voice_enroll") == []


def test_an_unrelated_yes_is_not_consent_to_an_offer_that_was_only_queued(env, monkeypatch):
    on(monkeypatch)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "offered"           # queued, not yet played
    assert run(say("yes")) is None                                                # "yes" to something else entirely
    assert queued(env, "voice_enroll") == []
    assert sql(env, "SELECT accepted_at FROM enrollment_offers WHERE user_id = ?", (ADULT,))[0][0] is None


def test_the_offer_becomes_answerable_only_when_the_daemon_reports_it_played(env, monkeypatch):
    on(monkeypatch)
    run(ve.maybe_offer(ADULT, "pin_stepup", PANEL))
    assert not ve.pending_for(ADULT)
    assert _heard() is True and ve.pending_for(ADULT)
    assert run(say("yes")).startswith("Okay, let's do it")


def test_the_played_route_arms_the_yes_for_an_offer_but_not_for_other_announcements(env, monkeypatch):
    import routers.voice_tts as voice_tts

    on(monkeypatch)
    run(ve.maybe_offer(ADULT, "pin_stepup", PANEL))

    async def play(trigger_filter=None):
        async with db_compat.get_compat_db() as db:
            items = await voice_announce.claim_announcements(db, panel_id=PANEL)
            for item in items:
                out = await voice_tts.voice_announcement_played(item["id"], {"source": "device", "panel_id": PANEL}, db)
                assert out["updated"] is True
            return items

    run(play())
    assert ve.pending_for(ADULT)                                                 # through the real route, not the helper
    ve.reset()

    async def brief():
        async with db_compat.get_compat_db() as db:
            await voice_announce.enqueue_announcement(db, user_id=ADULT, panel_id=PANEL, message="Good morning", trigger_type="morning_checkin")
        return await play()

    run(brief())
    assert not ve.pending_for(ADULT)                                             # a played brief asks no question


def test_a_played_ack_for_an_offer_is_ignored_while_the_flag_is_not_on(env, monkeypatch):
    on(monkeypatch)
    run(ve.maybe_offer(ADULT, "pin_stepup", PANEL))
    monkeypatch.setenv("ZOE_ENROLL_OFFER", "shadow")
    assert _heard() is False and not ve.pending_for(ADULT)


def test_the_spanish_offer_arms_the_spanish_answer(env, monkeypatch):
    on(monkeypatch)
    run(ve.maybe_offer(ADULT, "pin_stepup", PANEL, lang="es"))
    assert _heard() is True
    assert run(say("claro")).startswith("Vale, vamos alla")


def test_a_failed_enqueue_gives_the_offer_slot_back(env, monkeypatch):
    on(monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError("queue down")

    monkeypatch.setattr(voice_announce, "enqueue_announcement", boom)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "not_queued"
    st = run(es.get_state(ADULT))
    assert (st.last_offered_at, st.offer_count, st.pending_since, st.ignored_count) == (None, 0, None, 0)
    assert not ve.pending_for(ADULT)


def test_failures_are_not_counted_as_ignored_offers(env, monkeypatch):
    """Three failed enqueues must not start the 60-day quiet period, and the next real offer goes out the same day."""
    on(monkeypatch)
    real = voice_announce.enqueue_announcement
    calls = {"n": 0}

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("queue down")
        return await real(*a, **k)

    monkeypatch.setattr(voice_announce, "enqueue_announcement", flaky)
    for _ in range(3):
        assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "not_queued"
    st = run(es.get_state(ADULT))
    assert not st.declined_now(NOW) and st.ignored_count == 0
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "offered"           # not blocked by a 24 h gap nor a quiet period
    assert len(queued(env, "voice_enroll_offer")) == 1


def test_a_failed_enqueue_after_an_earlier_unanswered_offer_restores_that_offers_state(env, monkeypatch):
    on(monkeypatch)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "offered"
    before = run(es.get_state(ADULT))

    async def boom(*a, **k):
        raise RuntimeError("queue down")

    monkeypatch.setattr(voice_announce, "enqueue_announcement", boom)
    env["now"] = NOW + timedelta(hours=30)
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL)) == "not_queued"
    after = run(es.get_state(ADULT))
    assert (after.last_offered_at, after.offer_count, after.pending_since, after.ignored_count) == (
        before.last_offered_at, before.offer_count, before.pending_since, before.ignored_count)


def test_release_never_undoes_a_later_claim(env):
    st0 = run(es.get_state(ADULT))
    assert run(es.claim_offer(ADULT, st0, now=NOW))
    st1 = run(es.get_state(ADULT))
    assert run(es.claim_offer(ADULT, st1, now=NOW + timedelta(days=2)))           # someone else's later offer
    assert run(es.release_offer(ADULT, st0, now=NOW)) is False                    # the first claim's rollback must not touch it
    assert run(es.get_state(ADULT)).offer_count == 2


def test_only_the_person_stopping_it_is_a_decline_a_daemon_shutdown_is_not(client, env):
    client.who["caller"] = {"source": "device", "user_id": "voice-daemon", "panel_id": "zoe-touch-pi"}

    def post(**kw):
        _grant()                                                                   # every run is its own granted command
        return client.post("/api/enrollment/result", json={"status": "cancelled", "user_id": ADULT, **kw})

    for kw in ({"reason": "shutdown"}, {"reason": "stopped"}, {}, {"reason": "crashed"}):
        assert post(**kw).status_code == 200
        assert not run(es.get_state(ADULT)).declined_now(NOW), kw                  # a restart is not a "no"
    assert post(reason="tapped_stop").status_code == 200
    assert run(es.get_state(ADULT)).declined_now(NOW)
    run(es.record_answer(ADULT, True, now=NOW))                                    # reset
    assert post(reason="said_stop").status_code == 200 and run(es.get_state(ADULT)).declined_now(NOW)


def test_a_non_cancel_status_with_a_stop_reason_is_not_a_decline(client, env):
    client.who["caller"] = {"source": "device", "user_id": "voice-daemon", "panel_id": "zoe-touch-pi"}
    client.post("/api/enrollment/result", json={"status": "failed", "user_id": ADULT, "reason": "said_stop"})
    assert not run(es.get_state(ADULT)).declined_now(NOW)


# ── the self-model knows the gap ─────────────────────────────────────────────────────────────────────────────────────────

def _know_me(monkeypatch, voice):
    import self_model as sm

    async def enrolment(uid):
        return voice, False

    async def own_name(uid):
        return "Ann"

    monkeypatch.setattr(sm, "_enrolment", enrolment)
    monkeypatch.setattr(sm, "_own_name", own_name)
    return run(sm.answer(sm.Question("know_me", "en"), ADULT, channel="voice", live=sm.Live()))


def test_do_you_know_who_i_am_admits_the_missing_voice_only_when_the_offer_is_on(env, monkeypatch):
    on(monkeypatch)
    assert "I don't know your voice yet. Say learn my voice if you'd like me to." in _know_me(monkeypatch, False)
    assert "I know your voice too." in _know_me(monkeypatch, True)
    monkeypatch.setenv("ZOE_ENROLL_OFFER", "off")
    said = _know_me(monkeypatch, False)
    assert "voice" not in said.lower()                                  # a dark feature is not advertised
    monkeypatch.setenv("ZOE_ENROLL_OFFER", "on")
    assert "voice" not in _know_me(monkeypatch, None).lower()           # an unread state is not "you are not enrolled"


def test_every_reply_leaves_its_provenance_note(env, monkeypatch):
    on(monkeypatch)
    run(say("do you know my voice"))
    assert env["noted"] == [(ADULT, ve.TIER)]


# ── language files ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_language_files_have_the_same_shape():
    en, es_ = ve.lex("en"), ve.lex("es")
    assert en and es_
    for section in ("cues", "say", "script"):
        assert set(en[section]) == set(es_[section]), section
    assert len(en["phrases"]) == len(es_["phrases"]) == 3
    assert all("signature" in en["say"][k] or "firma" in es_["say"][k] for k in ("offer_pin", "offer_close"))


def test_a_language_with_no_file_contributes_nothing(env, monkeypatch):
    on(monkeypatch)
    assert ve.lex("fr") == {}
    assert ve.classify("apprends ma voix") is None
    assert run(ve.maybe_offer(ADULT, "pin_stepup", PANEL, lang="fr")) in ("offered",)       # falls back to English rather than staying silent


def test_the_code_holds_no_english_sentences():
    import ast

    literals = []
    for fname in ("voice_enrollment.py", "enrollment_state.py"):
        tree = ast.parse((SVC / fname).read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                      and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        literals += [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]
    joined = "\n".join(literals)
    for sentence in ("Want to?", "I don't know your voice", "Okay, let's do it", "No problem.", "voice signature"):
        assert sentence not in joined, sentence
