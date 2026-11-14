"""anticipation (phase 1, flag-dark ``ZOE_ANTICIPATION``): calendar -> offers at night -> one "By the way" on the member's own turn ->
yes runs the real tool / no cools down. Real sqlite (the alarms migration + the tables the module touches). Neutral fixtures only.

Negative controls (run by hand when written; each turns the named test red):
  * drop the ``and (ev.location or "").strip()`` clause in ``classify`` -> test_classify_needs_a_keyword_and_a_place;
  * return True from ``_chat_session`` (a voice session gets an offer) -> test_offers_only_reach_the_events_owner;
  * delete the ``if s.get("last_day") == day or ...`` skip in ``pick_for_turn`` and the PER_DAY check -> test_one_offer_per_turn_and_two_a_day;
  * drop the cooldown loop in ``pick_for_turn`` -> test_no_records_a_decline_and_cools_down;
  * skip the ``_offered`` check in ``pick_for_turn`` -> test_deleting_the_event_withdraws_its_offer;
  * make ``erase_entity`` delete nothing -> test_forget_cascade_deletes_offers_naming_the_entity;
  * remove ``if not alarms.enabled()`` in ``_do`` -> test_wake_yes_with_alarms_off_writes_nothing;
  * remove the ``mode() != "on"`` check in ``match_reply`` -> test_flag_off_does_no_io.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from datetime import datetime, timedelta, timezone
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
import write_receipts  # noqa: E402

pytestmark = pytest.mark.ci_safe

PERTH = ZoneInfo("Australia/Perth")
ANN, BOB = "ann-0001", "bob-0002"
NOW = datetime(2026, 11, 10, 22, 0, tzinfo=PERTH)            # Tuesday evening
EVENTS = [
    # id, user, title, date, time, end_date, location, all_day
    ("ev-flight", ANN, "Flight to Sydney", "2026-11-12", "09:40", "", "Perth Airport", 0),
    ("ev-dentist", ANN, "Dentist", "2026-11-13", "10:00", "", "12 Example Street", 0),
    ("ev-lunch", ANN, "Lunch with a friend", "2026-11-12", "12:30", "", "Corner Cafe", 0),
    ("ev-holiday", ANN, "Holiday", "2026-11-14", "", "2026-11-18", "", 1),
    ("ev-bob", BOB, "Flight to Melbourne", "2026-11-12", "08:00", "", "Perth Airport", 0),
]


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
        " location TEXT, all_day INTEGER DEFAULT 0, recurring TEXT, rrule TEXT, status TEXT, deleted INTEGER DEFAULT 0, end_time TEXT,"
        " timezone TEXT, rdates TEXT, exdates TEXT, recurrence_id TEXT, series_id TEXT)",
        "CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL, action_type TEXT"
        " NOT NULL, description TEXT NOT NULL, list_type TEXT, when_hint TEXT, amount_hint TEXT, offer_phrase TEXT NOT NULL,"
        " pre_filled_slots TEXT, created_at TEXT NOT NULL, turns_elapsed INTEGER NOT NULL DEFAULT 0, expire_after_turns INTEGER"
        " NOT NULL DEFAULT 2, resolved INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE lists (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, list_type TEXT, description TEXT, visibility TEXT,"
        " deleted INTEGER DEFAULT 0)",
        "CREATE TABLE list_items (id TEXT PRIMARY KEY, list_id TEXT, text TEXT, priority TEXT, category TEXT, quantity TEXT,"
        " parent_id TEXT, assigned_to TEXT)",
    ):
        await db.execute(sql)
    for uid in (ANN, BOB):
        await db.execute("INSERT INTO users (id, name, role) VALUES (?, ?, 'member')", (uid, uid))
    for e in EVENTS:
        await db.execute("INSERT INTO events (id, user_id, title, start_date, start_time, end_date, location, all_day)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", e)
    # Ann's usual alarm: 07:00 on weekdays
    await db.execute("INSERT INTO alarms (id, user_id, label, time_hhmm, repeat_rule, once_date, sound_kind, sound_id, sound_name,"
                     " snooze_minutes, ramp_seconds, enabled, next_fire_at, state, snooze_count, created_at, updated_at)"
                     " VALUES ('al-usual', ?, 'Work', '07:00', 'weekdays', NULL, 'tone', 'sunrise', '', 9, 30, 1, 0, 'idle', 0, 0, 0)",
                     (ANN,))
    await db.commit()


@pytest.fixture
def dbpath(tmp_path):
    p = str(tmp_path / "a.db")
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
    monkeypatch.delenv("ZOE_ALARMS", raising=False)
    monkeypatch.delenv("ZOE_OUTCOME_EDGES", raising=False)

    async def adult(uid, db=None):
        return True

    monkeypatch.setattr(ant, "_is_adult", adult)
    ant.reset_state()
    yield
    ant.reset_state()


async def _with(path, fn):
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row          # the shared calendar read path reads rows by name, as the pool's records allow
        return await fn(db)


async def _offers(db, uid=ANN):
    return await ant._rows(db, uid)


def _ev(**kw):
    base = dict(id="e", user_id=ANN, title="", start_date="2026-11-12", start_time="09:40", end_date="", location="")
    base.update(kw)
    return ant.Event(**base)


# -- extraction ---------------------------------------------------------------------------------------------------------------
def test_classify_needs_a_keyword_and_a_place():
    assert ant.classify(_ev(title="Flight to Sydney")) == "flight"
    assert ant.classify(_ev(title="QF642")) == "flight"
    assert ant.classify(_ev(title="Train to the city")) == "train"
    assert ant.classify(_ev(title="Holiday")) == "trip"
    assert ant.classify(_ev(title="Dentist", location="12 Example Street")) == "appointment"
    assert ant.classify(_ev(title="Dentist")) is None                    # an appointment needs a place to leave for
    assert ant.classify(_ev(title="Lunch with a friend", location="Corner Cafe")) is None
    assert ant.classify(_ev(title="Team lunch")) is None


def test_candidates_for_flight_dentist_and_trip_not_lunch():
    events = [ant.Event(e[0], e[1], e[2], e[3], e[4], e[5], e[6], bool(e[7])) for e in EVENTS if e[1] == ANN]
    offers = ant.candidates(events, now=NOW, usual_wake=lambda d: "07:00", adult=True)
    got = sorted((o.event_id, o.kind) for o in offers)
    assert got == [("ev-dentist", "leave"), ("ev-flight", "leave"), ("ev-flight", "wake"), ("ev-holiday", "packing")]
    wake = next(o for o in offers if o.kind == "wake")
    assert wake.slots["wake_time"] == "06:10" and wake.slots["usual_time"] == "07:00"
    leave = next(o for o in offers if o.kind == "leave" and o.event_id == "ev-flight")
    assert leave.slots["leave_time"] == "07:10"
    line = ant.phrase("wake", wake.slots, NOW)
    assert "Sydney" not in line and "Airport" not in line                  # the line never carries a title or a place
    assert "6:10 am" in line and "Thursday" in line


def test_kids_get_no_alarm_offers():
    events = [ant.Event("ev-flight", ANN, "Flight to Sydney", "2026-11-12", "09:40", "", "Perth Airport")]
    kinds = {o.kind for o in ant.candidates(events, now=NOW, usual_wake=lambda d: "07:00", adult=False)}
    assert kinds == {"leave"}


def test_no_wake_offer_when_the_usual_alarm_is_early_enough_or_unknown():
    events = [ant.Event("ev-flight", ANN, "Flight to Sydney", "2026-11-12", "09:40", "", "Perth Airport")]
    assert {o.kind for o in ant.candidates(events, now=NOW, usual_wake=lambda d: "06:00", adult=True)} == {"leave"}
    assert {o.kind for o in ant.candidates(events, now=NOW, usual_wake=lambda d: None, adult=True)} == {"leave"}


# -- night: store ---------------------------------------------------------------------------------------------------------------
async def _repeat_offers(db, *rows, now=NOW):
    for rid, start_date, start_time, rule in rows:
        await db.execute("INSERT INTO events (id, user_id, title, start_date, start_time, end_date, location, all_day, rrule)"
                         " VALUES (?, ?, 'Physio', ?, ?, '', '12 Example Street', 0, ?)", (rid, ANN, start_date, start_time, rule))
    await db.commit()
    events = [e for e in await ant._events(db, ANN, now) if e.id in {r[0] for r in rows}]
    return ant.candidates(events, now=now, usual_wake=lambda d: None, adult=True)


def test_a_repeating_event_offers_for_its_next_occurrence_only(dbpath):
    today = NOW.astimezone(ant._tz()).date()
    anchor = (today - timedelta(days=70)).isoformat()                    # started ten weeks ago, same weekday as today

    async def go(db):
        weekly = await _repeat_offers(db, ("ev-rep", anchor, "23:30", "weekly"))
        fortnight = await _repeat_offers(db, ("ev-rr", anchor, "23:30", "FREQ=WEEKLY;INTERVAL=2"))
        daily = await _repeat_offers(db, ("ev-day", anchor, "23:30", "daily"))
        odd = await _repeat_offers(db, ("ev-odd", anchor, "23:30", "every blue moon"))
        old = await _repeat_offers(db, ("ev-old", anchor, "23:30", ""))
        return weekly, fortnight, daily, odd, old

    (offer,), (o2,), daily, odd, old = run(_with(dbpath, go))
    assert offer.kind == "leave" and offer.slots["start_date"] == today.isoformat()      # the next one, not the anchor
    assert offer.key.startswith(f"leave:ev-rep:{today.isoformat()}T")                    # next week's is a new key, a new offer
    assert o2.slots["start_date"] == today.isoformat()                                   # 70 days = five fortnights
    assert len(daily) == 1                                                               # one offer, never fourteen
    assert odd == []                                                                     # not understood: no guessed date
    assert old == []                                       # control: no repeat and a start ten weeks ago offers nothing


def test_a_repeating_event_whose_start_today_has_passed_offers_for_the_one_after(dbpath):
    today = NOW.astimezone(ant._tz()).date()
    anchor = (today - timedelta(days=70)).isoformat()

    async def go(db):                                      # NOW is 22:00: this morning's has been and gone
        return (await _repeat_offers(db, ("ev-am", anchor, "09:00", "weekly")),
                await _repeat_offers(db, ("ev-d", anchor, "09:00", "daily")))

    weekly, daily = run(_with(dbpath, go))
    assert [o.slots["start_date"] for o in weekly if o.kind == "leave"] == [(today + timedelta(days=7)).isoformat()]
    assert [o.slots["start_date"] for o in daily if o.kind == "leave"] == [(today + timedelta(days=1)).isoformat()]


def test_a_repeating_event_is_read_from_the_calendar_and_its_offer_survives_the_recheck(dbpath):
    today = NOW.astimezone(ant._tz()).date()
    anchor = (today - timedelta(days=70)).isoformat()

    async def go(db):
        await db.execute("INSERT INTO events (id, user_id, title, start_date, start_time, end_date, location, all_day, recurring)"
                         " VALUES ('ev-rep', ?, 'Physio', ?, '23:30', '', '12 Example Street', 0, 'weekly')", (ANN, anchor))
        await db.commit()
        res = await ant.generate_for_user(ANN, now=NOW, db=db)
        again = await ant.generate_for_user(ANN, now=NOW + timedelta(minutes=30), db=db)
        mine = [r for r in await _offers(db) if r["slots"]["event_id"] == "ev-rep"]
        still = await ant._offered(db, ANN, mine[0]["slots"])
        wrong_day = await ant._offered(db, ANN, {**mine[0]["slots"], "start_date": (today + timedelta(days=1)).isoformat()})
        return res, again, mine, still, wrong_day

    res, again, mine, still, wrong_day = run(_with(dbpath, go))
    assert len(mine) == 1 and mine[0]["kind"] == "leave" and mine[0]["resolved"] == ant.OPEN
    assert mine[0]["key"] == f"leave:ev-rep:{today.isoformat()}T23:30" and again["stored"] == 0   # a re-run never duplicates it
    assert still is not None and wrong_day is None         # the offer holds while the rule still lands on its day, and only then


def test_a_zoned_flight_gets_household_times(dbpath):
    """A flight stored at 09:40 in Sydney (UTC+11 in November) is 06:40 in Perth: the leave and wake times follow the household
    clock. Before the shared read path the wall clock was read as household time (leave 07:10)."""
    async def go(db):
        await db.execute("INSERT INTO events (id, user_id, title, start_date, start_time, end_date, location, all_day, timezone)"
                         " VALUES ('ev-syd', ?, 'Flight home', '2026-11-12', '09:40', '', 'Sydney Airport', 0, 'Australia/Sydney')",
                         (ANN,))
        await db.commit()
        return [e for e in await ant._events(db, ANN, NOW) if e.id == "ev-syd"]

    events = run(_with(dbpath, go))
    assert [(e.start_date, e.start_time) for e in events] == [("2026-11-12", "06:40")]
    (leave,) = [o for o in ant.candidates(events, now=NOW, usual_wake=lambda d: "07:00", adult=True) if o.kind == "leave"]
    assert leave.slots["start_time"] == "06:40" and leave.slots["leave_time"] == "04:10"


def test_exdate_cancelled_instance_and_unreadable_rule_get_no_offer(dbpath):
    """Behaviour change: an EXDATE or a cancelled override removes that occurrence (the next one is offered instead). Unchanged: a
    rule that is not understood offers nothing, even when its base date is inside the horizon."""
    async def go(db):
        for sql in ("INSERT INTO events (id, user_id, title, start_date, start_time, location, rrule, exdates) VALUES"
                    " ('ev-ex', ?, 'Physio', '2026-11-05', '10:00', '12 Example Street', 'FREQ=WEEKLY', '[\"2026-11-12\"]')",
                    "INSERT INTO events (id, user_id, title, start_date, start_time, location, rrule) VALUES"
                    " ('ev-cx', ?, 'Dentist', '2026-11-05', '11:00', '12 Example Street', 'FREQ=WEEKLY')",
                    "INSERT INTO events (id, user_id, title, start_date, start_time, location, status, series_id, recurrence_id)"
                    " VALUES ('ev-cx-1', ?, 'Dentist', '2026-11-12', '11:00', '12 Example Street', 'cancelled', 'ev-cx', '2026-11-12')",
                    "INSERT INTO events (id, user_id, title, start_date, start_time, location, recurring) VALUES"
                    " ('ev-odd', ?, 'Doctor', '2026-11-13', '09:00', '12 Example Street', 'every blue moon')"):
            await db.execute(sql, (ANN,))
        await db.commit()
        return {(e.id, e.start_date) for e in await ant._events(db, ANN, NOW) if e.id.startswith(("ev-ex", "ev-cx", "ev-odd"))}

    assert run(_with(dbpath, go)) == {("ev-ex", "2026-11-19"), ("ev-cx", "2026-11-19")}


def test_each_occurrence_is_its_own_offer_and_a_rerun_never_duplicates(dbpath):
    """The key is ``<kind>:<event id>:<household date>T<HH:MM>``: next week's occurrence is a new offer, the same night re-run is not."""
    async def go(db):
        await db.execute("INSERT INTO events (id, user_id, title, start_date, start_time, location, recurring) VALUES"
                         " ('ev-wk', ?, 'Physio', '2026-11-11', '10:00', '12 Example Street', 'weekly')", (ANN,))
        await db.commit()
        for now in (NOW, NOW + timedelta(hours=2), NOW + timedelta(days=7), NOW + timedelta(days=7, hours=2)):
            await ant.generate_for_user(ANN, now=now, db=db)
        return sorted((r["key"], r["resolved"]) for r in await _offers(db) if r["slots"]["event_id"] == "ev-wk")

    assert run(_with(dbpath, go)) == [("leave:ev-wk:2026-11-11T10:00", ant.WITHDRAWN), ("leave:ev-wk:2026-11-18T10:00", ant.OPEN)]


