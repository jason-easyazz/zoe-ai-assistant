"""The commitment tracker (``commitments.py``, migration 0044; ``ZOE_COMMITMENTS`` = off | shadow | enforce, default shadow).

Zoe's OWN reply makes a timed promise ("I'll remind you at 5", "I'll check back tomorrow about the dentist"); a row is recorded; at the
due time a deterministic check says it was kept, or she makes it now, or she owns it up ONCE in the pull queue. Groups, each with a
break-the-fix control (the ``test_control_*`` tests and the flag arms; each was run red before commit - see the PR):

  extraction    cue + time in ONE sentence of Zoe's reply; offers, conditions, questions, negations and time-less sentences are not promises
  user words    a promise-shaped USER turn records nothing, whatever Zoe replied
  walls         off the record, distress, guests: nothing recorded; a normal turn beside them is (the control)
  recording     shadow records and acts on nothing; off records nothing; recording is idempotent per reply
  due check     kept (a reminder exists, however it was made) / fulfilled late / owned up once / the pull delivers it once
  check-back    armed in the pull queue held until due; delivered once; "never asked" is void, not a breach
  language      the cue words are DATA (no English literal in the code); Spanish works from its file alone
  wiring        brain_dispatch records from the reply stream with the tool names; the selector leaves the candidate alone

Synthetic data only. The DB edge is a real SQLite file built by migrations 0033 + 0036 + 0041 + 0044 behind ``db_compat``.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import ast
import asyncio
import contextlib
import importlib.util
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import commitments as cm
import db_compat
import memory_provenance as mp
from db_pool import _Cursor, _ExecResult
from proactive import lines, pull
from proactive import selector as sel

SVC = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NOW = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)         # Sat 14:00 Perth; the household clock is UTC in these tests
MEMBER, OTHER = "member-a", "member-b"
PROMISE_5 = "Sure, I'll remind you at 5 about the dentist."
DUE_5 = datetime(2026, 10, 10, 17, 0, tzinfo=UTC)


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


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
    for name in ("0033_proactive_candidates.py", "0036_proactive_deliveries.py", "0041_proactive_ledger_lines.py",
                 "0044_reply_sources_commitments.py"):
        _migrate(engine, name)
    db = _Sqlite(path)
    db.conn.execute(
        "CREATE TABLE reminders (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, description TEXT, reminder_type TEXT, category TEXT, "
        "priority TEXT, due_date TEXT, due_time TEXT, recurring_pattern TEXT, is_active INTEGER DEFAULT 1, acknowledged INTEGER DEFAULT 0, "
        "snoozed_until TEXT, visibility TEXT, deleted INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT)")
    db.conn.execute("CREATE TABLE proactive_pending (id TEXT, trigger_type TEXT, item_id TEXT)")

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    for key in (cm.ENV, "ZOE_PULL_NOT_PUSH", "ZOE_DELIVERY_LEDGER", "ZOE_PROACTIVE_LEDGER", "ZOE_RESTRAINT", "ZOE_MEMORY_PROVENANCE_ANSWERS",
                "ZOE_BRIEF_ON_FIRST_TURN", "ZOE_SYNTHETIC_USER_ALLOWLIST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    sel._reset_state()
    lines._reset_state()
    state = {"now": NOW, "db": db, "scheduled": []}
    monkeypatch.setattr(pull, "_now", lambda: state["now"])
    monkeypatch.setattr(lines, "_now", lambda: state["now"])
    monkeypatch.setattr(sel, "_now", lambda: state["now"])

    async def fake_schedule(user_id, message, send_at, item_id=""):
        state["scheduled"].append((user_id, message, item_id))
        return "x"

    import proactive.triggers.reminders as trig
    monkeypatch.setattr(trig, "schedule_reminder", fake_schedule)
    mp.reset()
    cm._pending.clear()
    cm._last_purge = 0.0
    yield state
    sel._reset_state()
    lines._reset_state()
    mp.reset()


@pytest.fixture(autouse=True)
def _household_is_utc(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")       # the pure extraction tests too: "5" means 17:00 UTC here


def run(coro):
    return asyncio.run(coro)


def rows(env, sql="SELECT * FROM commitments ORDER BY due_at"):
    return env["db"].rows(sql)


def promise(env, reply=PROMISE_5, *, user=MEMBER, session="s1", message="remind me later", tools=(), reply_id="r1", at=None):
    """Zoe's reply ended: the hook records its promises (background task awaited)."""
    async def go():
        mp.note_user_turn(user, message, session)
        mp.claim_turn(user, message)
        ok = cm.schedule_record(user, session, reply_id, reply, message, tools, now=at or NOW)
        await cm.flush()
        return ok
    return run(go())


