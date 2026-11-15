"""WP-40: reminder entry points go through the reminder_service verbs and ONE ownership rule (synthetic people)."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.ci_safe

import pending_suggestions
import reminder_service as rs
from intent_router import Intent, execute_intent
from models import ReminderCreate

A, B = "member_a", "member_b"
FROZEN = datetime(2026, 6, 10, 4, 0, tzinfo=timezone.utc)  # 12:00 in a +08:00 household


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class _Store:
    """One reminder row; the SELECT honours the visibility filter the rule uses (family OR own, not deleted)."""

    def __init__(self, **row):
        self.row = {"id": "rem-1", "user_id": A, "title": "take pills", "visibility": "personal", "due_date": "2099-06-15",
                    "due_time": "08:00", "recurring_pattern": None, "is_active": 1, "acknowledged": 0, "deleted": 0,
                    "snoozed_until": None, **row}
        self.writes = []

    async def execute(self, sql, params=()):
        norm, p = " ".join(sql.split()), list(params)
        if norm.startswith("SELECT"):
            r = self.row
            if "visibility = 'family' OR user_id = ?" in norm:
                return _Cursor(dict(r) if (r["visibility"] == "family" or r["user_id"] == p[0]) and not r["deleted"] else None)
            if "FROM users" in norm:
                return _Cursor({"id": p[0], "role": "user", "name": "Member"})
            if "LOWER(title)" in norm:
                hit = r["user_id"] == p[0] and r["title"].lower() == p[1].lower() and not r["deleted"]
                return _Cursor({"id": r["id"]} if hit else None)
            return _Cursor(dict(r))
        self.writes.append((norm, p))
        if norm.startswith("UPDATE reminders SET deleted = 1"):
            self.row["deleted"] = 1
        return _Cursor()

    async def commit(self):
        pass

    def updates(self):
        return [w for w in self.writes if w[0].startswith("UPDATE reminders")]


def _ctx(store):
    @asynccontextmanager
    async def ctx():
        yield store
    return ctx


_actor = lambda uid: {"user_id": uid, "role": "user"}  # noqa: E731


@pytest.fixture
def quiet(monkeypatch):
    """No role matrix, panels or scheduler; records kernel-seam calls and scheduled jobs."""
    seam, jobs = [], []

    async def allow(*_a, **_k):
        return None

    async def schedule(**kw):
        jobs.append(kw)

    async def resched(_db, rid):
        jobs.append({"rescheduled": rid})

    for name, fake in (("require_feature_access", allow), ("broadcaster.broadcast", allow),
                       ("_cancel_reminder_jobs_safe", allow), ("_reschedule_reminder_due_safe", resched)):
        monkeypatch.setattr(f"reminder_service.{name}", fake)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", schedule)
    monkeypatch.setattr("reminder_service.kernel_seam", lambda verb, actor, rid=None: seam.append(verb))
    return {"seam": seam, "jobs": jobs}


# ── the one rule ──────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("visibility,actor,verb,own_only,expect", [
    ("personal", A, "snooze", False, None), ("personal", A, "done", False, None),
    ("personal", B, "edit", False, 404), ("personal", B, "snooze", False, 404),   # another member's private row: not found
    ("family", B, "edit", False, None), ("family", B, "cancel", False, None),     # family rows are shared for edit / cancel
    ("family", B, "snooze", False, 403), ("family", B, "done", False, 403),       # snooze and done: the owner only
    ("family", B, "edit", True, 404),                                             # a stricter entry point stays stricter
])
@pytest.mark.asyncio
async def test_one_rule(visibility, actor, verb, own_only, expect):
    call = rs.load_for_actor(_Store(visibility=visibility), _actor(actor), "rem-1", verb=verb, own_only=own_only)
    if expect is None:
        assert (await call)["id"] == "rem-1"
    else:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == expect


@pytest.mark.asyncio
async def test_only_the_owner_changes_visibility(quiet):
    db = _Store(visibility="family")
    await rs.update_reminder_record(db, _actor(B), "rem-1", {"title": "take pills twice", "visibility": "personal"})
    (sql, _), = db.updates()
    assert "title = ?" in sql and "visibility" not in sql and quiet["seam"] == ["edit"]
    assert quiet["jobs"] == [{"rescheduled": "rem-1"}]


# ── the store verbs: snooze is owner-only and a UTC instant; cancel honours own_only ───────────────────────────────────────
@pytest.mark.asyncio
async def test_snooze_is_owner_only_and_keeps_the_due_fields(quiet):
    db = _Store(visibility="family")                       # B can SEE it, but it is A's
    with pytest.raises(HTTPException) as exc:
        await rs.snooze_reminder_record(db, _actor(B), "rem-1", 10)
    assert exc.value.status_code == 403 and db.updates() == []
    db = _Store(recurring_pattern="FREQ=DAILY")
    await rs.snooze_reminder_record(db, _actor(A), "rem-1", 10, now_utc=FROZEN)
    (sql, params), = db.updates()
    assert "due_date" not in sql and "due_time" not in sql and params[0] == "2026-06-10T04:10:00Z"  # no clock offset
    assert quiet["jobs"][0]["item_id"] == "rem-1" and quiet["seam"] == ["snooze"]


@pytest.mark.asyncio
async def test_cancel_own_only_is_stricter(quiet):
    db = _Store(visibility="family")
    with pytest.raises(HTTPException) as exc:
        await rs.delete_reminder_record(db, _actor(B), "rem-1", own_only=True)
    assert exc.value.status_code == 404 and db.updates() == []
    await rs.delete_reminder_record(db, _actor(B), "rem-1")   # REST: a family reminder is cancellable by any member
    assert db.row["deleted"] == 1 and quiet["seam"] == ["cancel"]


# ── bug 2: an accepted reminder suggestion ────────────────────────────────────────────────────────────────────────────────
class _PgConn:
    """A fake asyncpg connection (the one ``AsyncpgCompat`` wraps) with REAL transaction semantics: ``transaction()`` snapshots the
    state and puts it back on an exception, and every statement is recorded with the transaction depth it ran at. A bare
    ``commit`` does nothing (as on Postgres), so a write made OUTSIDE ``transaction()`` is durable at once and cannot be undone."""

    def __init__(self, fail_resolve=False):
        self.reminders, self.notifications, self.resolved, self.fail_resolve = {}, [], 0, fail_resolve
        self.depth, self.log = 0, []

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                conn.depth += 1
                self.snap = (dict(conn.reminders), list(conn.notifications), conn.resolved)

            async def __aexit__(self, exc_type, *_):
                conn.depth -= 1
                if exc_type is not None:
                    conn.reminders, conn.notifications, conn.resolved = self.snap
        return _Tx()

    async def fetch(self, sql, *args):
        self.log.append((sql.split()[0], self.depth))
        if "FROM users" in sql:
            return [{"id": args[0], "role": "user", "name": "Member"}]
        if "FROM reminders" in sql:
            return [dict(self.reminders[args[0]])] if args[0] in self.reminders else []
        return []

    async def fetchrow(self, sql, *args):
        self.log.append(("fetchrow", self.depth))
        if "FROM pending_suggestions" in sql and not self.resolved:
            return {"action_type": "reminder_create",
                    "pre_filled_slots": '{"title": "book the vet", "date": "2099-06-16", "time": "15:00"}'}
        return None

    async def execute(self, sql, *args):
        self.log.append((" ".join(sql.split()[:3]), self.depth))
        if "INSERT INTO reminders" in sql:
            self.reminders[args[0]] = {"id": args[0], "user_id": args[1], "title": args[2], "due_date": args[7],
                                       "due_time": args[8], "is_active": 1, "acknowledged": 0, "deleted": 0}
        elif "INSERT INTO notifications" in sql:
            self.notifications.append(args)
        elif "UPDATE pending_suggestions" in sql:
            if self.fail_resolve:
                raise RuntimeError("connection lost")
            self.resolved = 1
        return "OK 1"


class _PgPool:
    def __init__(self, conn):
        self.conn, self.acquired = conn, 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                pool.acquired += 1
                return pool.conn

            async def __aexit__(self, *_):
                pass
        return _Ctx()


@pytest.mark.asyncio
async def test_accepted_suggestion_is_one_transaction_on_one_connection(monkeypatch):
    conn = _PgConn(fail_resolve=True)
    pool, scheduled, announced = _PgPool(conn), [], []

    async def allow(*_a, **_k):
        return None

    async def schedule(_db, row, **_k):
        scheduled.append(row["id"])

    async def announce(*a, **k):
        announced.append(a)

    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: pool)
    monkeypatch.setattr("reminder_service.require_feature_access", allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", announce)
    monkeypatch.setattr("proactive.triggers.reminder_scan.schedule_due_reminder", schedule)
    monkeypatch.setattr("anticipation.mode", lambda: "off")

    out = await pending_suggestions.execute_suggestion("sug-1", A)       # the suggestion's own update fails AFTER the insert
    assert out["ok"] is False and "connection lost" in out["error"]
    assert conn.reminders == {} and conn.notifications == [] and conn.resolved == 0     # rolled back together
    assert scheduled == [] and announced == []                           # no job and no panel news for a reminder that never was

    conn.fail_resolve = False                                            # the retry
    out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out["ok"] is True and len(conn.reminders) == 1 and conn.resolved == 1       # exactly one reminder
    assert scheduled == list(conn.reminders) and len(announced) == 1     # job and broadcast only after the commit
    assert pool.acquired == 2 and {depth for _, depth in conn.log} == {1}   # one connection per attempt; EVERY statement in the transaction


@pytest.mark.asyncio
async def test_a_reminder_offer_without_a_time_stays_open():
    conn = _PgConn()
    with pytest.raises(ValueError, match="reminder_needs_time"):
        await pending_suggestions._execute_action(conn, "reminder_create", {"title": "book the vet"}, A)
    assert conn.reminders == {} and conn.log == []
    assert pending_suggestions._suggested_when("book the vet", {"when_hint": "friday"}) == ("friday", None)


# ── a past day or time: refused, and the person is told ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_time_already_gone_today_is_refused(quiet, monkeypatch):
    monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
    db = _Store()

    async def make(**kw):
        await rs.create_reminder_record(ReminderCreate(title="call the plumber", **kw), user=_actor(A), db=db,
                                        now_utc=FROZEN, schedule=False)

    with pytest.raises(rs.ReminderPastError) as exc:
        await make(due_date="2026-06-10", due_time="09:00")
    assert exc.value.status_code == 422 and exc.value.spoken == rs.SPOKEN["past_time"] and db.writes == []
    await make(due_date="2026-06-10", due_time="13:00")  # positive controls: later today, and a daily (time-only) one
    await make(due_time="09:00")
    assert sum("INSERT INTO reminders" in w[0] for w in db.writes) == 2


def _edit(db, **changes):
    return rs.update_reminder_record(db, _actor(A), "rem-1", changes, now_utc=FROZEN)


@pytest.mark.asyncio
async def test_an_edit_that_moves_a_one_off_into_the_past_is_refused(quiet, monkeypatch):
    monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
    db = _Store()  # a one-off on 2099-06-15 08:00
    with pytest.raises(rs.ReminderPastError) as exc:
        await _edit(db, due_date="2026-06-10", due_time="09:00")    # earlier today: stored, never fired
    assert exc.value.spoken == rs.SPOKEN["past_time"] and db.updates() == []
    db = _Store(due_date="2026-06-10", due_time="13:00")
    with pytest.raises(rs.ReminderPastError):
        await _edit(db, due_time="9am")                              # only the time moved, onto today's stored date
    assert db.updates() == []
    await _edit(db, due_time="14:30")                                # positive control: later today
    assert any("due_time = ?" in w[0] and "14:30" in w[1] for w in db.updates())


@pytest.mark.asyncio
async def test_an_edit_that_leaves_the_time_alone_on_a_past_reminder_is_allowed(quiet, monkeypatch):
    monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
    for day in ("2026-06-10", "2026-06-09"):                         # earlier today, and yesterday
        db = _Store(due_date=day, due_time="09:00")
        await _edit(db, title="take pills with food")                # rename
        await _edit(db, title="take pills with food", due_date=day, due_time="9am")  # an edit form re-sends the row
        await _edit(db, is_active=False)                             # switch it off
        sets = [w[0] for w in db.updates()]
        assert len(sets) == 3 and not any("due_date = ?" in s or "due_time = ?" in s for s in sets)


@pytest.mark.asyncio
async def test_a_recurring_edit_is_re_anchored_not_refused(quiet, monkeypatch):
    monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
    db = _Store(recurring_pattern="FREQ=DAILY", due_date="2026-06-10", due_time="13:00")
    await _edit(db, due_time="09:00")                                # today's 09:00 has gone: tomorrow's still fires
    await _edit(db, due_date="2026-06-10", due_time="10:00")         # the anchor re-sent with an earlier time
    await _edit(db, due_date="today", due_time="08:00")              # a moved anchor: the next 08:00 is tomorrow
    sets = db.updates()
    assert len(sets) == 3 and sets[2][1][0] == "2026-06-11"


@pytest.mark.asyncio
async def test_the_keyword_path_says_the_refusal(monkeypatch):
    async def past(*_a, **_k):
        raise rs.ReminderPastError("due_date 'x' is already past", rs.SPOKEN["past_day"])

    monkeypatch.setattr("reminder_service.create_reminder_record", past)
    monkeypatch.setattr("database.get_db_ctx", _ctx(_Store()))
    reply = await execute_intent(Intent("reminder_create", {"title": "call the plumber", "date": "2001-01-01"}), A)
    assert reply == rs.SPOKEN["past_day"]


# ── the MCP tools go through the store (they used to write the table with their own SQL) ───────────────────────────────────
async def _tool(db, name, uid=A, **args):
    import mcp_server

    return await mcp_server._execute_tool(db, name, {"reminder_id": "rem-1", **args}, {"user_id": uid, "source": "transport"})


def _fixed_clock(monkeypatch, moment=datetime(2026, 6, 10, 23, 50, tzinfo=timezone.utc)):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(rs, "datetime", _Clock)
    monkeypatch.setattr("proactive.triggers.reminder_scan.datetime", _Clock)   # the household clock the date check reads
    return moment


@pytest.mark.asyncio
async def test_mcp_update_to_a_past_time_is_refused_and_writes_nothing(quiet, monkeypatch):
    monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
    _fixed_clock(monkeypatch, FROZEN)
    db = _Store()
    out = await _tool(db, "reminder_update", due_date="2026-06-10", due_time="09:00")
    assert out == {"error": rs.SPOKEN["past_time"], "status": "refused"} and db.updates() == [] and quiet["jobs"] == []


@pytest.mark.asyncio
async def test_mcp_update_that_leaves_the_time_alone_works_on_a_past_reminder(quiet, monkeypatch):
    monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
    _fixed_clock(monkeypatch, FROZEN)
    db = _Store(due_date="2026-06-10", due_time="09:00")
    out = await _tool(db, "reminder_update", title="take pills with food", due_time="9am")  # the time re-sent is no move
    assert out == {"id": "rem-1", "status": "updated"}
    (sql, _), = db.updates()
    assert "title = ?" in sql and "due_time" not in sql


@pytest.mark.asyncio
async def test_mcp_update_that_moves_the_time_goes_the_rest_route_and_reschedules(quiet, monkeypatch):
    monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
    _fixed_clock(monkeypatch, FROZEN)
    db = _Store(due_date="2026-06-10", due_time="13:00")
    out = await _tool(db, "reminder_update", due_time="14:30")
    assert out == {"id": "rem-1", "status": "updated"}
    (sql, params), = db.updates()
    assert "schedule_generation" in sql and "14:30" in params           # normalised, generation bumped (voids a claimed job)
    assert quiet["jobs"] == [{"rescheduled": "rem-1"}] and quiet["seam"] == ["edit"]   # as the REST edit asserts above
    assert await _tool(db, "reminder_update") == {"error": "No fields to update"}


@pytest.mark.asyncio
async def test_mcp_snooze_uses_the_store_and_keeps_the_due_fields(quiet, monkeypatch):
    _fixed_clock(monkeypatch)                      # 23:50 UTC: already tomorrow in a +08:00 household, still today in UTC
    db = _Store()
    out = await _tool(db, "reminder_snooze", minutes=30)
    assert set(out) == {"id", "snoozed_until", "status"} and out["id"] == "rem-1" and out["status"] == "snoozed"
    (sql, params), = db.updates()
    assert "due_date" not in sql and "due_time" not in sql and params[0] == "2026-06-11T00:20:00Z"   # a UTC instant, no offset
    assert quiet["seam"] == ["snooze"] and quiet["jobs"][0]["item_id"] == "rem-1"
    assert quiet["jobs"][0]["send_at"] == datetime(2026, 6, 11, 0, 20, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_mcp_delete_goes_through_the_store(quiet):
    db = _Store()
    assert await _tool(db, "reminder_delete") == {"id": "rem-1", "status": "deleted"}
    assert db.row["deleted"] == 1 and quiet["seam"] == ["cancel"]


@pytest.mark.parametrize("visibility", ["personal", "family"])
@pytest.mark.parametrize("tool,args", [("reminder_update", {"title": "x"}), ("reminder_snooze", {"minutes": 5}),
                                       ("reminder_delete", {})])
@pytest.mark.asyncio
async def test_mcp_another_member_cannot_change_a_reminder(quiet, visibility, tool, args):
    db = _Store(visibility=visibility)             # A's reminder; B asks
    out = await _tool(db, tool, uid=B, **args)
    assert "error" in out and out.get("status") in (None, "refused")
    assert db.updates() == [] and db.row["deleted"] == 0 and quiet["seam"] == []
    if visibility == "personal":
        assert out == {"error": "Reminder rem-1 not found"}         # not even a hint that it exists


# ── bug 3: the spoken "cancel that" ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_spoken_cancel_removes_only_the_speakers_own_reminder(monkeypatch, quiet):
    store = _Store(visibility="family")
    monkeypatch.setattr("database.get_db_ctx", _ctx(store))
    assert await execute_intent(Intent("reminder_cancel", {"title": "take pills"}), B) == rs.SPOKEN["cancel_not_found"]
    guest = await execute_intent(Intent("reminder_cancel", {"title": "take pills"}), "guest")
    assert guest.startswith("I need to know who you are")
    assert await execute_intent(Intent("reminder_cancel", {"title": ""}), A) == rs.SPOKEN["cancel_not_found"]
    assert store.row["deleted"] == 0
    assert await execute_intent(Intent("reminder_cancel", {"title": "Take pills"}), A) == "Reminder cancelled: Take pills."
    assert store.row["deleted"] == 1 and quiet["seam"] == ["cancel"]


# ── the "you haven't spoken to X" nudge: once per household day, not every tick of the 9 o'clock hour ─────────────────────
class _PeopleDb:
    """The people read plus ``proactive_pending``, which ``fire_notification`` writes for a send that went through."""

    def __init__(self, people):
        self.people, self.pending, self.pending_read_fails = people, [], False

    def sent(self, results, day):  # what fire_notification leaves behind for a send that worked
        self.pending += [{"user_id": r.user_id, "item_id": r.item_id, "trigger_type": r.trigger_type, "day": day} for r in results]

    def execute(self, sql, params=()):
        db = self

        class _C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def fetchall(self):
                if "FROM proactive_pending" in sql:
                    if db.pending_read_fails:
                        raise RuntimeError("pool timeout")
                    kind, tz_key, day = params
                    assert tz_key == "Australia/Perth"
                    return [r for r in db.pending if r["trigger_type"] == kind and r["day"] == day]
                return [dict(p) for p in db.people]
        return _C()


@pytest.mark.asyncio
async def test_people_health_nudge_fires_once_per_day(monkeypatch):
    """Once per person per household day, surviving a restart (a NEW trigger instance every tick here), a failed send
    retried, and the 21-day rule measured on the trigger's own clock."""
    import zoneinfo

    from proactive.triggers import people_health as ph

    monkeypatch.setattr(ph, "_ZOE_TZ", zoneinfo.ZoneInfo("Australia/Perth"))  # +08:00, no DST
    nine = datetime(2026, 6, 10, 1, 0, tzinfo=timezone.utc)  # 09:00 local
    ago = lambda days: (nine - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    db = _PeopleDb([{"id": "p1", "name": "Person One", "user_id": A, "last_contacted_at": ago(30), "context": ""},
                    {"id": "p2", "name": "Person Two", "user_id": A, "last_contacted_at": ago(10), "context": ""}])

    async def tick(minutes, days=0):
        return await ph.PeopleHealthTrigger().check(db, now_utc=nine + timedelta(days=days, minutes=minutes))

    first = await tick(0)
    assert [(r.item_id, r.message) for r in first] == [("p1", "You haven't spoken to Person One in 4 weeks.")]
    assert [r.item_id for r in await tick(5)] == ["p1"]       # that send failed (no pending row): retried
    db.sent(first, "2026-06-10")
    assert await tick(10) == []                                # sent: a restart in the same hour does not repeat it
    db.pending_read_fails = True
    assert await tick(15) == []                                # cannot tell what was sent: send nothing this tick
    db.pending_read_fails = False
    assert [r.item_id for r in await tick(0, days=1)] == ["p1"]  # tomorrow fires again