def test_generate_stores_once_per_event_and_shadow_stores_nothing(dbpath, monkeypatch):
    async def go(db):
        monkeypatch.setenv("ZOE_ANTICIPATION", "shadow")
        shadow = await ant.generate_for_user(ANN, now=NOW, db=db)
        assert shadow["stored"] == 0 and shadow["wake"] == 1 and await _offers(db) == []
        monkeypatch.setenv("ZOE_ANTICIPATION", "on")
        first = await ant.generate_for_user(ANN, now=NOW, db=db)
        again = await ant.generate_for_user(ANN, now=NOW + timedelta(hours=1), db=db)
        return first, again, await _offers(db)

    first, again, rows = run(_with(dbpath, go))
    assert first["stored"] == 4 and again["stored"] == 0
    assert sorted(r["kind"] for r in rows) == ["leave", "leave", "packing", "wake"]
    assert all(r["resolved"] == ant.OPEN for r in rows)


# -- day: surface -----------------------------------------------------------------------------------------------------------------
async def _night(db, uid=ANN):
    await ant.generate_for_user(uid, now=NOW, db=db)


def test_offers_only_reach_the_events_owner(dbpath):
    async def go(db):
        await _night(db)
        await _night(db, BOB)
        bob = await ant.pick_for_turn(BOB, "chat-b", now=NOW, db=db)
        guest = await ant.pick_for_turn("guest", "chat-g", now=NOW, db=db)
        panel = await ant.pick_for_turn("panel-kitchen", "chat-p", now=NOW, db=db)
        voice = await ant.pick_for_turn(ANN, "voice-panel-1", now=NOW, db=db)
        for shared in ("panel-kitchen-1", "kiosk-1", "Touch-abc"):     # a shared screen, even with the member signed in
            assert await ant.pick_for_turn(ANN, shared, now=NOW, db=db) is None
        ann = await ant.pick_for_turn(ANN, "chat-a", now=NOW, db=db)
        return bob, guest, panel, voice, ann, await _offers(db, BOB)

    bob, guest, panel, voice, ann, bob_rows = run(_with(dbpath, go))
    assert guest is None and panel is None and voice is None
    assert ann is not None and ann.kind == "wake" and ann.text.startswith("By the way - ")
    # Bob gets his own flight only (Bob is an adult with no usual alarm: leave-by), never Ann's
    assert bob is not None and bob.id in {r["id"] for r in bob_rows}