def reminder(env, *, user=MEMBER, due_date="2026-10-10", due_time="17:00", created=None, deleted=0, ack=0, title="Dentist"):
    created = created or NOW
    env["db"].conn.execute(
        "INSERT INTO reminders (id, user_id, title, due_date, due_time, is_active, acknowledged, deleted, created_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (f"rem{env['db'].conn.execute('SELECT COUNT(*) FROM reminders').fetchone()[0]}", user, title, due_date, due_time, ack, deleted,
         created.strftime("%Y-%m-%d %H:%M:%S.123456+00")))
    env["db"].conn.commit()


def sweep(at):
    return run(cm.sweep(now=at))


def lines_of(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("COMMITMENT")]


# ── extraction (pure) ────────────────────────────────────────────────────────

def ex(reply, now=NOW):
    return cm.extract(reply, now=now)


@pytest.mark.parametrize("reply,kind,due,about", [
    ("Sure, I'll remind you at 5 about the dentist.", "remind", datetime(2026, 10, 10, 17, 0, tzinfo=UTC), "about the dentist"),
    ("I'll check back tomorrow about the dentist.", "check_back", datetime(2026, 10, 11, 9, 0, tzinfo=UTC), "about the dentist"),
    ("Don't worry, I'll remind you at 5 to call your mum.", "remind", DUE_5, "to call your mum"),
    ("I'll remind you in 20 minutes.", "remind", datetime(2026, 10, 10, 6, 20, tzinfo=UTC), ""),
    ("I will remind you tomorrow morning about the school pickup.", "remind", datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
     "about the school pickup"),
    ("I'll remind you on Friday at 9am to book the vet.", "remind", datetime(2026, 10, 16, 9, 0, tzinfo=UTC), "to book the vet"),
    ("I'll ping you at 5 p.m. tomorrow about swimming.", "remind", datetime(2026, 10, 11, 17, 0, tzinfo=UTC), "about swimming"),
    ("I'm going to follow up on Monday.", "check_back", datetime(2026, 10, 12, 9, 0, tzinfo=UTC), ""),
    ("I’ll remind you at noon tomorrow.", "remind", datetime(2026, 10, 11, 12, 0, tzinfo=UTC), ""),
    ("I'll set a reminder for tonight at 8.", "remind", datetime(2026, 10, 10, 20, 0, tzinfo=UTC), ""),
])
def test_a_timed_first_person_future_promise_is_extracted(reply, kind, due, about):
    got = ex(reply)
    assert [(p.kind, p.due, p.about) for p in got] == [(kind, due, about)], reply


@pytest.mark.parametrize("reply", [
    "Let me know and I'll add it.",                                 # the owner's example: not a timed promise
    "Want me to remind you at 5?",                                  # a question
    "If you like, I'll remind you at 5.",                           # a condition
    "I can remind you at 5.",                                       # an offer
    "Once you tell me the time I'll remind you.",                   # a condition and no time
    "I'll remind you.",                                             # a cue and no time
    "I won't remind you at 5.",                                     # negated
    "I'll not check back tomorrow.",                                # negated
    "I'll remind you not to forget your keys at 5.",                # a negation inside the cue's own clause
    "I reminded you at 5 yesterday.",                               # past
    "The meeting is at 5 tomorrow.",                                # a time and no cue
    "You asked me to remind you at 5.",                             # her words about the USER's ask, not her promise
    "Happy to remind you at 5 if that helps.",
    "I'll remind you at 25 o'clock.",                               # no such time
    "I'll remind you in 3 months.",                                 # a unit the data does not know
    "",
])
def test_an_offer_a_condition_a_question_a_negation_or_a_time_less_sentence_is_not_a_promise(reply):
    assert ex(reply) == [], reply


