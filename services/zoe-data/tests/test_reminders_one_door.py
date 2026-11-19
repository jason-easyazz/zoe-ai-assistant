"""The reminders ONE DOOR: every entry point (the screen's REST routes, the brain's MCP tools, the spoken path, an accepted
suggestion) changes a reminder through ``reminder_service`` under one ownership rule, and the three faults fixed with it:

* an accepted reminder suggestion was a raw INSERT with no day or time (it could never fire): it now goes through the door with
  its day and time, in the suggestion's own transaction, and an offer with no readable day or time stays open;
* the spoken "cancel that" (``reminder_cancel``) had no handler: it cancels the speaker's own newest reminder of that title;
* a one-off for a clock time already gone today was stored and never fired: refused on create and on an edit that moves it there
  (a spoken turn is told so in words).

Negative controls run for this file (each broke the code, saw the named test fail, restored): ``refuse_past_due`` made a no-op ->
``test_a_time_already_gone_today_is_refused`` fails; ``acknowledged = 0`` dropped from the snooze UPDATE ->
``test_snoozing_a_done_reminder_still_fires_through_the_trigger`` fails; the ``reminder_cancel`` branch removed from
``execute_intent`` -> ``test_spoken_cancel_removes_only_the_speakers_own_reminder`` fails; ``own_only=True`` dropped from the MCP
delete -> ``test_mcp_another_member_cannot_change_a_reminder`` fails; the suggestion's reminder created with no ``after_commit``
list (job and broadcast inside the transaction) -> ``test_accepted_suggestion_is_one_transaction_on_one_connection`` fails.
Invented people and words only."""

import asyncio
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
    """No role matrix, panels or scheduler; records scheduled jobs."""
    jobs = []

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
    return {"jobs": jobs}


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
    assert "title = ?" in sql and "visibility" not in sql
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
    assert quiet["jobs"][0]["item_id"] == "rem-1"


@pytest.mark.asyncio
async def test_cancel_own_only_is_stricter(quiet):
    db = _Store(visibility="family")
    with pytest.raises(HTTPException) as exc:
        await rs.delete_reminder_record(db, _actor(B), "rem-1", own_only=True)
    assert exc.value.status_code == 404 and db.updates() == []
    await rs.delete_reminder_record(db, _actor(B), "rem-1")   # REST: a family reminder is cancellable by any member
    assert db.row["deleted"] == 1


# ── bug 2: an accepted reminder suggestion ────────────────────────────────────────────────────────────────────────────────

# ── fault 1: an accepted reminder suggestion goes through the door, in the suggestion's own transaction ────────────────────
class _PgState:
    def __init__(self, slots=None):
        self.reminders, self.notifications, self.resolved = {}, [], 0
        self.slots = slots or '{"title": "book the vet", "date": "2099-06-16", "time": "15:00"}'
        self.fail_after_insert = False


class _PgConn:
    """A fake asyncpg connection (the one ``AsyncpgCompat`` wraps): ``transaction()`` snapshots the state and puts it back on an
    exception, and every statement is recorded with the transaction depth it ran at. A bare ``commit`` does nothing."""

    def __init__(self, state=None):
        self.st = state or _PgState()
        self.depth, self.log = 0, []

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                conn.depth += 1
                self.snap = (dict(conn.st.reminders), list(conn.st.notifications), conn.st.resolved)

            async def __aexit__(self, exc_type, *_):
                conn.depth -= 1
                if exc_type is not None:
                    conn.st.reminders, conn.st.notifications, conn.st.resolved = self.snap
        return _Tx()

    async def fetch(self, sql, *args):
        self.log.append((sql.split()[0], self.depth))
        if "FROM users" in sql:
            return [{"id": args[0], "role": "user", "name": "Member"}]
        if "FROM reminders" in sql:
            if self.st.fail_after_insert:
                raise RuntimeError("connection lost")
            return [dict(self.st.reminders[args[0]])] if args[0] in self.st.reminders else []
        return []

    async def fetchrow(self, sql, *args):
        self.log.append(("fetchrow", self.depth))
        if "FROM pending_suggestions" in sql and not self.st.resolved:
            return {"action_type": "reminder_create", "pre_filled_slots": self.st.slots}
        return None

    async def execute(self, sql, *args):
        self.log.append((" ".join(sql.split()[:3]), self.depth))
        if "INSERT INTO reminders" in sql:
            self.st.reminders[args[0]] = {"id": args[0], "user_id": args[1], "title": args[2], "due_date": args[7],
                                          "due_time": args[8], "is_active": 1, "acknowledged": 0, "deleted": 0}
        elif "INSERT INTO notifications" in sql:
            self.st.notifications.append(args)
        elif "UPDATE pending_suggestions SET resolved = 1" in sql:
            self.st.resolved = 1
        return "OK 1"