def test_one_offer_per_turn_and_two_a_day(dbpath):
    async def go(db):
        await _night(db)
        a = await ant.pick_for_turn(ANN, "chat-1", now=NOW, db=db)
        b = await ant.pick_for_turn(ANN, "chat-2", now=NOW + timedelta(minutes=5), db=db)
        c = await ant.pick_for_turn(ANN, "chat-3", now=NOW + timedelta(minutes=10), db=db)
        nxt = await ant.pick_for_turn(ANN, "chat-4", now=NOW + timedelta(hours=10), db=db)   # the next local day
        return a, b, c, nxt

    a, b, c, nxt = run(_with(dbpath, go))
    assert a and b and a.id != b.id
    assert c is None                                   # PER_DAY = 2
    assert nxt is not None


def test_an_offer_asked_twice_without_an_answer_is_ignored(dbpath):
    async def go(db):
        await db.execute("UPDATE events SET deleted = 1 WHERE id <> 'ev-dentist'")
        await db.commit()
        await _night(db)
        seen = [await ant.pick_for_turn(ANN, f"chat-{d}", now=NOW + timedelta(days=d), db=db) for d in range(3)]
        return seen, await _offers(db)

    seen, rows = run(_with(dbpath, go))
    assert seen[0] is not None and seen[1] is not None and seen[0].id == seen[1].id
    assert seen[2] is None
    assert [(r["resolved"], r["slots"]["outcome"]) for r in rows] == [(ant.IGNORED, "ignored")]