def test_a_promise_in_the_past_is_not_tracked():
    assert ex("I'll remind you this morning about the bins.", now=datetime(2026, 10, 10, 14, 0, tzinfo=UTC)) == []
    assert ex("I'll remind you at 5 today.", now=datetime(2026, 10, 10, 19, 0, tzinfo=UTC)) == []


def test_an_ambiguous_clock_picks_the_next_future_one():
    # "at 5" said at 06:00 is 17:00 today only if 05:00 has passed - it has
    assert ex("I'll remind you at 5.", now=datetime(2026, 10, 10, 6, 0, tzinfo=UTC))[0].due == DUE_5
    assert ex("I'll remind you at 5.", now=datetime(2026, 10, 10, 3, 0, tzinfo=UTC))[0].due == datetime(2026, 10, 10, 5, 0, tzinfo=UTC)


def test_the_household_timezone_decides_what_5_means(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    got = ex("I'll remind you at 5pm.", now=datetime(2026, 10, 10, 2, 0, tzinfo=UTC))[0]
    assert got.due == datetime(2026, 10, 10, 9, 0, tzinfo=UTC)          # 17:00 Perth (UTC+8)


def test_at_most_three_promises_per_reply_and_duplicates_collapse():
    got = ex("I'll remind you at 5. I'll remind you at 5. I'll check back tomorrow. I'll check back on Monday. I'll remind you at 9pm.")
    assert len(got) == 3 and len({(p.kind, p.due) for p in got}) == 3


def test_spanish_comes_from_its_data_file_alone():
    got = ex("Te recordaré mañana por la mañana sobre el dentista.")
    assert [(p.kind, p.lang, p.about, p.due) for p in got] == [("remind", "es", "sobre el dentista", datetime(2026, 10, 11, 9, 0, tzinfo=UTC))]
    assert ex("Si quieres, te recordaré a las 5.") == []                                            # a condition
    assert ex("Volveré a preguntarte mañana.")[0].kind == "check_back"


def test_a_language_without_a_commitments_section_contributes_nothing():
    assert cm._lex("fr") is None and ex("Je te rappellerai à 17 heures.") == []


def test_no_english_cue_word_is_in_the_code():
    """The words are data (lexicons_data/<lang>.json "commitments"): no string constant in the module holds a promise cue or a time word."""
    tree = ast.parse((SVC / "commitments.py").read_text())
    consts = [n.value.lower() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    # docstrings and the log/SQL strings aside, none of these may appear as code data
    needles = ("remind you", "check back", "tomorrow", "tonight", "this morning", "o'clock", "afternoon", "follow up", "let me know")
    doc_nodes = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                 and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
    bad = [c for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes
           for c in [n.value.lower()] if any(w in c for w in needles)]
    assert bad == [], bad
    assert consts      # (the walk found strings at all)


# ── the user's words are never a promise ─────────────────────────────────────

@pytest.mark.parametrize("user_turn", ["Remind me at 5 about the dentist", "I'll remind you at 5 about the dentist",
                                       "I'll check back tomorrow about the dentist", "please remind me tomorrow morning"])
def test_a_promise_shaped_user_turn_records_nothing_when_zoe_replies_without_a_promise(env, user_turn):
    assert promise(env, "Okay, noted.", message=user_turn) is False
    assert rows(env) == []


def test_extract_is_only_ever_given_the_reply_so_a_user_sentence_cannot_reach_it():
    import inspect
    assert list(inspect.signature(cm.extract).parameters) == ["reply", "now"]
    assert "message" not in inspect.getsource(cm.extract) and "user_turn" not in inspect.getsource(cm.extract)


def test_the_user_turn_is_read_only_by_the_walls_never_for_a_promise(env, monkeypatch):
    """Even a user turn that is nothing but a promise cannot be recorded: the recorder receives the user's turn for the off-record /
    distress walls and nothing else (break-the-fix: feed it the user turn AS the reply and a row appears - so the separation is what holds)."""
    assert promise(env, "Okay.", message="I'll remind you at 5 about the dentist") is False and rows(env) == []
    assert promise(env, "I'll remind you at 5 about the dentist", message="x") is True and len(rows(env)) == 1


# ── walls ────────────────────────────────────────────────────────────────────

def test_an_off_the_record_turn_records_no_promise_and_a_normal_turn_does(env, caplog):
    caplog.set_level(logging.INFO)
    assert promise(env, PROMISE_5, message="Off the record: I have a dentist appointment for my bad tooth") is False
    assert rows(env) == []
    assert any("event=skipped reason=off_record" in m for m in lines_of(caplog))
    assert promise(env, PROMISE_5, message="Remind me about the dentist later", session="s2", reply_id="r2") is True     # the control
    assert len(rows(env)) == 1


def test_a_bare_off_the_record_cue_arms_the_next_turn_and_its_promise_is_not_recorded(env):
    mp.note_user_turn(MEMBER, "off the record", "s1")
    assert mp.claim_turn(MEMBER, "off the record") is False
    assert promise(env, PROMISE_5, message="I have a dentist appointment about my tooth") is False
    assert rows(env) == []


def test_a_distress_turn_records_no_promise(env):
    assert promise(env, "I'm here with you. I'll check back tomorrow.", message="I want to die") is False
    assert rows(env) == []


@pytest.mark.parametrize("guest", ["guest", "voice-guest", "anonymous", "guest-ab12", ""])
def test_a_guest_records_no_promise(env, guest):
    assert promise(env, PROMISE_5, user=guest) is False
    assert rows(env) == []


def test_control_without_the_off_record_wall_the_promise_is_recorded(env, monkeypatch):
    monkeypatch.setattr(mp, "is_off_record", lambda *a, **k: False)
    monkeypatch.setattr(mp, "reply_is_off_record", lambda *a, **k: False)
    assert promise(env, PROMISE_5, message="Off the record: I have a dentist appointment for my bad tooth") is True
    assert len(rows(env)) == 1


# ── recording ────────────────────────────────────────────────────────────────

def test_shadow_is_the_default_records_and_logs_without_words(env, caplog):
    caplog.set_level(logging.INFO)
    assert cm.mode() == "shadow"
    assert promise(env, PROMISE_5, tools=("add_reminder",)) is True
    (r,) = rows(env)
    assert (r["kind"], r["due_at"], r["about"], r["status"], r["lang"], r["user_id"], r["session_id"]) == \
           ("remind", "2026-10-10T17:00:00Z", "about the dentist", "open", "en", MEMBER, "s1")
    assert json.loads(r["tools"]) == ["add_reminder"]
    got = lines_of(caplog)
    assert got and all("dentist" not in m and "remind you" not in m.lower() and "at 5" not in m for m in got)
    assert "event=recorded kind=remind lang=en" in got[0] and "tool=1" in got[0]


def test_off_records_nothing_and_a_flag_value_of_zero_is_off(env, monkeypatch):
    for v in ("off", "0", "false", "no"):
        monkeypatch.setenv(cm.ENV, v)
        assert cm.mode() == "off" and promise(env, PROMISE_5) is False
    assert rows(env) == [] and sweep(DUE_5 + timedelta(hours=1)) == {}


def test_a_garbled_flag_value_is_shadow_and_1_is_enforce(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enfroce")
    assert cm.mode() == "shadow"
    monkeypatch.setenv(cm.ENV, "1")
    assert cm.mode() == "enforce"


def test_recording_the_same_reply_twice_makes_one_row(env):
    promise(env)
    promise(env)
    assert len(rows(env)) == 1


def test_the_same_promise_in_another_conversation_is_its_own_row(env):
    promise(env, session="s1", reply_id="r1")
    promise(env, session="s2", reply_id="r2")
    assert len(rows(env)) == 2


def test_a_missing_table_costs_only_the_record(env):
    env["db"].conn.execute("DROP TABLE commitments")
    assert promise(env) is True          # scheduled; the failed insert is swallowed
    assert sweep(DUE_5 + timedelta(hours=1)) == {}


# ── the due-time check: remind ───────────────────────────────────────────────

def test_a_reminder_that_exists_means_the_promise_was_kept(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env, tools=("add_reminder",))
    reminder(env)                                                      # made during the turn, due at 17:00
    assert sweep(DUE_5 + timedelta(minutes=2)) == {"kept": 1}
    assert rows(env)[0]["status"] == "kept" and rows(env)[0]["resolution"] == "reminder_exists"
    assert rows(env, "SELECT * FROM proactive_candidates") == [] and env["scheduled"] == []


def test_a_reminder_made_by_someone_else_or_for_another_time_does_not_count(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env)
    reminder(env, user=OTHER)                                          # another member's
    reminder(env, due_time="21:00")                                    # the wrong time
    reminder(env, created=NOW - timedelta(days=2))                     # made long before the promise
    assert sweep(DUE_5 + timedelta(minutes=2)) == {"fulfilled": 1}


def test_a_reminder_the_member_deleted_or_acknowledged_still_counts_as_kept(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env)
    reminder(env, deleted=1)
    assert sweep(DUE_5 + timedelta(minutes=2)) == {"kept": 1}
    promise(env, "I'll remind you at 7pm.", session="s2", reply_id="r2")
    reminder(env, due_time="19:00", ack=1)
    assert sweep(datetime(2026, 10, 10, 19, 2, tzinfo=UTC)) == {"kept": 1}


def test_a_missed_reminder_is_made_late_through_the_reminder_path(env, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env, tools=())                                             # she said it and called no tool
    late = DUE_5 + timedelta(minutes=3)
    assert sweep(late) == {"fulfilled": 1}
    (made,) = rows(env, "SELECT * FROM reminders")
    assert made["title"] == "The dentist" and made["user_id"] == MEMBER and made["is_active"] == 1 and made["deleted"] == 0
    assert made["due_date"] == "2026-10-10" and made["due_time"] == "17:04"
    assert env["scheduled"] == [(MEMBER, "The dentist", made["id"])]
    r = rows(env)[0]
    assert (r["status"], r["resolution"], r["reminder_id"]) == ("fulfilled", "reminder_made_late", made["id"])
    assert sweep(late + timedelta(minutes=5)) == {}                    # once
    assert len(rows(env, "SELECT * FROM reminders")) == 1
    assert all("dentist" not in m.lower() for m in lines_of(caplog))


def test_a_stale_miss_is_owned_up_once_in_the_pull_and_never_pushed(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env)
    stale = DUE_5 + timedelta(hours=3)                                 # past the fulfil grace: making it now would be noise
    assert sweep(stale) == {"owned": 1}
    assert rows(env, "SELECT * FROM reminders") == [] and env["scheduled"] == []
    (c,) = rows(env, "SELECT * FROM proactive_candidates")
    assert (c["kind"], c["user_id"], c["cooldown_until"]) == ("commitment", MEMBER, None)
    assert c["text"] == "I said I'd remind you about the dentist and didn't - want me to now?"
    assert rows(env)[0]["status"] == "owned"
    env["now"] = stale + timedelta(minutes=1)
    res = run(pull.pull(MEMBER, "s9", channel="chat", now=env["now"]))
    assert res.delivered == 1 and res.reply == "I said I'd remind you about the dentist and didn't - want me to now?"
    again = run(pull.pull(MEMBER, "s9", channel="chat", now=env["now"] + timedelta(minutes=1)))
    assert again.reply == pull.EMPTY_REPLY                             # said ONCE
    assert sweep(stale + timedelta(minutes=10)) == {"surfaced": 1}
    assert rows(env)[0]["status"] == "surfaced"
    assert sweep(stale + timedelta(hours=1)) == {}


def test_a_miss_with_no_subject_is_owned_up_not_guessed(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env, "I'll remind you at 5.")
    assert sweep(DUE_5 + timedelta(minutes=3)) == {"owned": 1}         # in grace, but nothing to title a reminder with
    assert rows(env, "SELECT text FROM proactive_candidates")[0]["text"] == "I said I'd remind you and didn't - want me to now?"


def test_shadow_checks_and_logs_but_makes_nothing(env, caplog):
    caplog.set_level(logging.INFO)
    promise(env)
    assert cm.mode() == "shadow"
    assert sweep(DUE_5 + timedelta(minutes=3)) == {"would_fulfil": 1}
    assert rows(env)[0]["status"] == "missed" and rows(env)[0]["resolution"] == "shadow:would_fulfil"
    assert rows(env, "SELECT * FROM reminders") == [] and rows(env, "SELECT * FROM proactive_candidates") == []
    assert env["scheduled"] == []
    assert any("event=check kind=remind verdict=would_fulfil" in m for m in lines_of(caplog))
    promise(env, "I'll remind you at 9pm about the bins.", session="s2", reply_id="r2")
    assert sweep(datetime(2026, 10, 10, 23, 0, tzinfo=UTC)) == {"would_ownup": 1}


def test_a_promise_is_not_checked_before_it_is_due(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env)
    assert sweep(DUE_5 - timedelta(minutes=1)) == {} and rows(env)[0]["status"] == "open"


def test_two_sweeps_at_once_act_once(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env)

    async def both():
        return await asyncio.gather(cm.sweep(now=DUE_5 + timedelta(minutes=3)), cm.sweep(now=DUE_5 + timedelta(minutes=3)))
    a, b = run(both())
    assert (a.get("fulfilled", 0) + b.get("fulfilled", 0)) == 1 and len(rows(env, "SELECT * FROM reminders")) == 1


def test_control_without_the_due_check_the_broken_promise_goes_unnoticed(env, monkeypatch):
    """Break-the-fix: stub the check and nothing is fulfilled or owned up - which is exactly the pre-0044 behaviour."""
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env)

    async def blind(*a, **k):
        return ""
    monkeypatch.setattr(cm, "_check_remind", blind)
    assert sweep(DUE_5 + timedelta(minutes=3)) == {}
    assert rows(env)[0]["status"] == "open" and rows(env, "SELECT * FROM reminders") == [] \
        and rows(env, "SELECT * FROM proactive_candidates") == []


# ── the due-time check: check back ───────────────────────────────────────────

def test_a_check_back_is_armed_in_the_pull_queue_held_until_due_and_delivered_once(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env, "I'll check back tomorrow about the dentist.")
    due = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
    (c,) = rows(env, "SELECT * FROM proactive_candidates")
    assert c["kind"] == "commitment" and c["cooldown_until"] == "2026-10-11T09:00:00Z"
    assert c["text"] == "I said I'd check back with you about the dentist - how's it going?"
    early = run(pull.pull(MEMBER, "s9", channel="chat", now=NOW + timedelta(hours=1)))
    assert early.reply == pull.EMPTY_REPLY                             # not before she said she would
    env["now"] = due + timedelta(minutes=30)
    got = run(pull.pull(MEMBER, "s9", channel="chat", now=env["now"]))
    assert got.delivered == 1 and "check back with you about the dentist" in got.reply
    assert sweep(due + timedelta(minutes=35)) == {"kept": 1} and rows(env)[0]["resolution"] == "pulled"
    assert run(pull.pull(MEMBER, "s9", channel="chat", now=due + timedelta(minutes=40))).reply == pull.EMPTY_REPLY


def test_a_check_back_nobody_asked_for_closes_void_after_the_patience_window(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env, "I'll check back tomorrow about the dentist.")
    due = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
    assert sweep(due + timedelta(hours=1)) == {}                       # still waiting to be asked
    assert rows(env)[0]["status"] == "open"
    assert sweep(due + timedelta(seconds=cm.CHECKBACK_PATIENCE_S + 600)) == {"void": 1}
    assert (rows(env)[0]["status"], rows(env)[0]["resolution"]) == ("void", "never_asked")
    assert rows(env, "SELECT expires_at FROM proactive_candidates")[0]["expires_at"] <= "2026-10-12T09:11:00Z"


def test_shadow_does_not_arm_a_check_back(env):
    promise(env, "I'll check back tomorrow about the dentist.")
    assert rows(env, "SELECT * FROM proactive_candidates") == []
    assert sweep(datetime(2026, 10, 11, 10, 0, tzinfo=UTC)) == {"would_arm": 1}


def test_the_commitment_candidate_is_left_alone_by_the_nightly_selector_and_the_raise(env, monkeypatch):
    """The nightly pass expires every candidate it did not re-select; a commitment candidate is not its to expire, and the brain
    never raises it in its own words (the pull delivers Zoe's stored sentence)."""
    monkeypatch.setenv(cm.ENV, "enforce")
    promise(env, "I'll check back tomorrow about the dentist.")
    env["db"].conn.execute("CREATE TABLE open_loops (id INTEGER PRIMARY KEY, user_id TEXT, loop_text TEXT, follow_up_hint TEXT, "
                           "emotional_weight INTEGER, created_at TEXT, follow_up_after TEXT, resolved BOOLEAN DEFAULT FALSE, resolved_at TEXT)")
    env["db"].conn.execute("CREATE TABLE events (id TEXT, user_id TEXT, title TEXT, start_date TEXT, start_time TEXT, deleted INTEGER DEFAULT 0)")
    import memory_service

    class _Mem:
        async def load_recent_for_prompt(self, user_id, **kw):
            return []
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Mem())
    env["now"] = NOW + timedelta(hours=2)
    run(sel.select_for_user(MEMBER, now=env["now"]))
    (c,) = rows(env, "SELECT expires_at FROM proactive_candidates")
    assert c["expires_at"] > "2026-10-11T09:00:00Z"                    # not expired by the nightly pass
    assert run(sel._load(MEMBER)) == []                                # and not in the raise path's rows


def test_control_the_nightly_pass_would_expire_an_unexempted_candidate(env):
    """Break-the-fix: a candidate of any OTHER kind that the nightly pass did not re-select IS expired by the same statement."""
    stamp = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    env["db"].conn.execute(
        "INSERT INTO proactive_candidates (id, user_id, kind, source_ref, text, salience, expires_at, created_at, updated_at) "
        "VALUES ('x', ?, 'open_loop', 'open_loops:1', 't', 0.5, '2030-01-01T00:00:00Z', ?, '2026-10-09T00:00:00Z')", (MEMBER, stamp))
    env["db"].conn.execute(
        "INSERT INTO proactive_candidates (id, user_id, kind, source_ref, text, salience, expires_at, created_at, updated_at) "
        "VALUES ('y', ?, 'commitment', 'commitments:1', 't', 0.5, '2030-01-01T00:00:00Z', ?, '2026-10-09T00:00:00Z')", (MEMBER, stamp))
    env["db"].conn.commit()
    import re
    src = (SVC / "proactive/selector.py").read_text()
    stmt = re.search(r'"UPDATE proactive_candidates SET expires_at = \? WHERE user_id = \? AND updated_at < \? "\s*"AND expires_at > \? AND kind <> \'commitment\'"', src)
    assert stmt, "the nightly expiry no longer exempts commitments"
    env["db"].conn.execute("UPDATE proactive_candidates SET expires_at = ? WHERE user_id = ? AND updated_at < ? AND expires_at > ? "
                           "AND kind <> 'commitment'", (stamp, MEMBER, stamp, stamp))
    left = {r["id"]: r["expires_at"] for r in rows(env, "SELECT id, expires_at FROM proactive_candidates")}
    assert left["x"] == stamp and left["y"] == "2030-01-01T00:00:00Z"


# ── forgetting, wiring, retention ────────────────────────────────────────────

def test_forgetting_a_person_drops_the_promises_that_name_them(env):
    import memory_forget_cascade as fc
    promise(env, "I'll remind you at 5 to call Marisol.")
    promise(env, "I'll remind you at 9pm about the bins.", session="s2", reply_id="r2")
    assert len(rows(env)) == 2
    assert run(cm.erase_entity(MEMBER, fc._name_re("Marisol"))) == 1
    assert [r["about"] for r in rows(env)] == ["about the bins"]


def test_closed_rows_are_purged_after_the_retention_and_open_ones_never(env, monkeypatch):
    monkeypatch.setenv(cm.ENV, "enforce")
    monkeypatch.setattr(cm, "RETENTION_S", 3600)
    promise(env)
    reminder(env)
    assert sweep(DUE_5 + timedelta(minutes=2)) == {"kept": 1}
    promise(env, "I'll remind you on Friday at 9am about the bins.", session="s2", reply_id="r2")      # still open, due in 6 days
    cm._last_purge = 0.0
    sweep(DUE_5 + timedelta(hours=2))
    assert [(r["about"], r["status"]) for r in rows(env)] == [("about the bins", "open")]


def test_brain_dispatch_records_from_the_reply_stream_with_the_tool_names(env):
    import brain_dispatch

    async def inner():
        yield '__TOOL__:{"phase": "start", "id": "a", "name": "add_reminder"}'
        yield "Sure, I'll remind you "
        yield "at 5 about the dentist."

    async def go():
        mp.note_user_turn(MEMBER, "remind me at five about the dentist", "s1")
        out = [d async for d in brain_dispatch._provenance_tracked_stream(inner(), "remind me at five about the dentist", "s1", MEMBER)]
        await cm.flush()
        return out
    out = run(go())
    assert out[1:] == ["Sure, I'll remind you ", "at 5 about the dentist."]
    (r,) = rows(env)
    assert json.loads(r["tools"]) == ["add_reminder"] and r["kind"] == "remind" and r["about"] == "about the dentist"


def test_the_stream_is_the_very_object_when_both_bookkeepers_are_off(env, monkeypatch):
    import brain_dispatch

    async def s():
        yield "hi"
    monkeypatch.setenv(cm.ENV, "off")
    monkeypatch.setenv("ZOE_MEMORY_PROVENANCE_ANSWERS", "0")
    stream = s()
    assert brain_dispatch._provenance_tracked_stream(stream, "hi", "s1", MEMBER) is stream


def test_control_without_the_guards_an_offer_or_a_condition_becomes_a_promise(monkeypatch):
    """Break-the-fix (in the test): empty the lexicon's guards and the offers the extraction tests refuse ARE recorded."""
    import dataclasses
    real = cm._lex
    monkeypatch.setattr(cm, "_lex", lambda lang: (lambda lx: dataclasses.replace(lx, guards=()) if lx else None)(real(lang)))
    assert ex("If you like, I'll remind you at 5.") != [] and ex("Let me know and I'll remind you at 5.") != []


def test_control_without_the_negators_a_refusal_becomes_a_promise(monkeypatch):
    import dataclasses
    real = cm._lex
    monkeypatch.setattr(cm, "_lex", lambda lang: (lambda lx: dataclasses.replace(lx, negators=()) if lx else None)(real(lang)))
    assert ex("I'll remind you not to forget your keys at 5.") != []        # with the negators this is refused (the negative list below)


def test_control_a_user_sentence_handed_to_the_extractor_is_a_promise(env):
    """The separation is what holds: handed the user's words as if they were Zoe's reply, the same extractor records them."""
    assert [p.kind for p in cm.extract("I'll remind you at 5 about the dentist", now=NOW)] == ["remind"]


def test_control_without_the_wall_a_distress_turn_records_its_promise(env, monkeypatch):
    monkeypatch.setattr(mp, "_distress", lambda text: False)
    monkeypatch.setattr(mp, "is_off_record", lambda *a, **k: False)
    monkeypatch.setattr(mp, "reply_is_off_record", lambda *a, **k: False)
    assert promise(env, "I'm here with you. I'll check back tomorrow.", message="I want to die") is True


def test_a_brain_fallback_reply_promises_nothing(env):
    import brain_dispatch
    from zoe_flue_client import _FALLBACK_TEXT
    brain_dispatch._provenance_commit(MEMBER, _FALLBACK_TEXT, "hi", "s1", (0, 0.0), ())
    assert rows(env) == []