class _PgPool:
    def __init__(self, state):
        self.state, self.conns = state, []

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                pool.conns.append(_PgConn(pool.state))
                return pool.conns[-1]

            async def __aexit__(self, *_):
                pass
        return _Ctx()


def _accept_world(monkeypatch, state):
    pool, scheduled, announced = _PgPool(state), [], []

    async def allow(*_a, **_k):
        return None

    async def schedule(_db, row, **_k):
        scheduled.append((row["id"], pool.conns[-1].depth))

    async def announce(*a, **k):
        announced.append(a)

    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: pool)
    monkeypatch.setattr("reminder_service.require_feature_access", allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", announce)
    monkeypatch.setattr("proactive.triggers.reminder_scan.schedule_due_reminder", schedule)
    monkeypatch.setattr("anticipation.mode", lambda: "off")
    return pool, scheduled, announced


@pytest.mark.asyncio
async def test_accepted_suggestion_is_one_transaction_on_one_connection(monkeypatch):
    state = _PgState()
    state.fail_after_insert = True                                       # fails AFTER the reminder insert
    pool, scheduled, announced = _accept_world(monkeypatch, state)
    out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out["ok"] is False
    assert state.reminders == {} and state.notifications == [] and state.resolved == 0   # insert and notice rolled back together
    assert scheduled == [] and announced == []                           # no job and no panel news for a reminder that never was
    state.fail_after_insert = False                                      # the retry
    out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out["ok"] is True and len(state.reminders) == 1 and state.resolved == 1       # exactly one reminder, WITH its day and time
    (row,) = state.reminders.values()
    assert (row["due_date"], row["due_time"]) == ("2099-06-16", "15:00")
    assert scheduled == [(row["id"], 0)] and len(announced) == 1         # job and broadcast only after the commit (depth 0)
    assert {d for c in pool.conns for _, d in c.log} == {1}              # EVERY statement inside the transaction


@pytest.mark.asyncio
async def test_a_reminder_offer_without_a_time_stays_open(monkeypatch):
    conn = _PgConn()
    with pytest.raises(ValueError, match="reminder_needs_time"):
        await pending_suggestions._execute_action(conn, "reminder_create", {"title": "book the vet"}, A)
    assert conn.st.reminders == {} and conn.log == []
    state = _PgState(slots='{"title": "book the vet"}')
    _accept_world(monkeypatch, state)
    assert await pending_suggestions.execute_suggestion("sug-1", A) == {"ok": False, "error": "reminder_needs_time"}
    assert state.reminders == {} and state.resolved == 0


# ── fault 3: a past day or time: refused, and the person is told ───────────────────────────────────────────────────────────────────
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
    assert quiet["jobs"] == [{"rescheduled": "rem-1"}]   # as the REST edit asserts above
    assert await _tool(db, "reminder_update") == {"error": "No fields to update"}


@pytest.mark.asyncio
async def test_mcp_snooze_uses_the_store_and_keeps_the_due_fields(quiet, monkeypatch):
    _fixed_clock(monkeypatch)                      # 23:50 UTC: already tomorrow in a +08:00 household, still today in UTC
    db = _Store()
    out = await _tool(db, "reminder_snooze", minutes=30)
    assert set(out) == {"id", "snoozed_until", "status"} and out["id"] == "rem-1" and out["status"] == "snoozed"
    (sql, params), = db.updates()
    assert "due_date" not in sql and "due_time" not in sql and params[0] == "2026-06-11T00:20:00Z"   # a UTC instant, no offset
    assert quiet["jobs"][0]["item_id"] == "rem-1"
    assert quiet["jobs"][0]["send_at"] == datetime(2026, 6, 11, 0, 20, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_mcp_delete_goes_through_the_store(quiet):
    db = _Store()
    assert await _tool(db, "reminder_delete") == {"id": "rem-1", "status": "deleted"}
    assert db.row["deleted"] == 1


@pytest.mark.parametrize("visibility", ["personal", "family"])
@pytest.mark.parametrize("tool,args", [("reminder_update", {"title": "x"}), ("reminder_snooze", {"minutes": 5}),
                                       ("reminder_delete", {})])
@pytest.mark.asyncio
async def test_mcp_another_member_cannot_change_a_reminder(quiet, visibility, tool, args):
    db = _Store(visibility=visibility)             # A's reminder; B asks
    out = await _tool(db, tool, uid=B, **args)
    assert "error" in out and out.get("status") in (None, "refused")
    assert db.updates() == [] and db.row["deleted"] == 0
    if visibility == "personal":
        assert out == {"error": "Reminder rem-1 not found"}         # not even a hint that it exists


# ── fault 2: the spoken "cancel that" ───────────────────────────────────────────────────────────────────────────────────────
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
    assert store.row["deleted"] == 1


# ── a snooze re-arms a reminder that was marked done (the one door keeps what the old brain tool did) ───────────────────────
class _E2EDb:
    """One reminder row that APPLIES the store's snooze UPDATE, the scheduled row's claim, and the trigger's supersede read."""

    def __init__(self, **row):
        self.row = {"id": "rem-1", "user_id": A, "title": "take pills", "visibility": "personal", "due_date": "2099-06-15",
                    "due_time": "08:00", "recurring_pattern": None, "is_active": 1, "acknowledged": 1, "deleted": 0,
                    "snoozed_until": None, "schedule_generation": 3, **row}
        self.job_generation = None

    def execute(self, sql, params=()):
        db, norm, p = self, " ".join(sql.split()), list(params)

        class _R:
            async def _run(self):
                r = db.row
                if norm.startswith("SELECT") and "FROM users" in norm:
                    return _Cursor({"id": p[0], "role": "user", "name": "Member"})
                if norm.startswith("SELECT is_active, acknowledged, deleted, schedule_generation"):
                    return _Cursor(dict(r))
                if norm.startswith("SELECT schedule_generation FROM reminders"):
                    return _Cursor({"schedule_generation": r["schedule_generation"]})
                if norm.startswith("SELECT"):
                    return _Cursor(dict(r))
                if norm.startswith("UPDATE reminders SET snoozed_until"):
                    r["snoozed_until"] = p[0]
                    if "acknowledged = 0" in norm:
                        r["acknowledged"] = 0
                    r["schedule_generation"] += 1
                if norm.startswith("UPDATE proactive_scheduled SET claimed_at"):
                    return _Cursor({"id": "sched-1", "item_id": "rem-1", "schedule_generation": db.job_generation})
                return _Cursor()

            def __await__(self):
                return self._run().__await__()

            async def __aenter__(self):
                return await self._run()

            async def __aexit__(self, *_):
                return False
        return _R()

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_snoozing_a_done_reminder_still_fires_through_the_trigger(quiet, monkeypatch):
    import contextlib

    from proactive.triggers import reminders as trig

    db = _E2EDb()                                              # acknowledged = 1: it was marked done
    delivered = []

    @contextlib.asynccontextmanager
    async def compat():
        yield db

    async def schedule(**kw):                                  # the store's one-shot: record the generation the job carries
        db.job_generation = await trig._reminder_generation(kw["item_id"])

    async def fire(**kw):
        delivered.append(kw["item_id"])

    monkeypatch.setattr(trig, "_get_compat_db", compat)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", schedule)
    monkeypatch.setattr("proactive.engine.fire_notification", fire)
    await rs.snooze_reminder_record(db, _actor(A), "rem-1", 10, now_utc=FROZEN)
    assert db.row["acknowledged"] == 0 and db.row["snoozed_until"] == "2026-06-10T04:10:00Z"
    await trig._fire_reminder("sched-1", A, "take pills")      # the job comes due: the real claim, supersede check and delivery
    assert delivered == ["rem-1"]



# ── a weekday hint keeps its clock time, or the offer stays open ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("hint,expected", [
    ("friday", ("friday", None)),                       # unchanged
    ("friday at 3pm", ("friday", "15:00")),             # the time is read, not dropped
    ("on friday at 3:30pm", ("on friday", "15:30")),
    ("friday morning", (None, None)),                   # clock wording we cannot read: refused, never "a day with the time dropped"
    ("friday at 3", (None, None)),
])
def test_a_weekday_hint_keeps_its_clock_time_or_is_refused(hint, expected):
    assert pending_suggestions._suggested_when("book the vet", {"when_hint": hint}) == expected


@pytest.mark.asyncio
async def test_an_accepted_weekday_hint_with_a_time_makes_a_reminder_at_that_time(monkeypatch):
    from datetime import date

    state = _PgState(slots='{"title": "book the vet", "when_hint": "friday at 3pm"}')
    _accept_world(monkeypatch, state)
    out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out["ok"] is True
    (row,) = state.reminders.values()
    assert row["due_time"] == "15:00" and date.fromisoformat(row["due_date"]).weekday() == 4       # a Friday, at 15:00
    state2 = _PgState(slots='{"title": "book the vet", "when_hint": "friday morning"}')
    _accept_world(monkeypatch, state2)
    assert await pending_suggestions.execute_suggestion("sug-1", A) == {"ok": False, "error": "reminder_needs_time"}
    assert state2.reminders == {} and state2.resolved == 0