# -- the answer -------------------------------------------------------------------------------------------------------------------
def test_yes_to_packing_creates_a_private_list_with_a_receipt(dbpath):
    async def go(db):
        await _night(db)
        row = next(r for r in await _offers(db) if r["kind"] == "packing")
        started = write_receipts.begin()
        reply = await ant.answer(ANN, row["id"], True, now=NOW, db=db)
        lists = await ant._all(db, "SELECT user_id, list_type, visibility, id FROM lists", ())
        items = await ant._all(db, "SELECT COUNT(*) FROM list_items", ())
        return reply, lists, items, write_receipts.has(ANN, started, "list"), (await ant._own_row(db, ANN, row["id"]))

    reply, lists, items, receipt, row = run(_with(dbpath, go))
    assert reply.startswith("Done") and "private packing list" in reply
    assert [(u, t, v) for u, t, v, _ in lists] == [(ANN, "personal", "personal")]    # never a household list
    assert items[0][0] >= 4 and receipt
    assert row["resolved"] == ant.DONE and row["slots"]["outcome"] == "acted" and row["slots"]["receipt"]["list_id"] == lists[0][3]


def test_yes_to_wake_sets_a_one_off_alarm(dbpath, monkeypatch):
    monkeypatch.setenv("ZOE_ALARMS", "on")

    async def go(db):
        await _night(db)
        row = next(r for r in await _offers(db) if r["kind"] == "wake")
        reply = await ant.answer(ANN, row["id"], True, now=NOW, db=db)
        alarms = await ant._all(db, "SELECT time_hhmm, repeat_rule, once_date FROM alarms WHERE user_id = ? ORDER BY time_hhmm", (ANN,))
        return reply, alarms

    reply, alarms = run(_with(dbpath, go))
    assert "6:10 am" in reply and "usual alarm stays" in reply
    assert ("06:10", "once", "2026-11-12") in [tuple(a) for a in alarms] and ("07:00", "weekdays", None) in [tuple(a) for a in alarms]


