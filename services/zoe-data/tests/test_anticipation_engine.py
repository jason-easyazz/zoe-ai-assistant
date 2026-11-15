"""The anticipation engine on the store: facts are read through the shared calendar reader, the rule pack's offers live in the
phase 1 rows (``pending_suggestions``), and created -> surfaced -> accepted / declined / expired runs through phase 1's code.
Real sqlite; placeholder household (adults ``ava`` and ``ben``, child ``kit``, guest ``remy``).

Negative controls (run 2026-11-15 on mutated copies of the tree, in groups; each turned the named test red):
  * drop ``if (o.rule, _source_event(o.sources)) not in taken`` -> test_the_pack_is_stored_in_the_same_rows_once;
  * store in ``shadow`` (drop ``if m != "on": return``) -> test_shadow_logs_counts_and_rule_ids_only;
  * drop ``if _quiet_hours(now): return None`` -> test_no_offer_in_quiet_hours_and_never_stacked;
  * drop the ``_other_offer_open`` check -> test_no_offer_in_quiet_hours_and_never_stacked;
  * make ``_still_holds`` return True -> test_an_offer_whose_situation_is_gone_is_withdrawn_not_said;
  * make ``erase_entity`` delete nothing -> test_a_forget_removes_derived_offers_in_any_state;
  * restore ``resolved = 0`` only in ``pending_suggestions.withdraw_matching`` -> test_a_topic_forget_reaches_answered_offers;
  * the privacy test carries its own control: with BOTH locks broken it must see the leak.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anticipation as ant  # noqa: E402
import anticipation_situations as S  # noqa: E402
import calendar_read  # noqa: E402

pytestmark = pytest.mark.ci_safe

TZ = ZoneInfo("Australia/Perth")
AVA, BEN, KIT, REMY = "ava", "ben", "kit", "remy"
NIGHT = datetime(2026, 11, 9, 3, 0, tzinfo=TZ)              # Monday, the night pass
MORNING = NIGHT.replace(hour=9)
SECRET = ("Zebracorn", "Quillfen")
EVENTS = [
    # id, owner, title, date, start, end, place, all_day, visibility, recurring
    ("dentist", AVA, "Dentist", "2026-11-09", "10:00", "", "12 Example Street", 0, "family", ""),
    ("secret", AVA, "Zebracorn specialist", "2026-11-09", "15:00", "16:00", "Quillfen Clinic", 0, "personal", ""),
    ("bins", AVA, "Bins out", "2026-11-03", "", "", "", 1, "family", "weekly"),
    ("early", AVA, "Breakfast meeting", "2026-11-10", "07:30", "", "", 0, "family", ""),
    ("swim", KIT, "Swimming", "2026-11-09", "15:30", "16:15", "", 0, "family", ""),
    ("site", BEN, "Site visit", "2026-11-09", "15:00", "17:00", "", 0, "family", ""),
]
AVA_RULES = {"alarm_for_event": 1, "birthday_reminder": 1, "child_cover": 1, "chore_night_before": 1}


def run(coro):
    return asyncio.run(coro)


def _migrate_alarms(path: str) -> None:
    spec = importlib.util.spec_from_file_location("mig_0063", ROOT / "alembic/versions/0063_alarms.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()
    engine.dispose()


async def _schema(db) -> None:
    for sql in (
        "CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)",
        "CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, start_date TEXT, start_time TEXT, end_date TEXT,"
        " end_time TEXT, location TEXT, all_day INTEGER DEFAULT 0, visibility TEXT DEFAULT 'family', recurring TEXT, rrule TEXT,"
        " rdates TEXT, exdates TEXT, recurrence_id TEXT, series_id TEXT, status TEXT, category TEXT, source TEXT, read_only INTEGER,"
        " calendar_id TEXT, timezone TEXT, end_timezone TEXT, deleted INTEGER DEFAULT 0)",
        "CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL, action_type TEXT"
        " NOT NULL, description TEXT NOT NULL, list_type TEXT, when_hint TEXT, amount_hint TEXT, offer_phrase TEXT NOT NULL,"
        " pre_filled_slots TEXT, created_at TEXT NOT NULL, turns_elapsed INTEGER NOT NULL DEFAULT 0, expire_after_turns INTEGER"
        " NOT NULL DEFAULT 2, resolved INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE reminders (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, due_date TEXT, due_time TEXT, is_active INTEGER"
        " DEFAULT 1, acknowledged INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0)",
        "CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT, user_id TEXT, circle TEXT, birthday TEXT, deleted INTEGER DEFAULT 0)",
        "CREATE TABLE person_important_dates (person_id TEXT, date_type TEXT, month INTEGER, day INTEGER, reminder_days_before INTEGER)",
    ):
        await db.execute(sql)
    for uid, role in ((AVA, "member"), (BEN, "member"), (KIT, "member"), (REMY, "guest")):
        await db.execute("INSERT INTO users (id, name, role) VALUES (?, ?, ?)", (uid, uid.capitalize(), role))
    for e in EVENTS:
        await db.execute("INSERT INTO events (id, user_id, title, start_date, start_time, end_time, location, all_day, visibility,"
                         " recurring) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", e)
    await db.execute("INSERT INTO people (id, name, user_id) VALUES ('p-noa', 'Noa Example', ?)", (AVA,))
    await db.execute("INSERT INTO person_important_dates VALUES ('p-noa', 'birthday', 11, 12, 7)")
    await db.commit()


@pytest.fixture
def dbpath(tmp_path):
    p = str(tmp_path / "e.db")
    _migrate_alarms(p)

    async def go():
        async with aiosqlite.connect(p) as db:
            await _schema(db)

    run(go())
    return p


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setenv("ZOE_ANTICIPATION", "on")
    for name in ("ZOE_ALARMS", "ZOE_OUTCOME_EDGES", "ZOE_QUIET_START_HOUR", "ZOE_QUIET_END_HOUR"):
        monkeypatch.delenv(name, raising=False)

    async def adult(uid, db=None):
        return uid in (AVA, BEN)

    monkeypatch.setattr(ant, "_is_adult", adult)
    monkeypatch.setattr(ant, "is_member", lambda uid: uid in (AVA, BEN, KIT))
    ant.reset_state()
    yield
    ant.reset_state()


async def _with(path, fn):
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        return await fn(db)


def _by_rule(rows):
    return {r["kind"]: r for r in rows if r["slots"].get("engine")}


# -- created -----------------------------------------------------------------------------------------------------------------------
def test_shadow_logs_counts_and_rule_ids_only(dbpath, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_ANTICIPATION", "shadow")
    caplog.set_level("INFO", logger="anticipation")

    async def go(db):
        return await ant.generate_for_user(AVA, now=NIGHT, db=db), await ant._rows(db, AVA)

    result, rows = run(_with(dbpath, go))
    assert result["rules"] == AVA_RULES and result["stored"] == 0 and rows == []
    assert "rules=alarm_for_event:1,birthday_reminder:1,child_cover:1,chore_night_before:1" in caplog.text
    for word in ("Dentist", "Bins", "Noa", "Swimming", "Breakfast", "Example") + SECRET:
        assert word not in caplog.text


def test_the_pack_is_stored_in_the_same_rows_once(dbpath):
    async def go(db):
        first = await ant.generate_for_user(AVA, now=NIGHT, db=db)
        again = await ant.generate_for_user(AVA, now=NIGHT + timedelta(hours=1), db=db)
        return first, again, await ant._rows(db, AVA)

    first, again, rows = run(_with(dbpath, go))
    assert first["rules"] == AVA_RULES and first["stored"] == 6 and again["stored"] == 0      # 2 phase-1 leave-bys + 4 from the pack
    # the dentist and the specialist each get ONE leave-by (phase 1's): the pack's twin for the same event is not stored
    assert sorted(r["kind"] for r in rows) == ["alarm_for_event", "birthday_reminder", "child_cover", "chore_night_before", "leave", "leave"]
    engine = _by_rule(rows)
    bins = engine["chore_night_before"]
    assert bins["title"] == "Bins out" and bins["slots"]["tool"] == "reminder.create"
    assert bins["slots"]["args"] == {"title": "Bins out", "due_date": "2026-11-09", "due_time": "19:00"}
    assert engine["alarm_for_event"]["slots"]["args"] == {"time_hhmm": "06:00", "once_date": "2026-11-10", "label": "Early start"}
    assert all(r["resolved"] == ant.OPEN and r["key"].startswith(r["kind"] + ":") for r in engine.values())


def test_a_broken_pack_never_costs_phase_1_its_offers(dbpath, monkeypatch):
    async def boom(conn, uid, now):
        raise RuntimeError("no such column")

    monkeypatch.setattr(ant, "_engine_offers", boom)

    async def go(db):
        return await ant.generate_for_user(AVA, now=NIGHT, db=db), await ant._rows(db, AVA)

    result, rows = run(_with(dbpath, go))
    assert result["status"] == "ok" and result["rules"] == {"error": 1} and sorted(r["kind"] for r in rows) == ["leave", "leave"]


def test_a_private_event_reaches_only_its_owner_and_two_locks_hold_it(dbpath, monkeypatch):
    async def ben_blob(db):
        await ant.generate_for_user(BEN, now=NIGHT, db=db)
        rows = await ant._rows(db, BEN)
        await db.execute("DELETE FROM pending_suggestions")
        await db.commit()
        # the ENGINE rows only: phase 1 reads through the shared reader alone, so breaking the reader breaks its single lock
        return json.dumps([[r["title"], r["line"], r["key"], r["slots"]] for r in rows if r["slots"].get("engine")])

    def leaks(blob):
        return "secret" in blob or any(w in blob for w in SECRET)

    assert not leaks(run(_with(dbpath, ben_blob)))                       # as shipped: Ben's offers hold nothing of her event
    real_view = S.view
    monkeypatch.setattr(S, "view", lambda facts, person: facts)         # lock 1 broken, the reader still never loads it
    assert not leaks(run(_with(dbpath, ben_blob)))
    monkeypatch.setattr(S, "view", real_view)
    monkeypatch.setattr(calendar_read, "_visibility_sql", lambda *a: ("1 = 1", []))   # lock 2 broken, the view still drops it
    assert not leaks(run(_with(dbpath, ben_blob)))
    monkeypatch.setattr(S, "view", lambda facts, person: facts)         # both broken: the leak this test exists to see
    assert leaks(run(_with(dbpath, ben_blob)))


# -- surfaced ----------------------------------------------------------------------------------------------------------------------
def test_surfaced_best_first_one_a_turn_two_a_day(dbpath):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        a = await ant.pick_for_turn(AVA, "chat-1", now=MORNING, db=db)
        b = await ant.pick_for_turn(AVA, "chat-2", now=MORNING + timedelta(minutes=5), db=db)
        c = await ant.pick_for_turn(AVA, "chat-3", now=MORNING + timedelta(minutes=10), db=db)
        return a, b, c

    a, b, c = run(_with(dbpath, go))
    assert a.kind == "child_cover" and a.text == ("By the way - Every adult looks busy when Kit's 3:30 pm thing starts today - "
                                                   "want a reminder at 1:30 pm to sort out who takes them?")
    assert b.kind == "leave" and c is None                              # priority 85, then 70; the third waits for another day
    assert ant.card(a)["actions"][0] == {"label": "Remind me", "action": "pending_suggestion_accept", "suggestion_id": a.id}


def test_no_offer_in_quiet_hours_and_never_stacked(dbpath, monkeypatch):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        quiet = await ant.pick_for_turn(AVA, "chat-1", now=NIGHT.replace(hour=6, minute=30), db=db)     # household quiet: 22:00-07:00
        await db.execute("INSERT INTO pending_suggestions (id, user_id, session_id, action_type, description, offer_phrase, created_at)"
                         " VALUES ('other', ?, 'chat-2', 'person_create', 'x', 'Save this?', '2026-11-09T00:00:00Z')", (AVA,))
        await db.commit()
        stacked = await ant.pick_for_turn(AVA, "chat-2", now=MORNING, db=db)
        free = await ant.pick_for_turn(AVA, "chat-3", now=MORNING, db=db)
        return quiet, stacked, free

    quiet, stacked, free = run(_with(dbpath, go))
    assert quiet is None and stacked is None and free is not None


def test_an_offer_whose_situation_is_gone_is_withdrawn_not_said(dbpath):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        await db.execute("UPDATE events SET deleted = 1 WHERE id IN ('swim', 'dentist', 'secret')")
        await db.commit()
        s = await ant.pick_for_turn(AVA, "chat-1", now=MORNING, db=db)
        return s, _by_rule(await ant._rows(db, AVA))

    s, engine = run(_with(dbpath, go))
    assert s.kind == "chore_night_before"                               # the next thing still true
    assert (engine["child_cover"]["resolved"], engine["child_cover"]["slots"]["outcome"]) == (ant.WITHDRAWN, "withdrawn")


def test_an_offer_past_its_moment_expires(dbpath):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        s = await ant.pick_for_turn(AVA, "chat-1", now=NIGHT.replace(hour=20), db=db)        # 8 pm: the 1:30 pm reminder is pointless now
        return s, _by_rule(await ant._rows(db, AVA))

    s, engine = run(_with(dbpath, go))
    assert s is not None and s.kind == "alarm_for_event"
    assert (engine["child_cover"]["resolved"], engine["child_cover"]["slots"]["outcome"]) == (ant.WITHDRAWN, "expired")


# -- accepted / declined -------------------------------------------------------------------------------------------------------------
def test_yes_runs_the_exact_action_and_keeps_a_receipt(dbpath, monkeypatch):
    import reminder_service

    calls = []

    async def fake(payload, *, user, db, now_utc=None, schedule=True):
        calls.append((payload.title, payload.due_date, payload.due_time, payload.visibility, user["user_id"]))
        return {"id": "rem-1"}

    monkeypatch.setattr(reminder_service, "create_reminder_record", fake)

    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        s = await ant.pick_for_turn(AVA, "chat-1", now=MORNING, db=db)
        reply = await ant.answer(AVA, s.id, True, now=MORNING, db=db)
        return reply, await ant.answer(AVA, s.id, True, now=MORNING, db=db), _by_rule(await ant._rows(db, AVA))["child_cover"]

    reply, twice, row = run(_with(dbpath, go))
    assert calls == [("Sort out who takes Kit at 3:30 pm", "2026-11-09", "13:30", "personal", AVA)]
    assert reply == "Done - I'll remind you at 1:30 pm today." and twice == ant.STALE
    assert row["resolved"] == ant.DONE and row["slots"]["outcome"] == "acted" and row["slots"]["receipt"] == {"reminder_id": "rem-1"}


def test_yes_to_an_alarm_offer_sets_a_one_off_alarm_only_when_alarms_are_on(dbpath, monkeypatch):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        row = _by_rule(await ant._rows(db, AVA))["alarm_for_event"]
        off = await ant.answer(AVA, row["id"], True, now=MORNING, db=db)
        none = await ant._all(db, "SELECT COUNT(*) FROM alarms", ())
        await db.execute("UPDATE pending_suggestions SET resolved = 0 WHERE id = ?", (row["id"],))
        await db.commit()
        monkeypatch.setenv("ZOE_ALARMS", "on")
        on = await ant.answer(AVA, row["id"], True, now=MORNING, db=db)
        return off, none[0][0], on, await ant._all(db, "SELECT user_id, time_hhmm, repeat_rule, once_date FROM alarms", ())

    off, none, on, alarms_now = run(_with(dbpath, go))
    assert off == ant.ALARMS_OFF and none == 0
    assert on == "Done - a one-off alarm for 6:00 am tomorrow. Your usual alarm stays as it is."
    assert [tuple(r) for r in alarms_now] == [(AVA, "06:00", "once", "2026-11-10")]


def test_a_declined_offer_is_never_made_or_said_again(dbpath):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        s = await ant.pick_for_turn(AVA, "chat-1", now=MORNING, db=db)
        reply = await ant.answer(AVA, s.id, False, now=MORNING, db=db)
        cooling = await ant.pick_for_turn(AVA, "chat-2", now=MORNING + timedelta(hours=1), db=db)
        again = await ant.generate_for_user(AVA, now=MORNING + timedelta(hours=2), db=db)
        rows = await ant._rows(db, AVA)
        later = [await ant.pick_for_turn(AVA, f"chat-{i}", now=MORNING + timedelta(hours=27 + i), db=db) for i in range(3)]
        return s, reply, cooling, again, rows, later

    s, reply, cooling, again, rows, later = run(_with(dbpath, go))
    assert s.kind == "child_cover" and reply == ant.DECLINED_REPLY and cooling is None        # a no: nothing more for 24 hours
    assert again["stored"] == 0 and [r["resolved"] for r in rows if r["kind"] == "child_cover"] == [ant.DECLINED]
    assert all(x is None or x.kind != "child_cover" for x in later)


# -- forgotten ---------------------------------------------------------------------------------------------------------------------
def test_a_forget_removes_derived_offers_in_any_state(dbpath):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        rows = _by_rule(await ant._rows(db, AVA))
        await ant.answer(AVA, rows["birthday_reminder"]["id"], False, now=MORNING, db=db)      # answered: now a learning vote
        person = await ant.erase_entity(db, AVA, re.compile(r"\bnoa\b", re.IGNORECASE))
        topic = await ant.erase_entity(db, AVA, re.compile(r"\bbins\b", re.IGNORECASE))
        return person, topic, sorted(r["kind"] for r in await ant._rows(db, AVA))

    person, topic, left = run(_with(dbpath, go))
    assert (person, topic) == (1, 1) and left == ["alarm_for_event", "child_cover", "leave", "leave"]


def test_a_topic_forget_reaches_answered_offers(dbpath, monkeypatch):
    import pending_suggestions

    class Pg:                                   # the asyncpg face ``withdraw_matching`` talks to, over the test's sqlite
        def __init__(self, db):
            self.db = db

        async def fetch(self, sql, *args):
            cur = await self.db.execute(re.sub(r"\$\d+", "?", sql), args)
            return await cur.fetchall()

        async def execute(self, sql, *args):
            await self.db.execute(re.sub(r"\$\d+", "?", sql), args)
            await self.db.commit()

    async def go(db):
        @contextlib.asynccontextmanager
        async def ctx():
            yield Pg(db)

        monkeypatch.setattr(pending_suggestions, "get_db_ctx", ctx)
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        bins = _by_rule(await ant._rows(db, AVA))["chore_night_before"]
        await ant.answer(AVA, bins["id"], False, now=MORNING, db=db)                           # declined: resolved = 2
        gone = await pending_suggestions.withdraw_matching(AVA, lambda text: "bins" in text.lower())
        return gone, [r["kind"] for r in await ant._rows(db, AVA)]

    gone, left = run(_with(dbpath, go))
    assert gone == 1 and "chore_night_before" not in left and len(left) == 5


# -- learning and the member's own controls (anticipation_learning over the stored rows) -------------------------------------------
# Negative controls (run 2026-11-15). NOT caught: dropping ``and not tune(r["kind"]).quiet`` in pick_for_turn - the learned priority
# shift and the re-check already keep a quiet rule's open offer from being said, so that filter is a second lock no test isolates.
# Caught:
# call ``anticipation_rules.offers`` without ``tune=`` -> test_three_noes_in_a_row_silence_a_rule_and_why_is_exact; without
# ``disabled=`` -> test_a_member_can_switch_a_rule_off_and_on; sort by the frozen ``slots["priority"]`` -> test_yeses_move_a_rule_up_the_order.
async def _votes(db, rule, outcome, when, n=3):
    """``n`` answered offers of ``rule`` (the rows the learning loop counts), a minute apart from ``when``."""
    for i in range(n):
        slots = {"outcome": outcome, "answered_at": ant._iso(when + timedelta(minutes=i))}
        await db.execute("INSERT INTO pending_suggestions (id, user_id, session_id, action_type, description, list_type, amount_hint,"
                         " offer_phrase, pre_filled_slots, created_at, resolved) VALUES (?, ?, 'anticipation', 'anticipation', ?, ?, ?, 'x', ?, ?, ?)",
                         (f"vote-{rule}-{i}", AVA, f"Yesteryear {rule}", rule, f"{rule}:old:{i}", json.dumps(slots), ant._iso(when),
                          ant.DECLINED if outcome == "dismissed" else ant.DONE))
    await db.commit()


def test_three_noes_in_a_row_silence_a_rule_and_why_is_exact(dbpath):
    async def go(db):
        await _votes(db, "child_cover", "dismissed", NIGHT - timedelta(days=3))
        return await ant.generate_for_user(AVA, now=NIGHT, db=db), await ant.explain(AVA, "child_cover", now=NIGHT, db=db)

    result, why = run(_with(dbpath, go))
    assert result["rules"] == {k: v for k, v in AVA_RULES.items() if k != "child_cover"}
    assert (why["status"], why["dismissed"], why["no_run"], why["quiet_until"]) == ("quiet", 3, 3, "2027-01-05")
    assert why["why"] == "You said no 3 times in a row (the last on 2026-11-06), so I stopped offering it. I'll offer it once more after 2027-01-05."


def test_a_forgotten_offer_takes_its_vote_with_it(dbpath):
    async def go(db):
        await _votes(db, "child_cover", "dismissed", NIGHT - timedelta(days=3))
        gone = await ant.erase_entity(db, AVA, re.compile(r"\byesteryear\b", re.IGNORECASE))
        return gone, await ant.generate_for_user(AVA, now=NIGHT, db=db)

    gone, result = run(_with(dbpath, go))
    assert gone == 3 and result["rules"] == AVA_RULES                    # the votes are gone, so the rule offers again


def test_an_open_offer_of_a_rule_gone_quiet_is_not_said(dbpath):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        await _votes(db, "child_cover", "dismissed", NIGHT - timedelta(days=2))
        return [await ant.pick_for_turn(AVA, f"chat-{i}", now=MORNING + timedelta(minutes=i), db=db) for i in range(2)]

    said = run(_with(dbpath, go))                                        # both of the day's two: neither is the quiet rule's
    assert said[0].kind == "leave" and all(s is None or s.kind != "child_cover" for s in said)


def test_yeses_move_a_rule_up_the_order(dbpath):
    async def go(db):
        await _votes(db, "leave", "acted", NIGHT - timedelta(days=2))
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        return await ant.pick_for_turn(AVA, "chat-1", now=MORNING, db=db), await ant.explain(AVA, "leave", now=MORNING, db=db)

    s, why = run(_with(dbpath, go))
    assert s.kind == "leave" and (why["status"], why["acted"], why["priority"], why["lead_h"]) == ("offering", 3, 98, 18)   # 70 + 28 > child_cover's 85


def test_a_member_can_switch_a_rule_off_and_on(dbpath):
    async def go(db):
        await ant.generate_for_user(AVA, now=NIGHT, db=db)
        off = await ant.set_rule(AVA, "chore_night_before", False, now=MORNING, db=db)
        row = _by_rule(await ant._rows(db, AVA))["chore_night_before"]
        again = await ant.generate_for_user(AVA, now=MORNING, db=db)
        said = [await ant.pick_for_turn(AVA, f"chat-{i}", now=MORNING + timedelta(days=i), db=db) for i in range(2)]
        untouched = await ant.erase_entity(db, AVA, re.compile(r"rule_off|chore_night"))        # the control row holds no words
        still_off = (await ant.explain(AVA, "chore_night_before", now=MORNING, db=db))["status"]
        ben = (await ant.explain(BEN, "chore_night_before", now=MORNING, db=db))["status"]
        on = await ant.set_rule(AVA, "chore_night_before", True, now=MORNING, db=db)
        return off, row, again, said, untouched, still_off, ben, on, await ant.set_rule(AVA, "no_such_rule", False, db=db)

    off, row, again, said, untouched, still_off, ben, on, unknown = run(_with(dbpath, go))
    assert off["status"] == "off" and off["why"] == "You switched this kind of offer off."
    assert (row["resolved"], row["slots"]["outcome"]) == (ant.WITHDRAWN, "switched_off")
    assert "chore_night_before" not in again["rules"] and all(s is None or s.kind != "chore_night_before" for s in said)
    assert untouched == 1 and still_off == "off" and ben == "offering"   # 1 = the withdrawn bins OFFER (its slots name the rule), not the switch
    assert on["status"] == "offering" and unknown is None


def test_the_controls_do_no_io_when_off(monkeypatch):
    monkeypatch.setenv("ZOE_ANTICIPATION", "off")

    def no_conn(db=None):
        raise AssertionError("flag off must not touch the store")

    monkeypatch.setattr(ant, "_conn", no_conn)
    assert run(ant.explain(AVA)) is None and run(ant.set_rule(AVA, "leave", False)) is None


def test_the_two_routes_are_404_while_the_flag_is_off(monkeypatch):
    from fastapi import HTTPException

    from routers import proactive as routes

    async def allowed(db, user, **kw):
        return None

    def no_conn(db=None):
        raise AssertionError("flag off must not touch the store")

    monkeypatch.setattr(routes, "require_feature_access", allowed)
    monkeypatch.setattr(ant, "_conn", no_conn)
    monkeypatch.setenv("ZOE_ANTICIPATION", "off")
    for call in (routes.anticipation_rules_why(user={"user_id": AVA}, db=None),
                 routes.anticipation_rule_switch("leave", routes.AnticipationRuleBody(enabled=False), user={"user_id": AVA}, db=None)):
        with pytest.raises(HTTPException) as err:
            run(call)
        assert err.value.status_code == 404
