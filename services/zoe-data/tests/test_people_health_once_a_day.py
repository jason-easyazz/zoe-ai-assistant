"""The "you haven't spoken to ..." nudge goes out ONCE per person per day, not on every slow-loop tick of the 9 o'clock hour.

On main the trigger's only gate was the hour, the slow loop runs every 300 s, and the engine fires every result a trigger
returns: up to twelve identical nudges between 09:00 and 10:00. Verified by reading ``proactive/engine.py`` (``_slow_loop``:
``trigger.check`` then ``fire_notification`` for each result, no dedupe) and by the first test below against the old code.

Control run for each test is named in its docstring (the code broken, the test seen to fail, restored). Invented people only.
"""
from __future__ import annotations

import asyncio
import zoneinfo
from datetime import datetime, timedelta

import pytest

from proactive.triggers import people_health as ph

pytestmark = pytest.mark.ci_safe

TZ = zoneinfo.ZoneInfo("Australia/Perth")
NINE = datetime(2026, 6, 10, 9, 2, tzinfo=TZ)
MEMBER, QUILL, WREN = "member-ova", "person-quill", "person-wren"


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return list(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class DB:
    """Two neglected inner-circle people. ``pending`` is what ``proactive_pending`` holds for this trigger today."""

    def __init__(self, pending=(), pending_fails=False):
        self.pending, self.pending_fails = list(pending), pending_fails

    def execute(self, sql, params=()):
        if "FROM proactive_pending" in sql:
            if self.pending_fails:
                raise RuntimeError("read failed")
            assert params[0] == "people_health" and params[2] == NINE.date().isoformat()
            return _Cur([{"user_id": u, "item_id": p} for u, p in self.pending])
        return _Cur([{"id": QUILL, "name": "Quill", "user_id": MEMBER, "last_contacted_at": None, "context": None},
                     {"id": WREN, "name": "Wren", "user_id": MEMBER, "last_contacted_at": None, "context": None}])


def tick(db, when=NINE, deliver=lambda item_id: None):
    """One slow-loop tick as the ENGINE runs it: ``check``, then ``engine._fire_trigger_results`` with ``fire_notification``
    replaced by ``deliver`` (which may raise). Returns the people offered."""
    from proactive import engine

    async def fire(**kw):
        deliver(kw["item_id"])
        if not any(p == (kw["user_id"], kw["item_id"]) for p in getattr(db, "pending", [])) and hasattr(db, "pending"):
            db.pending.append((kw["user_id"], kw["item_id"]))      # the pending row every delivered nudge leaves

    async def run():
        results = await ph.PeopleHealthTrigger().check(db, now=when)
        real, engine.fire_notification = engine.fire_notification, fire
        try:
            await engine._fire_trigger_results(results)
        finally:
            engine.fire_notification = real
        return [r.item_id for r in results]

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def _fresh():
    ph._fired.clear()
    yield
    ph._fired.clear()


def test_twelve_ticks_inside_the_hour_nudge_each_person_once():
    """The engine fires whatever ``check`` returns, so once-a-day has to hold here. Control run: with the two skip lines removed
    from ``check`` (main's code) every tick returns both people and this fails at the second tick."""
    db = DB()
    fired = [tick(db, NINE + timedelta(minutes=5 * i)) for i in range(12)]
    assert fired[0] == [QUILL, WREN] and all(f == [] for f in fired[1:])


def test_a_restart_inside_the_hour_does_not_repeat_a_nudge_already_recorded():
    """A new process has an empty in-process set; the row the first nudge left is what stops the repeat. Control run: with
    ``already`` ignored in ``check`` the restarted process nudges about Quill again."""
    assert tick(DB(pending=[(MEMBER, QUILL)])) == [WREN]


def test_a_nudge_that_failed_before_its_row_existed_is_tried_again_on_the_next_tick():
    """The mark is made only once the nudge is out. A delivery that raises (a composition or database error before the pending
    row exists) leaves that person unmarked, so the next tick offers them again, as main did; the one that went out is not
    repeated. Control run: with ``_fired.add(mark)`` put back in ``check`` (marked before delivery) the second tick offers
    nobody and Quill's nudge is lost for the day."""
    db = DB(pending_fails=True)                       # the fired-today read is no help: only the in-process mark is

    def quill_fails(item_id):
        if item_id == QUILL:
            raise RuntimeError("compose failed")

    assert tick(db, deliver=quill_fails) == [QUILL, WREN]
    assert tick(db, NINE + timedelta(minutes=5), deliver=quill_fails) == [QUILL]        # only the one that failed
    assert tick(db, NINE + timedelta(minutes=10)) == [QUILL]                            # ... and it goes out now
    assert tick(db, NINE + timedelta(minutes=15)) == []


def test_a_row_seen_once_is_remembered_when_a_later_read_fails():
    """Control run: with the ``_fired.add(mark)`` under ``key in already`` removed, the failing read lets Quill through again."""
    db = DB(pending=[(MEMBER, QUILL), (MEMBER, WREN)])
    assert tick(db) == []
    db.pending_fails = True
    assert tick(db, NINE + timedelta(minutes=5)) == []


def test_the_line_holds_when_the_fired_today_read_fails_and_the_next_day_starts_clean():
    """Control run: with ``_fired`` never added to, a failing read lets every tick through."""
    db = DB(pending_fails=True)
    assert tick(db) == [QUILL, WREN] and tick(db, NINE + timedelta(minutes=5)) == []
    assert tick(DB(), NINE + timedelta(days=1)) == [QUILL, WREN]                  # tomorrow: once again, and yesterday's keys are gone
    assert {k[2] for k in ph._fired} == {(NINE + timedelta(days=1)).date()}


def test_outside_the_nine_oclock_hour_nothing_is_read_or_sent():
    class Never:
        def execute(self, *a, **k):
            raise AssertionError("read outside the hour")

    assert tick(Never(), NINE.replace(hour=10)) == [] and tick(Never(), NINE.replace(hour=8)) == []
