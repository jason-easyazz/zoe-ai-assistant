"""Restraint in code (``restraint.py``, ``ZOE_RESTRAINT`` off | shadow | enforce) — register item BP1.

Fixtures only: synthetic names, no household text. The DB edge is a real SQLite file built by the real
migrations (0033 candidates, 0036 ledger, 0040 restraint) behind db_pool's own cursor types, so the SQL
runs for real. Every behaviour has a RED-WHEN-REMOVED twin: the scenario helpers return True when
something LEAKED, the test asserts False normally and True with the rule neutered (``decide`` forced to
allow, the mute check deleted, ...), so a green run can never mean "the check was never wired".
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # every edge is stubbed; slim-dep modules only

import contextlib
import importlib.util
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_compat
import restraint
from db_pool import _Cursor, _ExecResult
from memory_service import MemoryRef
from proactive import selector as sel

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
MEMBER = "member-a"
DENTIST = "I've got the dentist on Friday for a cracked molar and I'm really nervous about it"
INTERVIEW = "User is anxious about a job interview at the aquarium on Friday"
PARCEL = "parcel pickup at the depot on Friday"


# ── the real migrations over SQLite ─────────────────────────────────────────
def _mig(name: str):
    spec = importlib.util.spec_from_file_location(f"mig_{name}", SVC / f"alembic/versions/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apply(engine, *names, fn="upgrade"):
    for name in names:
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            getattr(_mig(name), fn)()


MIGRATIONS = ("0033_proactive_candidates", "0036_proactive_deliveries", "0040_restraint")


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)

    def execute(self, sql, params=()):
        async def _run():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


class _Mem:
    def __init__(self):
        self.refs = []

    async def load_recent_for_prompt(self, user_id, **kw):
        return list(self.refs)


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    _apply(engine, *MIGRATIONS)
    db = _Sqlite(path)
    for ddl in (
        "CREATE TABLE open_loops (id INTEGER PRIMARY KEY, user_id TEXT, loop_text TEXT, "
        "follow_up_hint TEXT, emotional_weight INTEGER, created_at TEXT, follow_up_after TEXT, "
        "resolved BOOLEAN DEFAULT FALSE, resolved_at TEXT)",
        "CREATE TABLE events (id TEXT, user_id TEXT, title TEXT, start_date TEXT, start_time TEXT, "
        "deleted INTEGER DEFAULT 0)",
        "CREATE TABLE proactive_pending (id TEXT, trigger_type TEXT, item_id TEXT)",
        "CREATE TABLE people (id TEXT, user_id TEXT, name TEXT, deleted INTEGER DEFAULT 0)",
    ):
        db.conn.execute(ddl)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    import memory_service

    mem = _Mem()
    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: mem)
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    for key in ("ZOE_SEAM_RECALL_INJECT", "ZOE_SEAM_OFFER_INJECT", "ZOE_BRIEF_ON_FIRST_TURN",
                "ZOE_SYNTHETIC_USER_ALLOWLIST", "ZOE_PROACTIVE_RAISE_GAP_S", "ZOE_PROACTIVE_RAISE_PER_DAY",
                "ZOE_LOOP_LIFECYCLE"):
        monkeypatch.delenv(key, raising=False)
    sel._reset_state()
    restraint._reset_state()
    state = {"now": NOW, "db": db, "mem": mem, "engine": engine}
    monkeypatch.setattr(sel, "_now", lambda: state["now"])
    yield state
    sel._reset_state()
    restraint._reset_state()


def _loop(db, text, *, weight=4, age_h=10, due_h=1, user=MEMBER):
    created = (NOW - timedelta(hours=age_h)).strftime("%Y-%m-%d %H:%M:%S")
    due = None if due_h is None else (NOW + timedelta(hours=due_h)).strftime("%Y-%m-%d %H:%M:%S")
    db.conn.execute("INSERT INTO open_loops (user_id, loop_text, follow_up_hint, emotional_weight, "
                    "created_at, follow_up_after) VALUES (?, ?, '', ?, ?, ?)",
                    (user, text, weight, created, due))


async def _nightly(env, user=MEMBER):
    return await sel.select_for_user(user, now=env["now"])


GREET = "Hi Zoe"                # a bare greeting: not a pull
OPEN = "Hi Zoe, how are things?"  # an open question: a pull


# ═════════════════════════════════════════════════════════════════════════════
# the flag
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("raw, want", [
    (None, "shadow"), ("", "shadow"), ("shadow", "shadow"), ("SHADOW", "shadow"), ("enfroce", "shadow"),
    ("off", "off"), ("0", "off"), ("false", "off"), ("enforce", "enforce"), ("ENFORCE", "enforce"),
    ("1", "enforce"), ("on", "enforce"),
])
def test_mode_default_is_shadow_and_a_typo_never_enforces(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv("ZOE_RESTRAINT", raising=False)
    else:
        monkeypatch.setenv("ZOE_RESTRAINT", raw)
    assert restraint.mode() == want


# ═════════════════════════════════════════════════════════════════════════════
# 1. the sensitivity class
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text, want", [
    (DENTIST, {"health", "affect"}),
    ("worry about the dentist on Friday for a wisdom tooth", {"health"}),
    ("migraines most afternoons, they stopped with the new glasses", {"health"}),
    ("worried about the car loan repayments this month", {"money", "affect"}),
    ("the mortgage is due and we are short this month", {"money"}),
    ("missing my grandfather since the funeral last week", {"grief", "other_member"}),
    ("Dana's knee surgery is on Monday", {"health", "other_member"}),
    ("I had a big fight with my sister last night", {"family_conflict", "other_member"}),
    ("we are getting a divorce", {"family_conflict"}),
    (INTERVIEW, {"affect"}),
    ("my mum Ingrid is recovering from a hip replacement", {"health", "other_member"}),
    # plain: nothing here waits for a pull
    (PARCEL, set()),
    ("I'm pescatarian, so fish is fine but I don't eat any meat", set()),
    ("the Kestrel migration goes live on Thursday", set()),
    ("physio appointment".replace("physio", "parcel"), set()),
    ("Today's plan: the kitchen quote is overdue", set()),
])
def test_the_classifier_table(text, want):
    assert set(restraint.classify(text)) == want


# A held-out set written BEFORE the lexicon was widened (32 sensitive sentences in the six classes, 18 plain ones).
# Measured on the first lexicon: class-exact recall 20 / 32, any-class recall 22 / 32, plain false positives 0 / 18.
# After widening the vocabulary (general words, not these sentences) the numbers below are the floor. The honest
# reading: the English word list is the FALLBACK of the union rule; structured signals carry the language-free
# cases and a paraphrase the list has never seen is missed (see docs/knowledge/restraint.md, limits).
HELD_OUT_SENSITIVE = [
    ("health", "User has been prescribed antibiotics for a chest infection"),
    ("health", "I need to see my GP about the lump on my neck"),
    ("health", "The physio says my shoulder needs another six weeks"),
    ("health", "I've been having panic attacks at night"),
    ("health", "Waiting on the blood test results from the lab"),
    ("health", "User is pregnant and due in March"),
    ("health", "Got my wisdom teeth out on Tuesday and the jaw is swollen"),
    ("health", "Scans show the cyst has shrunk"),
    ("money", "We are behind on the rent and the landlord has sent a notice"),
    ("money", "User is worried about being made redundant next month"),
    ("money", "I owe my brother four thousand dollars"),
    ("money", "The credit card bill came to nearly three grand"),
    ("money", "We can't afford the school fees this term"),
    ("money", "My super balance is a lot lower than I hoped"),
    ("grief", "My dad passed away in June"),
    ("grief", "It's the anniversary of Gran's death this week"),
    ("grief", "We scattered her ashes at the beach"),
    ("grief", "The dog died last night and the kids are heartbroken"),
    ("grief", "I still cry when I see his old jumper"),
    ("family_conflict", "Mum and I haven't spoken since the wedding"),
    ("family_conflict", "My brother and I had a huge blow-up about the will"),
    ("family_conflict", "She says she wants a separation"),
    ("family_conflict", "I'm angry at my sister for what she said to the kids"),
    ("family_conflict", "The in-laws keep interfering and it's causing fights"),
    ("other_member", "Priya's biopsy results are due on Friday"),
    ("other_member", "Teodor got suspended from school"),
    ("other_member", "My husband has been out of work since March"),
    ("other_member", "Anika is moving in with her boyfriend"),
    ("affect", "User is feeling really low about everything lately"),
    ("affect", "I'm dreading the performance review"),
    ("affect", "User felt ashamed after the meeting"),
    ("affect", "I've been so anxious about the move"),
]
HELD_OUT_PLAIN = [
    "User is pescatarian and does not eat meat", "The Kestrel migration goes live on the 14th of November",
    "User walks the dog along the river at 6am", "User is training for the City to Surf 12k in August",
    "User prefers short, direct answers", "Pick up the parcel from the depot on Friday",
    "User's favourite colour is green", "User works night shifts and sleeps during the day",
    "The kitchen renovation quote is overdue", "User plays the cello on Tuesday evenings",
    "Book the car service for next week", "User lives in Fremantle",
    "Dinner with the neighbours at 7pm on Saturday", "User wants to learn Portuguese before the trip to Lisbon",
    "The wifi password is on the fridge", "User likes tea with no sugar", "Remember to water the tomatoes",
    "User drives a blue hatchback", "I'm low on milk", "feeling down for tacos", "I just scanned the QR code",
]


def test_the_lexicon_fallback_on_a_held_out_set_is_measured_not_assumed():
    exact = sum(1 for want, t in HELD_OUT_SENSITIVE if want in restraint.classify(t))
    anyc = sum(1 for _w, t in HELD_OUT_SENSITIVE if restraint.classify(t))
    false_pos = [t for t in HELD_OUT_PLAIN if restraint.classify(t)]
    assert false_pos == []                       # restraint must not over-withhold ordinary life
    assert exact >= 28 and anyc >= 29, (exact, anyc)   # 20 / 22 of 32 before the vocabulary was widened
    # ... and the misses that remain are the honest limit: no list names a person it was never told about
    assert "other_member" not in restraint.classify("Teodor got suspended from school")
    assert "other_member" in restraint.classify("Teodor got suspended from school", names=["Teodor"])


def test_classes_come_back_in_the_declared_order():
    assert restraint.classify(DENTIST + " and my mum's bills") == tuple(
        c for c in restraint.CLASSES if c in restraint.classify(DENTIST + " and my mum's bills"))


def test_structured_signals_decide_without_the_words():
    """The union rule: a row in ANY language is classed by its type, entity, tags and captured affect;
    the English word list is only the fallback."""
    assert restraint.classify_full("Jahresgespraech mit Hannah", memory_type="person") == (
        ("other_member",), ("other_member:entity",))
    assert restraint.classify_full("rendez-vous", entity_type="person")[1] == ("other_member:entity",)
    assert restraint.classify_full("Es war ein langer Tag", memory_type="emotional_moment") == (
        ("affect",), ("affect:type",))
    assert restraint.classify_full("tengo cita", affect="nervous")[1] == ("affect:affect",)
    assert restraint.classify_full("Zahnarzt am Freitag", tags="plan,dental") == (("health",), ("health:tag",))
    assert restraint.classify_full("Kredit", tags="finance")[1] == ("money:tag",)
    assert restraint.classify_full("Zahnarzt am Freitag") == ((), ())  # no structure, no English: unclassed
    assert restraint.classify_full("some text", kind="emotional")[1] == ("affect:kind",)
    assert restraint.classify_full("a note about Hannah", names=["Hannah"])[1] == ("other_member:name",)


def test_signals_name_the_decider_so_an_audit_can_tell_structure_from_a_word_list():
    classes, signals = restraint.classify_full(DENTIST, memory_type="emotional_moment")
    assert dict(s.split(":") for s in signals) == {"health": "lexicon", "affect": "type"}


def test_stamp_writes_the_class_and_row_classes_trusts_it_only_while_it_still_matches():
    md = {"memory_type": "fact", "tags": ""}
    restraint.stamp(md, DENTIST)
    assert md["sensitivity"] == "health,affect" and md["sensitivity_v"] == restraint.VERSION
    assert restraint.row_classes(md, DENTIST) == ("health", "affect")
    # the stored label is TRUSTED while version and hash match (proved by planting a label no text implies)
    planted = dict(md, sensitivity="money")
    assert restraint.row_classes(planted, DENTIST) == ("money",)
    # an edited row: the label belongs to other words -> ignored (not deleted) and recomputed
    assert restraint.row_classes(planted, "parcel pickup on Friday") == ()
    assert planted["sensitivity"] == "money"  # invalidate, never delete
    # another classifier version: invalid
    assert restraint.row_classes(dict(planted, sensitivity_v=restraint.VERSION + 1), DENTIST) != ("money",)


def test_stamp_is_a_noop_when_off_and_never_raises(monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    md = {}
    restraint.stamp(md, DENTIST)
    assert md == {}
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    restraint.stamp(None, DENTIST)  # type: ignore[arg-type]  # swallowed


# ═════════════════════════════════════════════════════════════════════════════
# the pull, the rule
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("message, want", [
    ("what's up", True), ("Hey Zoe, what's new?", True), ("Hi Zoe, how are things?", True),
    ("Morning Zoe, how's it going?", True), ("anything I need to know?", True),
    ("what do you know about me", True), ("catch me up", True),
    ("Hi Zoe", False), ("good morning", False), ("morning zoe", False), ("how are you", False),
    ("what's on today", False), ("set a timer for ten minutes", False), ("", False),
    ("what's up with the dentist bill", False),
])
def test_an_open_question_is_a_pull_and_a_greeting_is_not(message, want):
    assert restraint.is_pull(message) is want


def _turn(message, verdict=None, mood=False):
    return restraint.make_turn(message, verdict=verdict, mood=mood)


@pytest.mark.parametrize("surface, message, verdict, classes, want", [
    # unprompted surfaces: a sensitive thread needs an open question
    ("brief", GREET, None, ("health",), False),
    ("brief", "what's up", None, ("health",), True),
    ("raise_greeting", GREET, None, ("money",), False),
    ("raise_greeting", OPEN, None, ("money",), True),
    ("raise_cue", "the dentist is on Friday", None, ("health",), True),  # the owner's words ARE the pull
    # a plain thread is never held back
    ("brief", GREET, None, (), True),
    ("raise_greeting", GREET, None, (), True),
    # the guest rule wins over every pull
    ("brief", "what's up", False, ("health",), False),
    ("raise_greeting", OPEN, False, ("grief",), False),
    ("raise_cue", "the dentist is on Friday", False, ("health",), False),
    ("packet", "when is my dentist appointment", False, ("health",), False),
    ("packet", "what's up", False, ("affect",), False),
    ("brief", GREET, False, (), True),  # a plain thread is fine in front of a guest
    # the packet: topic, class, mood-for-affect, open question
    ("packet", "set a timer for ten minutes", None, ("health",), False),
    ("packet", "when is my dentist appointment", None, ("health",), True),
    ("packet", "what did the doctor say", None, ("health",), True),      # the owner named the class
    ("packet", "I've been feeling a bit on edge today", None, ("affect",), True),  # its own words name the feeling
    ("packet", "had such a rough day", None, ("affect",), False),                  # no feeling word, no mood flag
])
def test_decide_matrix(surface, message, verdict, classes, want):
    text = "dentist on Friday cracked molar"
    assert restraint.decide(text, classes, _turn(message, verdict), surface=surface).allow is want


def test_a_mood_statement_pulls_feelings_and_only_feelings():
    assert not restraint.decide(INTERVIEW, ("affect",), _turn("had such a rough day"), surface="packet").allow
    mood = _turn("had such a rough day", mood=True)  # the continuity trigger says it is a mood statement
    assert restraint.decide(INTERVIEW, ("affect",), mood, surface="packet").allow
    assert not restraint.decide("Dana's knee surgery on Monday", ("health", "other_member"), mood,
                                surface="packet").allow
    assert not restraint.decide(DENTIST, ("health", "affect"), _turn("lights please"), surface="packet").allow


def test_decision_reasons_are_named():
    d = restraint.decide(DENTIST, ("health",), _turn(GREET), surface="brief")
    assert (d.allow, d.reason, d.classes) == (False, "sensitive", ("health",))
    assert restraint.decide(DENTIST, ("health",), _turn("what's up", False), surface="brief").reason == "guest"
    m = restraint.Mute("m1", frozenset({"dentist"}))
    assert restraint.decide(DENTIST, (), _turn("what's up"), [m], surface="raise_greeting").reason == "muted"


# ═════════════════════════════════════════════════════════════════════════════
# 3. the spoken mute: parsing
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("message, stems, deictic", [
    ("Don't mention that again", (), True),
    ("stop bringing that up", (), True),
    ("Stop bringing that up!", (), True),
    ("please don't bring up the dentist anymore", ("dentist",), False),
    ("stop asking me about the dentist", ("dentist",), False),
    ("Could you not bring that up again?", (), True),
    ("can you stop mentioning the interview", ("interview",), False),
    ("I don't want to talk about it anymore", (), True),
    ("I don't want to talk about my knee any more", ("knee",), False),
    ("that's enough about the interview", ("interview",), False),
    ("never mention Dana's surgery again", ("dana", "surgery"), False),
    ("no more mentioning the interview", ("interview",), False),
    ("Hey Zoe, please stop bringing up my knee", ("knee",), False),
    ("don't keep asking about my knee", ("knee",), False),
    ("I'd like you to stop mentioning the interview", ("interview",), False),
    ("leave the dentist alone", ("dentist",), False),
    ("lets not talk about the dentist", ("dentist",), False),
])
def test_a_spoken_mute_is_recognised(message, stems, deictic):
    u = restraint.parse_utterance(message)
    assert u is not None and u.kind == "mute", message
    assert u.stems == stems and u.deictic is deictic


@pytest.mark.parametrize("message", ["leave it", "Leave it.", "leave that alone", "drop it", "leave it alone"])
def test_a_bare_leave_it_is_a_mute_only_when_something_was_just_raised(message):
    u = restraint.parse_utterance(message)
    assert u.kind == "mute" and u.deictic and u.needs_referent


@pytest.mark.parametrize("message, stems, deictic", [
    ("you can mention the dentist again", ("dentist",), False),
    ("Ok, you can mention it again", (), True),
    ("feel free to bring that up again", (), True),
    ("it's okay to talk about the dentist again", ("dentist",), False),
    ("you can bring the dentist up again", ("dentist",), False),
])
def test_a_release_is_recognised(message, stems, deictic):
    u = restraint.parse_utterance(message)
    assert u is not None and u.kind == "release" and u.stems == stems and u.deictic is deictic


@pytest.mark.parametrize("message", [
    "leave it on", "leave it open", "leave the lights on", "stop", "stop talking", "turn the lights off",
    "don't forget to mention the dentist", "never mind", "what's the weather", "remind me about the dentist",
    "mention the dentist to Sam", "I can't bring that up with her", "bring up the lights", "",
    "stop the music", "don't stop", "leave me alone",
    "please stop bringing that up " + "and " * 60,  # a long ramble is not a command
])
def test_ordinary_speech_is_never_a_mute(message):
    assert restraint.parse_utterance(message) is None


def test_the_words_are_read_and_never_returned():
    u = restraint.parse_utterance("please don't bring up my secret divorce lawyer again")
    assert "lawyer" in u.stems and all(isinstance(s, str) for s in u.stems)
    assert not hasattr(u, "message") and not hasattr(u, "text")


# ═════════════════════════════════════════════════════════════════════════════
# 3. the mute store, end to end
# ═════════════════════════════════════════════════════════════════════════════
def _mutes(env, where=""):
    return env["db"].rows("SELECT user_id, topic_key, thread_ref, scope, source, session_id, turn_key, phrase, "
                          f"status, released_at, released_turn_key FROM restraint_mutes {where}")


async def test_a_topic_mute_is_recorded_with_provenance_and_without_the_words(env):
    msg = "please don't bring up the dentist anymore"
    ack = await restraint.handle_turn(msg, MEMBER, "sess-1")
    assert ack == restraint.ACK_MUTE
    (row,) = _mutes(env)
    user, topic, thread, scope, source, sid, key, phrase, status, rel, relkey = row
    assert (user, topic, thread, scope, source, sid, phrase, status) == (
        MEMBER, "dentist", None, "topic", "spoken", "sess-1", "neg", "active")
    assert key == restraint.turn_key(msg) and rel is None
    # nothing the owner SAID is stored: no column holds a word of the utterance
    dump = " ".join(str(c) for r in env["db"].rows("SELECT * FROM restraint_mutes") for c in r)
    for word in ("please", "bring", "anymore", "don't"):
        assert word not in dump


async def test_shadow_records_the_mute_but_says_nothing_and_off_records_nothing(env, monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
    assert await restraint.handle_turn("stop mentioning the dentist", MEMBER, "s1") == ""
    assert len(_mutes(env)) == 1  # the data exists the day the flag flips
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    assert await restraint.handle_turn("stop mentioning the interview", MEMBER, "s1") == ""
    assert len(_mutes(env)) == 1


async def test_a_second_identical_mute_is_one_row_and_a_non_mute_is_none(env):
    await restraint.handle_turn("stop mentioning the dentist", MEMBER, "s1")
    await restraint.handle_turn("never bring up the dentist again", MEMBER, "s2")
    assert len(_mutes(env)) == 1
    assert await restraint.handle_turn("what's the weather", MEMBER, "s2") == ""
    assert len(_mutes(env)) == 1


async def test_guests_and_empty_users_have_no_standing_to_mute(env):
    for who in ("guest", "anonymous", "voice-guest", "voice-daemon", ""):
        assert await restraint.handle_turn("stop mentioning the dentist", who, "s1") == ""
    assert _mutes(env) == []


async def test_that_means_the_thread_that_was_just_raised(env):
    _loop(env["db"], DENTIST)
    await _nightly(env)
    raised = await sel.prepare(OPEN, MEMBER, "sess-1")
    assert raised is not None and await sel.settle(raised, produced=True)
    assert await restraint.handle_turn("don't mention that again", MEMBER, "sess-1") == restraint.ACK_MUTE
    (row,) = _mutes(env)
    assert row[2] == "open_loops:1" and row[3] == "thread"
    assert {"dentist", "molar"} <= set(row[1].split())


async def test_leave_it_with_nothing_raised_is_not_hijacked(env):
    assert await restraint.handle_turn("leave it", MEMBER, "s1") == ""
    assert _mutes(env) == []
    # a spoken "that" with no referent asks, in enforce, rather than muting the wrong thing
    assert await restraint.handle_turn("don't mention that again", MEMBER, "s1") == restraint.ACK_ASK
    assert _mutes(env) == []


async def test_leave_it_after_a_checkin_uses_the_continuity_focus(env):
    restraint.note_focus(MEMBER, INTERVIEW)
    assert await restraint.handle_turn("leave it", MEMBER, "s1") == restraint.ACK_MUTE
    (row,) = _mutes(env)
    assert "interview" in row[1].split() and row[2] is None


async def test_a_release_keeps_the_row_and_stamps_it(env):
    await restraint.handle_turn("stop mentioning the dentist", MEMBER, "s1")
    assert await restraint.handle_turn("you can mention the dentist again", MEMBER, "s2") == restraint.ACK_RELEASE
    (row,) = _mutes(env)
    assert row[8] == "released" and row[9] and row[10] == restraint.turn_key("you can mention the dentist again")
    assert await restraint.list_mutes(MEMBER) == []  # no longer honoured
    # releasing what was never muted is honest, not an error
    assert await restraint.handle_turn("you can mention the interview again", MEMBER, "s3") == restraint.ACK_RELEASE_NONE


async def test_a_deictic_release_frees_the_most_recent_mute(env):
    await restraint.handle_turn("stop mentioning the dentist", MEMBER, "s1")
    env["db"].conn.execute("UPDATE restraint_mutes SET created_at = '2026-10-01T00:00:00Z'")
    await restraint.handle_turn("stop mentioning the interview", MEMBER, "s1")
    await restraint.handle_turn("ok you can mention it again", MEMBER, "s2")
    assert {r[1]: r[8] for r in _mutes(env)} == {"dentist": "active", "interview": "released"}


async def test_the_mute_survives_a_restart(env):
    await restraint.handle_turn("stop mentioning the dentist", MEMBER, "s1")
    restraint._reset_state()  # a new process: only the table remains
    (m,) = await restraint.list_mutes(MEMBER)
    assert m.stems == frozenset({"dentist"})


async def test_erase_entity_and_erase_user_remove_the_rows(env):
    await restraint.handle_turn("never mention Dana's surgery again", MEMBER, "s1")
    await restraint.handle_turn("stop mentioning the interview", MEMBER, "s1")
    assert await restraint.erase_entity(MEMBER, "Dana") == 1
    assert [r[1] for r in _mutes(env)] == ["interview"]
    await restraint.store_thread_classes(env["db"], MEMBER, [("open_loops:1", DENTIST, "open_loop")])
    assert await restraint.erase_user(MEMBER) >= 2
    assert _mutes(env) == [] and env["db"].rows("SELECT * FROM restraint_classes") == []


# ═════════════════════════════════════════════════════════════════════════════
# 1b. thread classes: stored, invalidate-never-delete
# ═════════════════════════════════════════════════════════════════════════════
async def test_thread_classes_are_stored_once_and_a_changed_text_invalidates_the_old_row(env):
    db = env["db"]
    items = [("open_loops:1", DENTIST, "open_loop"), ("events:e1", PARCEL, "event")]
    assert await restraint.store_thread_classes(db, MEMBER, items) == 2
    assert await restraint.store_thread_classes(db, MEMBER, items) == 0  # idempotent
    stored = await restraint.load_thread_classes(db, MEMBER)
    assert restraint.thread_classes("open_loops:1", DENTIST, "open_loop", stored) == ("health", "affect")
    # the loop's words changed: the old row is invalidated (kept), a new one is valid
    assert await restraint.store_thread_classes(db, MEMBER, [("open_loops:1", PARCEL, "open_loop")]) == 1
    rows = db.rows("SELECT classes, invalid_at FROM restraint_classes WHERE subject_ref = 'open_loops:1' "
                   "ORDER BY derived_at, rowid")
    assert len(rows) == 2 and sum(1 for r in rows if r[1] is None) == 1
    assert {r[0] for r in rows} == {"health,affect", ""}
    # back to the earlier words: the earlier row is revalidated, nothing is duplicated
    assert await restraint.store_thread_classes(db, MEMBER, [("open_loops:1", DENTIST, "open_loop")]) == 1
    assert db.rows("SELECT COUNT(*) FROM restraint_classes WHERE subject_ref = 'open_loops:1'") == [(2,)]
    assert db.rows("SELECT COUNT(*) FROM restraint_classes WHERE subject_ref = 'open_loops:1' "
                   "AND invalid_at IS NULL") == [(1,)]


async def test_a_classifier_version_bump_invalidates_every_stored_class(env, monkeypatch):
    await restraint.store_thread_classes(env["db"], MEMBER, [("open_loops:1", DENTIST, "open_loop")])
    stored = await restraint.load_thread_classes(env["db"], MEMBER)
    monkeypatch.setattr(restraint, "VERSION", restraint.VERSION + 1)
    # a label from another classifier is ignored: the planted class is not returned
    planted = {"open_loops:1": (restraint.VERSION - 1, restraint.text_hash(DENTIST), "money")}
    assert restraint.thread_classes("open_loops:1", DENTIST, "open_loop", planted) != ("money",)
    assert await restraint.store_thread_classes(env["db"], MEMBER, [("open_loops:1", DENTIST, "open_loop")]) == 1
    assert env["db"].rows("SELECT COUNT(*) FROM restraint_classes") == [(2,)]
    assert stored  # the first generation was read before the bump


async def test_the_nightly_pass_stores_the_classes_of_what_it_keeps(env):
    _loop(env["db"], DENTIST)
    _loop(env["db"], "The kitchen renovation quote is overdue", weight=2)
    await _nightly(env)
    got = {r[0]: r[1] for r in env["db"].rows("SELECT subject_ref, classes FROM restraint_classes")}
    assert got == {"open_loops:1": "health,affect", "open_loops:2": ""}


async def test_no_classes_are_stored_with_the_flag_off(env, monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    _loop(env["db"], DENTIST)
    await _nightly(env)
    assert env["db"].rows("SELECT * FROM restraint_classes") == []


# ═════════════════════════════════════════════════════════════════════════════
# 2. withhold from the selector's raise
# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture
async def seeded(env):
    _loop(env["db"], DENTIST, weight=5)                                   # sensitive, top salience
    _loop(env["db"], "The kitchen renovation quote is overdue", weight=2)  # plain
    await _nightly(env)
    return env


async def _raise(message, sid="s1"):
    r = await sel.prepare(message, MEMBER, sid)
    return r


async def test_a_bare_greeting_raises_the_plain_thread_not_the_sensitive_one(seeded):
    r = await _raise(GREET)
    assert r is not None and "kitchen" in r.text and "dentist" not in r.text.lower()


async def test_an_open_question_raises_the_sensitive_thread(seeded):
    r = await _raise(OPEN)
    assert r is not None and "dentist" in r.text.lower()


async def test_the_owner_naming_the_topic_is_a_pull(seeded):
    r = await _raise("my dentist is on Friday")
    assert r is not None and r.shape == "cue" and "dentist" in r.text.lower()


async def test_a_voice_the_gate_did_not_confirm_never_gets_a_sensitive_raise(seeded):
    restraint.bind_verdict(False)
    r = await _raise(OPEN)  # an open question, a pull - and still no
    assert r is None or "dentist" not in r.text.lower()
    assert r is not None and "kitchen" in r.text  # the plain thread is still fine in front of a guest
    restraint.bind_verdict(None)


async def test_shadow_raises_exactly_as_before_and_logs_what_it_would_have_withheld(seeded, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
    with caplog.at_level(logging.INFO, logger="restraint"):
        r = await _raise(GREET)
    assert r is not None and "dentist" in r.text.lower()  # byte-for-byte the pre-restraint pick
    line = next(m for m in caplog.messages if m.startswith("RESTRAINT user="))
    assert "surface=raise_greeting mode=shadow withheld=1 reasons=sensitive:1" in line
    assert "dentist" not in line and "molar" not in line  # counts and class names only, never text


async def test_off_changes_nothing(seeded, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    with caplog.at_level(logging.INFO, logger="restraint"):
        r = await _raise(GREET)
    assert "dentist" in r.text.lower() and not [m for m in caplog.messages if m.startswith("RESTRAINT")]


async def test_a_muted_thread_is_never_raised_even_on_a_pull_or_a_cue(seeded):
    await restraint.handle_turn("stop bringing up the dentist", MEMBER, "s0")
    first = await _raise(OPEN)
    assert first is not None and "dentist" not in first.text.lower()  # the plain thread, not the muted one
    for sid, said in (("s2", "my dentist is on Friday"), ("s3", "I am at the dentist now")):
        sel._reset_state()
        seeded["now"] = seeded["now"] + timedelta(days=1)  # clear of the member gap
        got = await _raise(said, sid)  # a cue about the muted topic: the owner's words are not a licence to nag
        assert got is None or "dentist" not in got.text.lower()


async def test_a_released_mute_raises_again(seeded):
    await restraint.handle_turn("stop bringing up the dentist", MEMBER, "s0")
    assert "dentist" not in (await _raise(OPEN)).text.lower()
    sel._reset_state()
    await restraint.handle_turn("you can mention the dentist again", MEMBER, "s1")
    seeded["now"] = NOW + timedelta(hours=3)
    assert "dentist" in (await _raise(OPEN, "s9")).text.lower()


async def test_an_event_is_the_schedule_and_only_the_guest_rule_holds_it(env):
    db = env["db"]
    db.conn.execute("INSERT INTO events (id, user_id, title, start_date, start_time) VALUES "
                    "('e1', ?, 'Dentist', '2026-10-09', '09:30')", (MEMBER,))
    await _nightly(env)
    assert (await _raise(GREET)) is not None                # an event rides a bare greeting
    sel._reset_state()
    db.conn.execute("UPDATE proactive_candidates SET surfaced_count = 0, cooldown_until = NULL, "
                    "last_surfaced_session = NULL, last_surfaced_at = NULL")
    restraint.bind_verdict(False)
    assert (await _raise(GREET, "s2")) is None              # but not in front of a guest
    restraint.bind_verdict(None)


async def test_the_bar_and_day_sim_open_turns_are_pulls_so_the_raises_they_measure_still_happen(env):
    """Samantha bar S5 ("Hi Zoe, how are things?" / "Hey Zoe, what's new?") and day-sim 1r / 7r / 7s ("Morning
    Zoe, how's it going?" / "Hey Zoe, what's new?") raise a WORRY with feelings in it on an open turn. Those
    openers are open questions, so under enforce the same raises happen; the 7r / 7s no-repeat rules (cooldown,
    member gap) sit before and after restraint and are untouched."""
    for opener in ("Hi Zoe, how are things?", "Hey Zoe, what's new?", "Morning Zoe, how's it going?"):
        assert restraint.is_pull(opener), opener
    _loop(env["db"], INTERVIEW, weight=5)                               # S5's seeded worry (a feeling, a job)
    _loop(env["db"], "I've got the dentist on Friday for a cracked molar and honestly I'm nervous", weight=5)
    await _nightly(env)
    first = await _raise("Morning Zoe, how's it going?", "open-1")
    assert first is not None and first.shape == "greeting"             # 1r: exactly one raise
    assert await sel.settle(first, produced=True)
    assert await _raise("Hey Zoe, what's new?", "open-2") is None       # 7s: minutes apart, the member gap holds
    env["now"] = NOW + timedelta(seconds=sel.raise_gap_s() + 1)
    again = await _raise("Hey Zoe, what's new?", "open-3")
    assert again is not None and again.candidate_id != first.candidate_id   # 7r: the next thread, not the same one


async def test_the_owners_previous_turn_keeps_the_topic_alive_for_an_are_you_sure(env):
    restraint.note_turn(MEMBER, "what time is my dentist appointment on Friday")
    restraint.note_turn(MEMBER, "Are you sure? I thought I told you.")
    p = await _packet("dentist appointment")      # the model's own query on the tool path
    assert HEALTH.text in p
    restraint._marks[MEMBER] = ("Are you sure? I thought I told you.", None, restraint._marks[MEMBER][2] - 400, "")
    restraint.note_turn(MEMBER, "set a timer")    # five-plus minutes later the topic has gone
    assert HEALTH.text not in await _packet("dentist appointment")


def test_a_kin_word_is_a_topic_although_it_is_short():
    assert restraint.stems("How's my mum doing?") & restraint.stems("User's mum Ingrid lives in Bendigo")
    assert restraint.stems("my mom") == restraint.stems("my mother") == frozenset({"mother"})
    assert not restraint.stems("what should I cook tonight") & restraint.stems("User's mum Ingrid lives in Bendigo")


# ── red when removed: the same scenarios with each rule neutered must LEAK ────
async def _leak_sensitive(env) -> bool:
    _loop(env["db"], DENTIST, weight=5)
    await _nightly(env)
    r = await _raise(GREET)
    return r is not None and "dentist" in r.text.lower()


async def test_negative_control_without_the_class_check_a_greeting_leaks_the_sensitive_thread(env, monkeypatch):
    assert await _leak_sensitive(env) is False
    sel._reset_state()
    monkeypatch.setattr(restraint, "decide", lambda *a, **k: restraint.Decision(True))
    assert await _leak_sensitive(env) is True


async def test_negative_control_without_the_guest_rule_a_stranger_is_told(env, monkeypatch):
    _loop(env["db"], DENTIST, weight=5)
    await _nightly(env)
    restraint.bind_verdict(False)
    assert (await _raise(OPEN)) is None
    sel._reset_state()
    monkeypatch.setattr(restraint, "current_verdict", lambda: None)  # the verdict never reaches the rule
    r = await _raise(OPEN, "s2")
    assert r is not None and "dentist" in r.text.lower()
    restraint.bind_verdict(None)


async def test_negative_control_without_the_mute_check_the_thread_comes_back(env, monkeypatch):
    _loop(env["db"], "The kitchen renovation quote is overdue", weight=5)
    await _nightly(env)
    await restraint.handle_turn("stop bringing up the kitchen", MEMBER, "s0")
    assert (await _raise(GREET)) is None
    sel._reset_state()
    monkeypatch.setattr(restraint, "muted", lambda *a, **k: False)
    r = await _raise(GREET, "s2")
    assert r is not None and "kitchen" in r.text


# ═════════════════════════════════════════════════════════════════════════════
# 4. back-off from the ledger
# ═════════════════════════════════════════════════════════════════════════════
def test_backoff_level_counts_consecutive_ignored_raises_of_the_kind_and_skips_the_unheard():
    ign = ("open_loop", "ignored")
    assert restraint.backoff_level([], "open_loop") == 0
    assert restraint.backoff_level([ign, ign], "open_loop") == 2
    assert restraint.backoff_level([ign, ("open_loop", "accepted"), ign], "open_loop") == 1  # accepted ends the run
    assert restraint.backoff_level([("open_loop", "undelivered"), ign, ("open_loop", "unknown"), ign], "open_loop") == 2
    assert restraint.backoff_level([("emotional", "ignored"), ign], "open_loop") == 1       # per kind
    assert restraint.backoff_level([ign] * 9, "open_loop") == restraint.BACKOFF_CAP_LEVEL    # capped
    assert restraint.unanswered_run([ign, ("emotional", "ignored"), ("event", "unknown"), ign]) == 3
    assert restraint.unanswered_run([ign, ("emotional", "accepted"), ign]) == 1


def test_the_wait_doubles_with_each_ignored_raise_and_is_capped():
    base = 7200
    assert [restraint.required_gap_s(base, n) for n in range(4)] == [7200, 14400, 28800, 57600]
    assert restraint.required_gap_s(base, restraint.BACKOFF_CAP_LEVEL) == 7200 * 32
    assert restraint.required_gap_s(base, 99) == restraint.BACKOFF_MAX_S
    assert restraint.required_gap_s(0, 3) == 8  # a zero gap config cannot zero the back-off


def _row(kind, last):
    return ("id", kind, "t", "h", 0.5, 1, "", "x", None, 1, "s", last, "ref")


def test_backoff_decision_waits_then_clears_and_pauses_after_three_unanswered():
    t = lambda h: restraint._iso(NOW - timedelta(hours=h))  # noqa: E731
    led = [("open_loop", "ignored", t(5))]
    rows = [_row("open_loop", t(5))]
    # one ignored raise: the 2 h gap doubles to 4 h; at 5 h since the last raise it has elapsed
    assert restraint.backoff_decision(led, "open_loop", rows, NOW)[0] == ""
    assert restraint.backoff_decision(led, "open_loop", [_row("open_loop", t(3))], NOW) == ("backoff", 1)
    led2 = [("open_loop", "ignored", t(3)), ("open_loop", "ignored", t(30))]
    assert restraint.backoff_decision(led2, "open_loop", [_row("open_loop", t(7))], NOW) == ("backoff", 2)  # 8 h
    # three unanswered in a row of ANY kind pause raising for PAUSE_S
    led3 = [("open_loop", "ignored", t(10)), ("emotional", "ignored", t(20)), ("event", "ignored", t(30))]
    assert restraint.backoff_decision(led3, "emotional", [], NOW)[0] == "paused"
    far = [("open_loop", "ignored", t(100)), ("emotional", "ignored", t(110)), ("event", "ignored", t(120))]
    assert restraint.backoff_decision(far, "emotional", [], NOW)[0] == ""  # the pause is over
    # an accepted raise resets it
    ok = [("open_loop", "accepted", t(1)), ("open_loop", "ignored", t(3))]
    assert restraint.backoff_decision(ok, "open_loop", [_row("open_loop", t(1))], NOW) == ("", 0)


def _ledger(db, rows):
    for i, (kind, outcome, hours_ago) in enumerate(rows):
        at = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        db.conn.execute(
            "INSERT INTO proactive_deliveries (id, idem_key, user_id, candidate_id, kind, source_ref, shape, "
            "delivered_by, session_id, cue_words, trigger_key, voiced, surfaced_at, expires_at, outcome, "
            "outcome_at, created_at) VALUES (?, ?, ?, NULL, ?, ?, 'greeting', 'turn', ?, '', '', 1, ?, ?, ?, ?, ?)",
            (f"d{i}", f"k{i}", MEMBER, kind, f"open_loops:{i}", f"old{i}", at, at, outcome, at, at))


async def _backoff_scenario(env, ledger_rows) -> bool:
    """True when a raise 3 h after an ignored one went out (the back-off did NOT hold it)."""
    _loop(env["db"], "The kitchen renovation quote is overdue", weight=5)
    await _nightly(env)
    first = await _raise(OPEN)
    assert first is not None and await sel.settle(first, produced=True)
    _ledger(env["db"], ledger_rows)
    env["db"].conn.execute("UPDATE proactive_candidates SET surfaced_count = 0, cooldown_until = NULL, "
                           "last_surfaced_session = NULL, last_surfaced_at = ?",
                           (restraint._iso(NOW - timedelta(hours=3)),))
    sel._reset_state()
    env["now"] = NOW
    return (await _raise(OPEN, "s2")) is not None


async def test_an_ignored_raise_doubles_the_wait_before_that_kind_is_raised_again(env):
    # base gap 2 h: 3 h after the last raise it is allowed, unless the last raise was IGNORED (gap 4 h)
    assert await _backoff_scenario(env, [("open_loop", "accepted", 3)]) is True


async def test_the_ledger_evidence_defers_the_next_raise(env):
    assert await _backoff_scenario(env, [("open_loop", "ignored", 3)]) is False


async def test_shadow_back_off_defers_nothing(env, monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
    assert await _backoff_scenario(env, [("open_loop", "ignored", 3)]) is True


async def test_negative_control_without_the_back_off_the_ignored_raise_comes_straight_back(env, monkeypatch):
    async def never(*a, **k):
        return ""
    monkeypatch.setattr(restraint, "backoff_why", never)
    assert await _backoff_scenario(env, [("open_loop", "ignored", 3)]) is True


# ═════════════════════════════════════════════════════════════════════════════
# 2b. the brief
# ═════════════════════════════════════════════════════════════════════════════
def _ctx():
    return {
        "calendar": [{"title": "Standup", "start": "09:00"}, {"title": "Dentist", "start": "09:30"}],
        "open_loops": [{"id": 1, "text": DENTIST, "hint": "", "due": None},
                       {"id": 2, "text": "book the car service", "hint": "", "due": None}],
        "emotional_moments": [INTERVIEW],
        "emotional_moment_ids": ["m1"],
    }


async def test_the_brief_after_a_bare_greeting_carries_the_day_and_not_the_feelings(env):
    ctx = _ctx()
    out = await restraint.filter_brief_ctx(ctx, MEMBER, "good morning")
    assert [lp["id"] for lp in out["open_loops"]] == [2]
    assert out["emotional_moments"] == [] and out["emotional_moment_ids"] == []
    assert [e["title"] for e in out["calendar"]] == ["Standup", "Dentist"]  # the day itself stays
    assert [lp["id"] for lp in ctx["open_loops"]] == [1, 2]                 # the cached ctx is untouched


async def test_the_brief_after_whats_up_carries_everything(env):
    out = await restraint.filter_brief_ctx(_ctx(), MEMBER, "what's up")
    assert [lp["id"] for lp in out["open_loops"]] == [1, 2] and out["emotional_moments"] == [INTERVIEW]


async def test_the_brief_in_front_of_a_guest_withholds_even_the_calendar_title(env):
    restraint.bind_verdict(False)
    out = await restraint.filter_brief_ctx(_ctx(), MEMBER, "what's up")
    assert [lp["id"] for lp in out["open_loops"]] == [2] and out["emotional_moments"] == []
    assert [e["title"] for e in out["calendar"]] == ["Standup"]
    restraint.bind_verdict(None)


async def test_the_brief_honours_a_mute_on_a_pull(env):
    await restraint.handle_turn("stop bringing up the interview", MEMBER, "s1")
    out = await restraint.filter_brief_ctx(_ctx(), MEMBER, "what's up")
    assert out["emotional_moments"] == [] and [lp["id"] for lp in out["open_loops"]] == [1, 2]


async def test_shadow_brief_is_the_same_object(env, monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
    ctx = _ctx()
    assert await restraint.filter_brief_ctx(ctx, MEMBER, "good morning") is ctx


# ═════════════════════════════════════════════════════════════════════════════
# 2c. the recall packet (the real composer, so what the model sees is what is asserted)
# ═════════════════════════════════════════════════════════════════════════════
def _ref(rid, text, mtype="fact", **meta):
    return MemoryRef(id=rid, text=text, metadata={"status": "approved", "memory_type": mtype, **meta})


HEALTH = _ref("hea00001", "User has a dentist appointment on Friday for a cracked molar")
WORRY = _ref("wor00001", INTERVIEW, "emotional_moment", candidate_affect="anxious")
SISTER = _ref("sis00001", "User's sister Marisol is flying in from Lisbon", "person", entity_type="person")
PLAIN = _ref("pla00001", "User is pescatarian and does not eat meat")
ROWS = [HEALTH, WORRY, SISTER, PLAIN]


async def _packet(message, *, mood=False, rows=None):
    import routers.memories as memories

    rows = ROWS if rows is None else rows
    facts, hits, recent = await restraint.apply_to_packet(MEMBER, message, list(rows), [], None, mood=mood)
    return memories._build_memory_prompt_packet(facts, hits)["packet"]


async def test_a_task_turn_packet_carries_the_plain_rows_only(env):
    p = await _packet("what should I cook tonight")
    assert PLAIN.text in p
    for gone in (HEALTH.text, WORRY.text, SISTER.text):
        assert gone not in p


async def test_withholding_leaves_no_trace_and_no_instruction(env):
    p = (await _packet("what should I cook tonight")).lower()
    for word in ("dentist", "molar", "aquarium", "interview", "marisol", "lisbon", "withheld", "do not", "don't",
                 "never mention", "avoid", "sensitive", "restraint", "omitted", "hidden"):
        assert word not in p, word


async def test_the_owner_asking_about_the_topic_gets_the_row(env):
    p = await _packet("when is my dentist appointment")
    assert HEALTH.text in p and WORRY.text not in p and SISTER.text not in p


async def test_a_mood_statement_pulls_the_feeling_row_only(env):
    p = await _packet("had such a rough day", mood=True)
    assert WORRY.text in p and HEALTH.text not in p and SISTER.text not in p
    assert WORRY.text not in await _packet("had such a rough day")  # without the trigger it is not a pull


async def test_an_open_question_pulls_everything(env):
    p = await _packet("what do you know about me")
    for text in (HEALTH.text, WORRY.text, SISTER.text, PLAIN.text):
        assert text in p


async def test_a_voice_the_gate_did_not_confirm_gets_no_sensitive_row_whatever_it_asks(env):
    restraint.bind_verdict(False)
    p = await _packet("what do you know about me")
    assert PLAIN.text in p
    for gone in (HEALTH.text, WORRY.text, SISTER.text):
        assert gone not in p
    restraint.bind_verdict(None)


async def test_the_tool_path_is_judged_on_the_owners_words_not_the_models_query(env):
    """recall_memory carries only the model's query; the turn the owner actually spoke was noted at its start."""
    restraint.note_turn(MEMBER, "set a timer for ten minutes")
    p = await _packet("dentist appointment")  # the model's own query
    assert HEALTH.text not in p
    restraint.note_turn(MEMBER, "when is my dentist appointment")
    assert HEALTH.text in await _packet("dentist appointment")


async def test_a_mute_withholds_a_row_from_a_mood_turn_but_not_from_the_owners_own_question(env):
    await restraint.handle_turn("stop bringing up the interview", MEMBER, "s1")
    assert WORRY.text not in await _packet("had such a rough day", mood=True)
    assert WORRY.text in await _packet("how did my interview go")  # they asked: muting is about volunteering


async def test_a_row_stamped_at_ingest_is_read_back_from_its_label(env):
    md = {"memory_type": "fact", "tags": "dental"}
    restraint.stamp(md, "Zahnarzt am Freitag")
    row = _ref("sta00001", "Zahnarzt am Freitag", tags="dental", **{k: md[k] for k in ("sensitivity", "sensitivity_v", "sensitivity_h")})
    p = await _packet("what should I cook tonight", rows=[row, PLAIN])
    assert "Zahnarzt" not in p and PLAIN.text in p


async def test_shadow_packet_keeps_every_row_and_logs_the_would_withhold(env, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
    with caplog.at_level(logging.INFO, logger="restraint"):
        p = await _packet("what should I cook tonight")
    for text in (HEALTH.text, WORRY.text, SISTER.text, PLAIN.text):
        assert text in p
    line = next(m for m in caplog.messages if m.startswith("RESTRAINT user="))
    assert "surface=packet mode=shadow withheld=3" in line and "health" in line and "affect" in line


async def test_off_leaves_the_lists_untouched(env, monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    facts = list(ROWS)
    out = await restraint.apply_to_packet(MEMBER, "what should I cook tonight", facts, [], None)
    assert out[0] is facts


async def test_a_failure_inside_restraint_is_no_restraint_never_a_broken_turn(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(restraint, "row_classes", boom)
    facts = list(ROWS)
    out = await restraint.apply_to_packet(MEMBER, "what should I cook tonight", facts, [], None)
    assert out[0] is facts


async def test_negative_control_without_the_filter_the_task_turn_packet_leaks(env, monkeypatch):
    assert HEALTH.text not in await _packet("what should I cook tonight")
    monkeypatch.setattr(restraint, "decide", lambda *a, **k: restraint.Decision(True))
    p = await _packet("what should I cook tonight")
    assert HEALTH.text in p and WORRY.text in p and SISTER.text in p


# ═════════════════════════════════════════════════════════════════════════════
# the migration
# ═════════════════════════════════════════════════════════════════════════════
def test_the_migration_creates_both_tables_and_downgrades_cleanly(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    _apply(engine, "0040_restraint")
    _apply(engine, "0040_restraint")  # IF NOT EXISTS: a rerun is safe
    insp = sa.inspect(engine)
    assert {"restraint_mutes", "restraint_classes"} <= set(insp.get_table_names())
    assert {"status", "released_at", "turn_key", "topic_key", "thread_ref"} <= {c["name"] for c in insp.get_columns("restraint_mutes")}
    assert {"invalid_at", "version", "text_hash", "signals"} <= {c["name"] for c in insp.get_columns("restraint_classes")}
    _apply(engine, "0040_restraint", fn="downgrade")
    assert not {"restraint_mutes", "restraint_classes"} & set(sa.inspect(engine).get_table_names())


def test_the_revision_chain_has_one_head():
    import ast

    revs = {}
    for f in (SVC / "alembic" / "versions").glob("*.py"):
        tree = ast.parse(f.read_text())
        vals = {n.targets[0].id: n.value.value for n in tree.body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and isinstance(n.value, ast.Constant)}
        if "revision" in vals:
            revs[vals["revision"]] = vals.get("down_revision")
    heads = set(revs) - {d for d in revs.values() if d}
    assert heads == {"0040"} and revs["0040"] == "0039"


# ═════════════════════════════════════════════════════════════════════════════
# the wiring: the real callers, so a green run means the rule is actually on the path
# ═════════════════════════════════════════════════════════════════════════════
import json  # noqa: E402

import brief_first_turn as bft  # noqa: E402
import routers.memories as memories  # noqa: E402
import zoe_flue_client as zc  # noqa: E402


class _Svc:
    def __init__(self, rows):
        self.rows = rows

    async def load_for_prompt(self, user_id, *, limit):
        return self.rows[:limit]

    async def load_recent_for_prompt(self, user_id, *, window_s, limit, emotional_first=False):
        return list(self.rows)

    async def search(self, query, *, user_id, limit=6, **_):
        return []


def _fresh(rows):
    now = datetime.now(timezone.utc).isoformat()
    return [MemoryRef(id=r.id, text=r.text, metadata={**r.metadata, "added_at": now}) for r in rows]


async def _endpoint(monkeypatch, message, **kw):
    monkeypatch.setattr(memories, "_svc", lambda: _Svc(_fresh(ROWS)))
    for key in ("ZOE_EMOTIONAL_RECALL_ENABLED", "ZOE_MEMORY_COMPOSE_ENABLED", "ZOE_PERSON_SUGGEST_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    return await memories.memory_for_prompt(user_id=MEMBER, message=message, limit=12, _=None, **kw)


async def test_the_for_prompt_endpoint_withholds_on_a_task_turn(env, monkeypatch):
    res = await _endpoint(monkeypatch, "what should I cook tonight")
    assert PLAIN.text in res["packet"] and HEALTH.text not in res["packet"] and WORRY.text not in res["packet"]
    assert [r["id"] for r in res["refs"]] == [PLAIN.id]


async def test_the_for_prompt_endpoint_keeps_the_s4_continuity_check_in(env, monkeypatch):
    """A mood statement still gets yesterday's worry (bar S4) and still does not get the sister or the dentist."""
    res = await _endpoint(monkeypatch, "Ugh, I've been feeling a bit on edge today", mode="continuity")
    assert WORRY.text in res["packet"] and HEALTH.text not in res["packet"] and SISTER.text not in res["packet"]
    assert res["continuity_focus"]["text"] == WORRY.text


async def test_the_for_prompt_endpoint_in_shadow_is_unchanged(env, monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
    res = await _endpoint(monkeypatch, "what should I cook tonight")
    for text in (HEALTH.text, WORRY.text, SISTER.text, PLAIN.text):
        assert text in res["packet"]


async def test_negative_control_remove_the_endpoint_call_and_the_task_turn_leaks(env, monkeypatch):
    async def passthrough(user_id, message, facts, hits, recent=None, *, mood=False):
        return facts, hits, recent
    monkeypatch.setattr(restraint, "apply_to_packet", passthrough)
    res = await _endpoint(monkeypatch, "what should I cook tonight")
    assert HEALTH.text in res["packet"]


@pytest.fixture
def brief_env(monkeypatch):
    monkeypatch.setenv("ZOE_BRIEF_ON_FIRST_TURN", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    for key in ("ZOE_BRIEF_WINDOW_START", "ZOE_BRIEF_WINDOW_END", "ZOE_LOOP_LIFECYCLE"):
        monkeypatch.delenv(key, raising=False)
    bft._reset_state()
    restraint._reset_state()
    calls = []

    async def claim(uid, now):
        return "free"

    async def gather(uid, local_date):
        calls.append(1)
        return _ctx()

    @contextlib.asynccontextmanager
    async def no_db():
        raise RuntimeError("no database in this test")
        yield  # pragma: no cover

    monkeypatch.setattr(bft, "_claim_state", claim)
    monkeypatch.setattr(bft, "_gather", gather)
    monkeypatch.setattr(bft, "_now_utc", lambda: datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(db_compat, "get_compat_db", no_db)  # mutes unreadable -> none (fail-open)
    yield calls
    bft._reset_state()
    restraint._reset_state()


async def test_the_first_turn_brief_after_good_morning_has_the_day_and_not_the_feelings(brief_env):
    b = await bft.prepare("good morning", MEMBER)
    assert b is not None and b.shape == "greeting"
    assert "book the car service" in b.body and "Dentist at 09:30" in b.body  # the day, and the schedule
    assert "cracked molar" not in b.body and "aquarium" not in b.body        # the threads wait


async def test_the_first_turn_brief_after_whats_up_has_everything(brief_env):
    b = await bft.prepare("morning, how's it going?", MEMBER)
    assert "cracked molar" in b.body and "aquarium" in b.body and "book the car service" in b.body


async def test_the_brief_marks_only_what_it_said_as_surfaced(brief_env, monkeypatch):
    """The marked set is built from the FILTERED context, so a withheld thread is not stamped as heard."""
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "1")
    b = await bft.prepare("good morning", MEMBER, "sess-1")
    assert [ref for _k, ref, _t in b.surfaced] == ["open_loops:2"]


class _Client:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["content"] = content

        class _R:
            def raise_for_status(self):
                return None

            def json(self):
                return {"result": {"text": "ok"}}
        return _R()


async def test_the_flue_seam_notes_the_owners_turn_for_the_tool_path(env, monkeypatch):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    noted = []
    monkeypatch.setattr(restraint, "note_turn", lambda uid, msg: noted.append((uid, msg)))
    out = [c async for c in zc.run_flue_brain_streaming("set a timer for ten minutes", "s1", MEMBER)]
    assert out == ["ok"] and noted == [(MEMBER, "set a timer for ten minutes")]


async def test_the_flue_seam_speaks_the_acknowledgement_and_never_calls_the_brain(env, monkeypatch):
    calls = []

    async def brain(message, session_id, user_id="", **kw):
        calls.append(message)
        yield "brain reply"

    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", brain)
    sink: dict = {}
    out = [c async for c in zc.run_flue_brain_streaming("stop mentioning the dentist", "s1", MEMBER, outcome_sink=sink)]
    assert out == [restraint.ACK_MUTE] and calls == []
    assert sink.get("reason") == "restraint_mute"
    assert len(_mutes(env)) == 1
    # an ordinary turn goes to the brain untouched
    assert [c async for c in zc.run_flue_brain_streaming("set a timer", "s1", MEMBER)] == ["brain reply"]


async def test_in_shadow_the_mute_is_recorded_and_the_brain_still_answers(env, monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")

    async def brain(message, session_id, user_id="", **kw):
        yield "brain reply"

    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", brain)
    out = [c async for c in zc.run_flue_brain_streaming("stop mentioning the dentist", "s1", MEMBER)]
    assert out == ["brain reply"] and len(_mutes(env)) == 1


async def test_a_replayed_corpus_turn_never_writes_a_mute(env, monkeypatch):
    async def brain(message, session_id, user_id="", **kw):
        yield "brain reply"

    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", brain)
    out = [c async for c in zc.run_flue_brain_streaming("stop mentioning the dentist", "s1", MEMBER, replay_isolation=True)]
    assert out == ["brain reply"] and _mutes(env) == []


def test_the_voice_route_binds_the_speaker_gates_verdict():
    """routers/voice_tts is too heavy to import here; pin the wiring at the source."""
    src = (SVC / "routers" / "voice_tts.py").read_text()
    assert "_restraint_bind_verdict(_speaker_verified)" in src and src.index("_speaker_verdict(payload, caller") < src.index(
        "_restraint_bind_verdict(_speaker_verified)")


async def test_the_brief_filter_adds_nothing_the_context_did_not_already_hold(env):
    """L3 for the brief: the output is a SUBSET of the input - no marker, no instruction, no placeholder."""
    ctx = _ctx()
    out = await restraint.filter_brief_ctx(ctx, MEMBER, "good morning")
    assert all(lp in ctx["open_loops"] for lp in out["open_loops"])
    assert all(m in ctx["emotional_moments"] for m in out["emotional_moments"])
    assert set(out) == set(ctx)