def test_wake_yes_with_alarms_off_writes_nothing(dbpath):
    async def go(db):
        await _night(db)
        row = next(r for r in await _offers(db) if r["kind"] == "wake")
        reply = await ant.answer(ANN, row["id"], True, now=NOW, db=db)
        n = await ant._all(db, "SELECT COUNT(*) FROM alarms", ())
        return reply, n[0][0], (await ant._own_row(db, ANN, row["id"]))["resolved"]

    reply, n, resolved = run(_with(dbpath, go))
    assert reply == ant.ALARMS_OFF and n == 1 and resolved == ant.UNAVAILABLE


def test_yes_to_leave_runs_the_reminder_writer(dbpath, monkeypatch):
    import reminder_service

    calls = []

    async def fake(payload, *, user, db, now_utc=None, schedule=True):
        calls.append((payload.title, payload.due_date, payload.due_time, payload.visibility, user["user_id"]))
        return {"id": "rem-1"}

    monkeypatch.setattr(reminder_service, "create_reminder_record", fake)

    async def go(db):
        await _night(db)
        row = next(r for r in await _offers(db) if r["kind"] == "leave" and r["slots"]["event_id"] == "ev-flight")
        return await ant.answer(ANN, row["id"], True, now=NOW, db=db), await ant.answer(ANN, row["id"], True, now=NOW, db=db)

    reply, again = run(_with(dbpath, go))
    assert calls == [("Leave for your flight", "2026-11-12", "07:10", "personal", ANN)]
    assert "7:10 am" in reply and again == ant.STALE           # a second yes never runs the tool twice


