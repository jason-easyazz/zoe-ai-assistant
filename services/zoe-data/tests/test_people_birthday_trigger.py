"""The birthday fix (person-centred memory, owner decision D3): day-of included, both date sources read, lead days by circle or
per-date override, once per person per day. Each rule has a control that reproduces the pre-fix behaviour. Real in-memory
SQLite, synthetic names only."""
from __future__ import annotations

import asyncio
import zoneinfo
from datetime import date, datetime, timedelta

import aiosqlite
import pytest

from proactive.triggers import people_birthday as pb

pytestmark = pytest.mark.ci_safe

U = "demo_bday_owner"
TZ = zoneinfo.ZoneInfo("Australia/Perth")
TODAY = date(2026, 6, 10)


def at(day: date, hour: int = 8) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 5, tzinfo=TZ)


def bday(offset: int) -> tuple:
    d = TODAY + timedelta(days=offset)
    return d.month, d.day


async def _db(people, dates):
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("""CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, circle TEXT, birthday TEXT,
        deleted INTEGER DEFAULT 0)""")
    await db.execute("""CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT, date_type TEXT,
        month INTEGER, day INTEGER, reminder_days_before INTEGER NOT NULL DEFAULT 7)""")
    await db.executemany("INSERT INTO people VALUES (?,?,?,?,?,?)", people)
    await db.executemany("INSERT INTO person_important_dates VALUES (?,?,?,?,?,?,?)", dates)
    await db.commit()
    return db


def check(people, dates, when):
    async def go():
        db = await _db(people, dates)
        try:
            return await pb.PeopleBirthdayTrigger().check(db, now=when)
        finally:
            await db.close()
    return asyncio.run(go())


def dated(pid, name, offset, circle="friends", lead=7, legacy=""):
    m, d = bday(offset)
    return (pid, U, name, circle, legacy, 0), (f"d-{pid}", pid, U, "birthday", m, d, lead)


@pytest.fixture(autouse=True)
def _reset():
    pb._fired.clear()
    yield
    pb._fired.clear()


def test_day_of_fires():
    p, d = dated("p1", "Ana Test", 0)
    out = check([p], [d], at(TODAY))
    assert [r.message for r in out] == ["It's Ana Test's birthday today."]


def test_negative_control_old_window_excluded_day_of():
    """The pre-fix rule (1 <= days <= 7) says nothing on the day: the fix is what makes it fire."""
    assert not (1 <= 0 <= 7)
    assert 0 in pb.lead_days("friends")


@pytest.mark.parametrize("offset,fires", [(7, True), (6, False), (3, False), (1, False), (14, False)])
def test_default_lead_is_seven_and_day_of(offset, fires):
    p, d = dated("p1", "Ana Test", offset)
    assert bool(check([p], [d], at(TODAY))) is fires


@pytest.mark.parametrize("offset,fires", [(14, True), (7, False), (3, True), (0, True)])
def test_close_circle_lead_is_fourteen_three_and_day_of(offset, fires):
    p, d = dated("p1", "Mum Test", offset, circle="family")
    assert bool(check([p], [d], at(TODAY))) is fires


def test_per_date_override():
    p, d = dated("p1", "Ana Test", 10, lead=10)
    assert check([p], [d], at(TODAY))
    assert pb.lead_days("family", 10) == (10, 0) and pb.lead_days("family", 7) == pb.LEAD_CLOSE


def test_legacy_birthday_column_is_read():
    m, d = bday(7)
    out = check([("p2", U, "Bo Test", "work", f"1990-{m:02d}-{d:02d}", 0)], [], at(TODAY))
    assert [r.item_id for r in out] == ["p2"]


def test_dates_row_wins_over_legacy_column():
    m, d = bday(0)
    p, row = dated("p1", "Ana Test", 30, legacy=f"1990-{m:02d}-{d:02d}")   # the legacy column says today, the row says +30
    assert check([p], [row], at(TODAY)) == []
    assert len(check([p], [], at(TODAY))) == 1                            # control: without the row, the column is read


def test_once_per_person_per_day():
    p, d = dated("p1", "Ana Test", 0)

    async def go():
        db = await _db([p], [d])
        try:
            trig = pb.PeopleBirthdayTrigger()
            first = await trig.check(db, now=at(TODAY))
            second = await trig.check(db, now=at(TODAY).replace(minute=35))   # the next slow-loop pass, same hour
            return first, second
        finally:
            await db.close()
    first, second = asyncio.run(go())
    assert len(first) == 1 and second == []


def test_negative_control_without_the_memo_it_fires_again():
    p, d = dated("p1", "Ana Test", 0)
    assert check([p], [d], at(TODAY))
    pb._fired.clear()                                # the pre-fix trigger kept no memory between passes
    assert check([p], [d], at(TODAY))


def test_outside_eight_and_deleted_person_silent():
    p, d = dated("p1", "Ana Test", 0)
    assert check([p], [d], at(TODAY, hour=9)) == []
    assert check([p[:5] + (1,)], [d], at(TODAY)) == []