def test_a_failed_write_says_so_and_keeps_the_offer(dbpath, monkeypatch):
    import reminder_service

    async def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(reminder_service, "create_reminder_record", broken)

    async def go(db):
        await _night(db)
        row = next(r for r in await _offers(db) if r["kind"] == "leave")
        return await ant.answer(ANN, row["id"], True, now=NOW, db=db), (await ant._own_row(db, ANN, row["id"]))["resolved"]

    reply, resolved = run(_with(dbpath, go))
    assert reply == ant.FAILED and resolved == ant.OPEN


def test_no_records_a_decline_and_cools_down(dbpath):
    async def go(db):
        await _night(db)
        first = await ant.pick_for_turn(ANN, "chat-1", now=NOW, db=db)
        reply = await ant.answer(ANN, first.id, False, now=NOW, db=db)
        during = await ant.pick_for_turn(ANN, "chat-2", now=NOW + timedelta(hours=12), db=db)
        after = await ant.pick_for_turn(ANN, "chat-3", now=NOW + timedelta(hours=25), db=db)
        return first, reply, during, after, await ant._own_row(db, ANN, first.id)

    first, reply, during, after, row = run(_with(dbpath, go))
    assert reply == ant.DECLINED_REPLY and row["resolved"] == ant.DECLINED and row["slots"]["outcome"] == "dismissed"
    assert during is None
    assert after is not None and after.id != first.id


def test_deleting_the_event_withdraws_its_offer(dbpath):
    async def go(db):
        await _night(db)
        await db.execute("UPDATE events SET deleted = 1 WHERE id = 'ev-flight'")
        await db.commit()
        s = await ant.pick_for_turn(ANN, "chat-1", now=NOW, db=db)
        rows = {r["slots"]["event_id"] + ":" + r["kind"]: r["resolved"] for r in await _offers(db)}
        return s, rows

    s, rows = run(_with(dbpath, go))
    assert s is not None and s.kind != "wake"            # the flight's offers are never shown
    assert rows["ev-flight:wake"] == ant.WITHDRAWN and rows["ev-flight:leave"] == ant.WITHDRAWN


def test_forget_cascade_deletes_offers_naming_the_entity(dbpath):
    async def go(db):
        await _night(db)
        n = await ant.erase_entity(db, ANN, re.compile(r"\bdentist\b", re.IGNORECASE))
        return n, {r["slots"]["event_id"] for r in await _offers(db)}

    n, left = run(_with(dbpath, go))
    assert n == 1 and "ev-dentist" not in left and "ev-flight" in left


def test_cascade_runs_the_anticipation_step(dbpath):
    import memory_forget_cascade as cascade

    assert "anticipation_offers" in cascade.DB_STEPS and "anticipation_offers" in cascade.ALL_LABELS

    async def go(db):
        await _night(db)
        res = cascade.CascadeResult()
        await cascade._run(db, ANN, re.compile(r"\bsydney\b", re.IGNORECASE), res, only=frozenset({"anticipation_offers"}))
        return res, {r["slots"]["event_id"] for r in await _offers(db)}

    res, left = run(_with(dbpath, go))
    assert res.offers == 2 and not res.failed and "ev-flight" not in left


def test_a_short_yes_binds_only_to_the_offer_just_made(dbpath):
    async def go(db):
        await _night(db)
        s = await ant.pick_for_turn(ANN, "chat-1", now=NOW, db=db)

        async def prev_ok(uid):
            return "Sure, here's the plan for tomorrow.\n\n" + s.text

        async def prev_other(uid):
            return "Shall I add milk to the shopping list?"

        return (s, await ant.match_reply(ANN, "yes please", prev_ok), await ant.match_reply(ANN, "no thanks", prev_ok),
                await ant.match_reply(ANN, "yes please", prev_other), await ant.match_reply(BOB, "yes", prev_ok),
                await ant.match_reply(ANN, "yes book the hotel too", prev_ok))

    s, yes, no, other, bob, long = run(_with(dbpath, go))
    assert yes == (s.id, True) and no == (s.id, False)
    assert other is None and bob is None and long is None


def test_card_uses_the_existing_accept_and_dismiss_actions():
    c = ant.card(ant.Surfaced("sid-1", "leave", "By the way - x?", "x?"))
    assert [a["action"] for a in c["actions"]] == ["pending_suggestion_accept", "pending_suggestion_dismiss"]
    assert all(a["suggestion_id"] == "sid-1" for a in c["actions"])


# -- flag off ---------------------------------------------------------------------------------------------------------------------
def test_flag_off_does_no_io(monkeypatch):
    monkeypatch.setenv("ZOE_ANTICIPATION", "off")

    class Boom:
        def __getattr__(self, name):
            raise AssertionError("I/O with the flag off")

    def no_conn(db=None):
        raise AssertionError("I/O with the flag off")

    monkeypatch.setattr(ant, "_conn", no_conn)

    async def prev(uid):
        raise AssertionError("I/O with the flag off")

    async def go():
        ant._ASKED[ANN] = {"id": "x", "question": "q?", "at": ant._clock_s()}
        return (await ant.generate_for_user(ANN, now=NOW, db=Boom()), await ant.pick_for_turn(ANN, "chat-1", now=NOW, db=Boom()),
                await ant.match_reply(ANN, "yes", prev), await ant.execute_if_ours("x", ANN))

    assert run(go()) == (None, None, None, None)
    monkeypatch.setenv("ZOE_ANTICIPATION", "banana")
    assert ant.mode() == "off"


def test_execute_suggestion_does_not_consult_anticipation_when_off(monkeypatch):
    import pending_suggestions

    monkeypatch.setenv("ZOE_ANTICIPATION", "off")

    async def boom(*a, **k):
        raise AssertionError("consulted with the flag off")

    monkeypatch.setattr(ant, "execute_if_ours", boom)

    def no_pool():
        raise RuntimeError("no pool")

    monkeypatch.setattr(pending_suggestions, "get_pool", no_pool)
    assert run(pending_suggestions.execute_suggestion("x", ANN)) == {"ok": False, "error": "no pool"}


def test_logs_carry_no_title_or_place(dbpath, caplog):
    caplog.set_level("INFO", logger="anticipation")

    async def go(db):
        await _night(db)
        s = await ant.pick_for_turn(ANN, "chat-1", now=NOW, db=db)
        await ant.answer(ANN, s.id, False, now=NOW, db=db)

    run(_with(dbpath, go))
    text = caplog.text
    assert "ANTICIPATION mode=on" in text and "ANTICIPATION_SURFACE" in text and "ANTICIPATION_OUTCOME" in text
    for word in ("Sydney", "Airport", "Dentist", "Example", "Holiday"):
        assert word not in text
