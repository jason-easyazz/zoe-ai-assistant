"""WP-40: reminder entry points go through the reminder_service verbs and ONE ownership rule (synthetic people)."""

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
class _PgState:
    """The database: one suggestion row, the reminders, and the ROW LOCK a claiming UPDATE takes until its transaction ends."""

    def __init__(self, slots=None, action="reminder_create", rollback=True):
        import asyncio

        self.action, self.rollback = action, rollback             # rollback=False: a connection whose transaction does not undo

        self.reminders, self.notifications, self.resolved = {}, [], 0
        self.slots = slots or '{"title": "book the vet", "date": "2099-06-16", "time": "15:00"}'
        self.row_lock, self.fail_after_insert = asyncio.Lock(), False


class _PgConn:
    """A fake asyncpg connection (the one ``AsyncpgCompat`` wraps) with REAL transaction semantics: ``transaction()`` snapshots the
    state and puts it back on an exception, and every statement is recorded with the transaction depth it ran at. A bare
    ``commit`` does nothing (as on Postgres). The claiming ``UPDATE ... WHERE resolved = 0`` blocks while another connection's
    transaction holds the row, then re-reads it (READ COMMITTED), as Postgres does."""

    def __init__(self, state=None):
        self.st = state or _PgState()
        self.depth, self.log, self.held = 0, [], False

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                conn.depth += 1
                st = conn.st
                self.snap = (dict(st.reminders), list(st.notifications), st.resolved)

            async def __aexit__(self, exc_type, *_):
                conn.depth -= 1
                st = conn.st
                if exc_type is not None and st.rollback:
                    st.reminders, st.notifications, st.resolved = self.snap
                if conn.held:
                    conn.held = False
                    st.row_lock.release()
        return _Tx()

    async def fetch(self, sql, *args):
        import asyncio

        await asyncio.sleep(0)
        self.log.append((sql.split()[0], self.depth))
        if "FROM users" in sql:
            return [{"id": args[0], "role": "user", "name": "Member"}]
        if "FROM reminders" in sql:
            if self.st.fail_after_insert:
                raise RuntimeError("connection lost")
            return [dict(self.st.reminders[args[0]])] if args[0] in self.st.reminders else []
        return []

    async def fetchrow(self, sql, *args):
        import asyncio

        self.log.append(("fetchrow", self.depth))
        if "UPDATE pending_suggestions" in sql:       # the claim: first statement of the transaction
            assert self.depth == 1 and "resolved = 0" in sql
            await self.st.row_lock.acquire()
            self.held = True
            await asyncio.sleep(0)
            if self.st.resolved:
                return None
            self.st.resolved = 1
            return {"action_type": self.st.action, "pre_filled_slots": self.st.slots}
        return None

    async def execute(self, sql, *args):
        import asyncio

        await asyncio.sleep(0)
        self.log.append((" ".join(sql.split()[:3]), self.depth))
        if "INSERT INTO reminders" in sql:
            self.st.reminders[args[0]] = {"id": args[0], "user_id": args[1], "title": args[2], "due_date": args[7],
                                          "due_time": args[8], "is_active": 1, "acknowledged": 0, "deleted": 0}
        elif "INSERT INTO notifications" in sql:
            self.st.notifications.append(args)
        elif "UPDATE pending_suggestions SET resolved = 0" in sql:
            self.st.resolved = 0
        return "OK 1"


class _PgPool:
    def __init__(self, state):
        self.state, self.acquired, self.conns = state, 0, []

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                pool.acquired += 1
                conn = _PgConn(pool.state)
                pool.conns.append(conn)
                return conn

            async def __aexit__(self, *_):
                pass
        return _Ctx()


def _accept_world(monkeypatch, state):
    pool, scheduled, announced = _PgPool(state), [], []

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
    return pool, scheduled, announced


@pytest.mark.asyncio
async def test_accepted_suggestion_is_one_transaction_on_one_connection(monkeypatch):
    state = _PgState()
    state.fail_after_insert = True                                       # fails AFTER the reminder insert
    pool, scheduled, announced = _accept_world(monkeypatch, state)

    out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out["ok"] is False
    assert state.reminders == {} and state.notifications == [] and state.resolved == 0   # claim, insert and notice rolled back together
    assert scheduled == [] and announced == []                           # no job and no panel news for a reminder that never was

    state.fail_after_insert = False                                      # the retry
    out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out["ok"] is True and len(state.reminders) == 1 and state.resolved == 1       # exactly one reminder
    assert scheduled == list(state.reminders) and len(announced) == 1    # job and broadcast only after the commit
    assert pool.acquired == 2 and {d for c in pool.conns for _, d in c.log} == {1}   # one connection per attempt; EVERY statement in the transaction


@pytest.mark.asyncio
async def test_two_accepts_racing_make_one_reminder_one_job(monkeypatch):
    import asyncio

    state = _PgState()
    pool, scheduled, announced = _accept_world(monkeypatch, state)
    first, second = await asyncio.gather(pending_suggestions.execute_suggestion("sug-1", A),
                                         pending_suggestions.execute_suggestion("sug-1", A))
    assert sorted([first["ok"], second["ok"]]) == [False, True]
    loser = first if not first["ok"] else second
    assert loser == {"ok": False, "error": "not_found"}                  # the answer a later accept of a taken offer gets
    assert len(state.reminders) == 1 and state.resolved == 1 and len(scheduled) == 1 and len(announced) == 1
    assert pool.acquired == 2


@pytest.mark.asyncio
async def test_a_failed_winner_hands_the_offer_to_the_waiting_accept(monkeypatch):
    import asyncio

    state = _PgState()
    state.fail_after_insert = True
    pool, scheduled, _ = _accept_world(monkeypatch, state)
    state.fail_after_insert = True

    async def heal():                                                    # the second accept runs once the first has rolled back
        while state.row_lock.locked() or not pool.conns:
            await asyncio.sleep(0)
        state.fail_after_insert = False
        return await pending_suggestions.execute_suggestion("sug-1", A)

    first, second = await asyncio.gather(pending_suggestions.execute_suggestion("sug-1", A), heal())
    assert first["ok"] is False and second["ok"] is True and len(state.reminders) == 1 and len(scheduled) == 1


@pytest.mark.parametrize("exit_name", ["unknown_action", "needs_time", "past_day", "dispute_not_applied", "forget_alias_not_applied",
                                       "action_raises", "scrub_raises"])
@pytest.mark.asyncio
async def test_every_exit_that_is_not_a_success_leaves_the_question_open(monkeypatch, exit_name):
    """Every way out of ``execute_suggestion`` short of success gives the offer back, even on a connection whose transaction does
    not undo (the claim is undone explicitly as well as by the rollback)."""
    import memory_disputes
    import memory_forget_alias

    action, slots = "reminder_create", '{"title": "book the vet"}'
    if exit_name == "unknown_action":
        action = "no_such_action"
    elif exit_name == "past_day":
        slots = '{"title": "book the vet", "date": "2001-01-01", "time": "9am"}'
    elif exit_name == "dispute_not_applied":
        action = "memory_dispute"
    elif exit_name in ("forget_alias_not_applied", "scrub_raises"):
        action = "forget_alias"
    state = _PgState(slots=slots, action=action, rollback=False)
    pool, scheduled, announced = _accept_world(monkeypatch, state)

    async def no(*_a, **_k):
        return False

    async def boom(*_a, **_k):
        raise RuntimeError("store down")

    monkeypatch.setattr(memory_disputes, "resolve", no)
    monkeypatch.setattr(memory_forget_alias, "forget_confirmed", no)
    if exit_name == "action_raises":
        monkeypatch.setattr(pending_suggestions, "_execute_action", boom)
    if exit_name == "scrub_raises":
        async def yes(*_a, **_k):
            return True
        monkeypatch.setattr(memory_forget_alias, "forget_confirmed", yes)
        monkeypatch.setattr(pending_suggestions, "_scrub_forget_alias", boom)
    out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out["ok"] is False and state.resolved == 0 and state.reminders == {} and scheduled == [] and announced == []


@pytest.mark.asyncio
async def test_an_unreadable_when_hint_stays_open_and_is_never_stored_or_logged(monkeypatch, caplog):
    import logging

    marker = "zxqv blorp wibble"
    state = _PgState(slots='{"title": "book the vet", "when_hint": "%s"}' % marker)
    pool, scheduled, announced = _accept_world(monkeypatch, state)
    with caplog.at_level(logging.DEBUG):
        out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out == {"ok": False, "error": "reminder_needs_time"}
    assert state.reminders == {} and state.resolved == 0 and scheduled == []          # the offer stays open
    for rec in caplog.records:
        assert marker not in rec.getMessage() and marker not in repr(rec.args) and not rec.exc_info
    # an unreadable explicit date and time are the same: fixed code, nothing quoted
    for slots in ('{"title": "t", "date": "%s", "time": "9am"}' % marker, '{"title": "t", "date": "2099-06-16", "time": "%s"}' % marker):
        state.slots = slots
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            out = await pending_suggestions.execute_suggestion("sug-1", A)
        assert out == {"ok": False, "error": "reminder_needs_time"} and state.reminders == {}
        assert all(marker not in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_past_day_is_refused_without_logging_the_words(monkeypatch, caplog):
    import logging

    state = _PgState(slots='{"title": "book the vet", "date": "2001-01-01", "time": "9am"}')
    _accept_world(monkeypatch, state)
    with caplog.at_level(logging.DEBUG):
        out = await pending_suggestions.execute_suggestion("sug-1", A)
    assert out == {"ok": False, "error": rs.SPOKEN["past_day"]} and state.reminders == {} and state.resolved == 0
    assert all("2001" not in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_reminder_offer_without_a_time_stays_open():
    conn = _PgConn()
    with pytest.raises(ValueError, match="reminder_needs_time"):
        await pending_suggestions._execute_action(conn, "reminder_create", {"title": "book the vet"}, A)
    assert conn.st.reminders == {} and conn.log == []
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


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Nothing in this file may reach a real endpoint (the proactive composer POSTs to the LOCAL BRAIN): an un-stubbed composer or any
    httpx send raises, loudly, instead of reaching the live system."""
    import httpx

    def refuse(*_a, **_k):
        raise AssertionError("a test reached for the network (stub the composer / the client it uses)")

    async def refuse_async(*_a, **_k):
        refuse()

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_async)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr("proactive.composer.compose_message", refuse_async)
    monkeypatch.setattr("proactive.engine.compose_message", refuse_async)


class _Claims:
    """``app_settings`` as ``nudge_claims`` uses it: a unique key, ``ON CONFLICT DO NOTHING`` (rowcount 1 / 0), the prune and the erase."""

    def __init__(self):
        self.rows, self.marks = {}, {}       # marks: member -> the durable erase mark (memory_forgotten.forgotten_at)

    def execute(self, sql, params=()):
        norm, p = " ".join(sql.split()), list(params)
        store = self

        class _Cur:
            rowcount = 0

        async def run():
            cur = _Cur()
            if norm.startswith("INSERT INTO app_settings") and "SELECT CAST" in norm:
                key, at, uid, _mark_key, started = p
                if (uid not in store.marks or store.marks[uid] < started) and key not in store.rows:   # ONE conditional statement
                    store.rows[key] = {"updated_at": at}
                    cur.rowcount = 1
            elif norm.startswith("INSERT INTO app_settings"):
                if p[0] not in store.rows:
                    store.rows[p[0]] = {"updated_at": p[1]}
                    cur.rowcount = 1
            elif norm.startswith("DELETE FROM app_settings WHERE key LIKE ? AND updated_at < ?"):
                for k in [k for k, v in store.rows.items() if k.startswith(p[0].rstrip("%")) and v["updated_at"] < p[1]]:
                    del store.rows[k]
            elif norm.startswith("DELETE FROM app_settings WHERE key = ?"):
                store.rows.pop(p[0], None)
            elif norm.startswith("DELETE FROM app_settings WHERE key LIKE ? ESCAPE"):
                import re as _re

                rx = _re.compile("^" + _re.escape(p[0].replace("\\_", "_")).replace("%", ".*").replace("\\.\\*", ".*") + "$")
                for k in [k for k in store.rows if rx.match(k)]:
                    del store.rows[k]
                    cur.rowcount += 1
            return cur
        return run()

    async def commit(self):
        pass


@pytest.fixture(autouse=True)
def claims(monkeypatch):
    import contextlib

    import nudge_claims

    store = _Claims()

    @contextlib.asynccontextmanager
    async def db():
        yield store

    import user_erase_gate

    user_erase_gate.reset()
    monkeypatch.setattr(nudge_claims, "_db", db)
    yield store
    user_erase_gate.reset()


# ── the "you haven't spoken to X" nudge: once per household day, not every tick of the 9 o'clock hour ─────────────────────
class _PeopleDb:
    """The people read plus ``proactive_pending``, which ``fire_notification`` writes for a send that went through."""

    def __init__(self, people):
        self.people, self.pending, self.pending_read_fails = people, [], False
        self.after_people_read = None                # an async hook run between the trigger's read and its claim

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
                await asyncio.sleep(0)                     # a real read yields: two ticks can interleave between read and send
                if "FROM proactive_pending" in sql:
                    if db.pending_read_fails:
                        raise RuntimeError("pool timeout")
                    kind, tz_key, day = params
                    assert tz_key == "Australia/Perth"
                    return [r for r in db.pending if r["trigger_type"] == kind and r["day"] == day]
                rows = [dict(p) for p in db.people]
                if db.after_people_read is not None:
                    await db.after_people_read()
                return rows
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
    import nudge_claims
    assert [r.item_id for r in await tick(5)] == []            # the claim is held while the send is in flight: nobody else sends it
    await nudge_claims.release(first[0].context["claim_key"])  # that send failed (fire_notification releases): retried
    assert [r.item_id for r in await tick(5)] == ["p1"]
    db.sent(first, "2026-06-10")
    assert await tick(10) == []                                # sent: a restart in the same hour does not repeat it
    db.pending_read_fails = True
    assert await tick(15) == []                                # cannot tell what was sent: send nothing this tick
    db.pending_read_fails = False
    assert [r.item_id for r in await tick(0, days=1)] == ["p1"]  # tomorrow fires again


def test_no_logger_call_in_the_reminder_paths_carries_exception_text():
    """Log pin (the privacy sweep's rule): the suggestion and store paths log an exception's TYPE, never its text (a refusal quotes
    the person's words) and never a traceback (``logger.exception``). Negative control: the scanner flags the old form."""
    import ast
    from pathlib import Path

    def bad(src):
        hits = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "debug", "info", "warning", "error", "exception", "critical") and getattr(node.func.value, "id", "") == "logger":
                if node.func.attr == "exception" or any(isinstance(a, ast.Name) and a.id in ("exc", "e", "err") for a in node.args[1:]):
                    hits.append(node.lineno)
        return hits

    assert bad('logger.warning("x failed: %s", exc)\n') == [1] and bad('logger.exception("x")\n') == [1]
    root = Path(__file__).resolve().parents[1]
    for name in ("pending_suggestions.py", "reminder_service.py"):
        assert bad((root / name).read_text(encoding="utf-8")) == [], name


# ── the nudge's "sent today" mark means a send went through (the row is removed when no lane delivered) ───────────────────
def _engine_world(monkeypatch, db, *, push, spoken=False):
    """``fire_notification`` over a fake pending table (the ``_PeopleDb``): the row is written, then the lanes run. ``push`` is
    the web-push count, or an exception class to raise from that lane."""
    import contextlib

    from proactive import engine

    async def create_pending(*, user_id, message, trigger_type, item_id, context, **_k):
        db.pending.append({"user_id": user_id, "item_id": item_id, "trigger_type": trigger_type, "day": "2026-06-10", "id": "pid-1"})
        return "pid-1"

    class _Del:
        async def execute(self, sql, params=()):
            if sql.startswith("DELETE FROM proactive_pending"):
                db.pending = [r for r in db.pending if r["id"] != params[0]]

        async def commit(self):
            pass

    @contextlib.asynccontextmanager
    async def compat():
        yield _Del()

    async def send_push(**_k):
        if isinstance(push, type):
            raise push("push lane down")
        return push

    async def speak(**_k):
        if spoken:
            import delivery_tally

            delivery_tally.note()      # the real spoken lane notes the delivery the moment it is enqueued
        return spoken

    async def compose(_trigger_type, _ctx, fallback, **_k):    # the real composer POSTs to the local brain: never from a test
        return fallback

    monkeypatch.setattr(engine, "compose_message", compose)
    monkeypatch.setattr(engine, "create_pending", create_pending)
    monkeypatch.setattr(engine, "_get_compat_db", compat)
    monkeypatch.setattr(engine, "_send_push", send_push)
    monkeypatch.setattr(engine, "_maybe_speak_notification", speak)
    monkeypatch.setattr(engine, "_is_in_quiet_hours", lambda: False)
    monkeypatch.setattr(engine, "_reminder_phone_mode", lambda: "off")
    return engine


@pytest.mark.parametrize("lane,push,spoken,row_stays,raises", [
    ("every lane raises", RuntimeError, False, False, True),      # the row was written, the send blew up: retried next tick
    ("no lane reached anyone", 0, False, False, False),           # nothing delivered and nothing raised: retried too
    ("push reached one", 1, False, True, False),                  # one lane succeeded: not repeated
    ("only the spoken lane", 0, True, True, False),
])
@pytest.mark.asyncio
async def test_a_nudge_is_marked_sent_only_once_a_lane_delivered(monkeypatch, claims, lane, push, spoken, row_stays, raises):
    import zoneinfo

    from proactive.triggers import people_health as ph

    monkeypatch.setattr(ph, "_ZOE_TZ", zoneinfo.ZoneInfo("Australia/Perth"))
    nine = datetime(2026, 6, 10, 1, 0, tzinfo=timezone.utc)
    db = _PeopleDb([{"id": "p1", "name": "Person One", "user_id": A,
                     "last_contacted_at": (nine - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "context": ""}])
    engine = _engine_world(monkeypatch, db, push=push, spoken=spoken)
    trig = ph.PeopleHealthTrigger()
    (result,) = await trig.check(db, now_utc=nine)
    kw = dict(user_id=result.user_id, message=result.message, trigger_type=result.trigger_type, item_id=result.item_id,
              context=result.context, retract_if_undelivered=trig.retract_if_undelivered)
    if raises:
        with pytest.raises(RuntimeError):
            await engine.fire_notification(**kw)
    else:
        assert await engine.fire_notification(**kw) is True or not (push or spoken)
    assert bool(db.pending) is row_stays
    assert bool(claims.rows) is row_stays                             # the claim goes back with the row: kept only after a delivery
    again = await ph.PeopleHealthTrigger().check(db, now_utc=nine + timedelta(minutes=5))
    assert (again == []) is row_stays                                 # retried on the next tick, or not repeated


@pytest.mark.asyncio
async def test_a_trigger_that_does_not_ask_keeps_its_row_when_nothing_delivered(monkeypatch):
    db = _PeopleDb([])
    engine = _engine_world(monkeypatch, db, push=0)
    out = await engine.fire_notification(user_id=A, message="m", trigger_type="morning_checkin", item_id="")
    assert out is False and len(db.pending) == 1                    # the default is unchanged (the brief's row is read elsewhere)


# ── a snooze re-arms a done reminder: the job it schedules is DELIVERED by the real trigger path ──────────────────────────
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


def _nudge_world(monkeypatch):
    import zoneinfo

    from proactive.triggers import people_health as ph

    monkeypatch.setattr(ph, "_ZOE_TZ", zoneinfo.ZoneInfo("Australia/Perth"))
    nine = datetime(2026, 6, 10, 1, 0, tzinfo=timezone.utc)
    db = _PeopleDb([{"id": "p1", "name": "Person One", "user_id": A,
                     "last_contacted_at": (nine - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "context": ""}])
    return ph, nine, db


@pytest.mark.asyncio
async def test_two_overlapping_ticks_send_the_nudge_once(monkeypatch, claims):
    ph, nine, db = _nudge_world(monkeypatch)
    first, second = await asyncio.gather(ph.PeopleHealthTrigger().check(db, now_utc=nine),
                                         ph.PeopleHealthTrigger().check(db, now_utc=nine))   # both read "no mark today" ...
    assert len(first) + len(second) == 1                          # ... only the one whose claim landed sends
    assert list(claims.rows) == [f"nudge:people_health:{A}:p1:2026-06-10"]   # ids and a date, no name


@pytest.mark.asyncio
async def test_a_restart_after_a_delivered_nudge_does_not_repeat_it(monkeypatch, claims):
    ph, nine, db = _nudge_world(monkeypatch)
    engine = _engine_world(monkeypatch, db, push=1)
    trig = ph.PeopleHealthTrigger()
    (result,) = await trig.check(db, now_utc=nine)
    await engine.fire_notification(user_id=result.user_id, message=result.message, trigger_type=result.trigger_type,
                                   item_id=result.item_id, context=result.context, retract_if_undelivered=True)
    db.pending.clear()                                            # even with the pending row gone (another process, a purge) ...
    assert await ph.PeopleHealthTrigger().check(db, now_utc=nine + timedelta(minutes=5)) == []   # ... the claim holds


@pytest.mark.asyncio
async def test_a_cancel_right_after_the_spoken_enqueue_keeps_the_mark_and_the_claim(monkeypatch, claims):
    ph, nine, db = _nudge_world(monkeypatch)
    engine = _engine_world(monkeypatch, db, push=asyncio.CancelledError, spoken=True)   # speech took it, then the task is cancelled
    (result,) = await ph.PeopleHealthTrigger().check(db, now_utc=nine)
    with pytest.raises(asyncio.CancelledError):
        await engine.fire_notification(user_id=result.user_id, message=result.message, trigger_type=result.trigger_type,
                                       item_id=result.item_id, context=result.context, retract_if_undelivered=True)
    assert len(db.pending) == 1 and len(claims.rows) == 1         # speech was queued: the row and the claim stay
    assert await ph.PeopleHealthTrigger().check(db, now_utc=nine + timedelta(minutes=5)) == []


@pytest.mark.asyncio
async def test_a_failure_before_the_row_is_written_releases_the_claim(monkeypatch, claims):
    ph, nine, db = _nudge_world(monkeypatch)
    engine = _engine_world(monkeypatch, db, push=1)

    async def broken(**_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(engine, "create_pending", broken)
    (result,) = await ph.PeopleHealthTrigger().check(db, now_utc=nine)
    with pytest.raises(RuntimeError):
        await engine.fire_notification(user_id=result.user_id, message=result.message, trigger_type=result.trigger_type,
                                       item_id=result.item_id, context=result.context, retract_if_undelivered=True)
    assert claims.rows == {}                                      # nothing went out: the next tick may claim and try again
    assert len(await ph.PeopleHealthTrigger().check(db, now_utc=nine + timedelta(minutes=5))) == 1


@pytest.mark.asyncio
async def test_claims_are_pruned_and_erased_with_the_member(claims):
    import nudge_claims

    old, new = datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 6, 10, tzinfo=timezone.utc)
    import erase_watermark
    import user_erase_gate as erase_gate

    t0 = erase_watermark._now()
    take = lambda k, u, now: nudge_claims.claim(k, user_id=u, gen=erase_gate.generation(u), started_at=t0, now=now)  # noqa: E731
    assert await take("nudge:people_health:member_a:p1:2026-06-01", "member_a", old)
    assert not await take("nudge:people_health:member_a:p1:2026-06-01", "member_a", old)   # the second claimer loses
    assert await take("nudge:people_health:member_a:p1:2026-06-10", "member_a", new)       # prunes the key a week+ old
    assert list(claims.rows) == ["nudge:people_health:member_a:p1:2026-06-10"]
    await take("nudge:people_health:member_b:p1:2026-06-10", "member_b", new)
    assert await nudge_claims.forget_user("member_a") == 1 and list(claims.rows) == ["nudge:people_health:member_b:p1:2026-06-10"]


# ── a member erase wins every race with a nudge claim (the gate's generation and the durable mark) ──────────────────────────
@pytest.mark.parametrize("how", ["gate", "durable_mark_only"])
@pytest.mark.asyncio
async def test_a_member_deleted_between_the_read_and_the_claim_gets_no_claim_and_no_nudge(monkeypatch, claims, how):
    import erase_watermark
    import nudge_claims
    import user_erase_gate as erase_gate

    ph, nine, db = _nudge_world(monkeypatch)

    async def delete_member():                                   # runs after the trigger read the member's rows, before its claim
        if how == "gate":
            with erase_gate.erase_span(A):
                await nudge_claims.forget_user(A)
        else:                                                    # another process / a restart: only the durable mark is visible
            claims.marks[A] = erase_watermark._iso(erase_watermark._now() + timedelta(milliseconds=5))

    db.after_people_read = delete_member
    assert await ph.PeopleHealthTrigger().check(db, now_utc=nine) == []     # nothing to compose or deliver
    assert claims.rows == {}                                      # no claim row holding the member's id
    db.after_people_read = None
    if how == "gate":                                             # a tick that STARTS after the erase is a new world: it claims again
        assert len(await ph.PeopleHealthTrigger().check(db, now_utc=nine)) == 1


@pytest.mark.asyncio
async def test_a_claim_that_landed_first_is_erased_with_the_member(monkeypatch, claims):
    import nudge_claims
    import user_erase_gate as erase_gate

    ph, nine, db = _nudge_world(monkeypatch)
    (result,) = await ph.PeopleHealthTrigger().check(db, now_utc=nine)
    assert len(claims.rows) == 1
    with erase_gate.erase_span(A):
        assert await nudge_claims.forget_user(A) == 1
    assert claims.rows == {}
    del result


@pytest.mark.asyncio
async def test_a_member_deleted_between_the_claim_and_the_send_is_not_sent_to(monkeypatch, claims):
    import user_erase_gate as erase_gate

    ph, nine, db = _nudge_world(monkeypatch)
    engine = _engine_world(monkeypatch, db, push=1)
    sent = []

    async def spy(**k):
        sent.append(k)
        return 1

    monkeypatch.setattr(engine, "_send_push", spy)
    (result,) = await ph.PeopleHealthTrigger().check(db, now_utc=nine)
    with erase_gate.erase_span(A):                                # the delete lands after the tick built its results
        pass
    out = await engine.fire_notification(user_id=result.user_id, message=result.message, trigger_type=result.trigger_type,
                                         item_id=result.item_id, context=result.context, retract_if_undelivered=True)
    assert out is False and sent == [] and db.pending == [] and claims.rows == {}


@pytest.mark.asyncio
async def test_the_compose_step_cannot_reach_the_network_from_these_tests(monkeypatch):
    """The guard itself: an un-stubbed composer fails loudly instead of POSTing to the local brain."""
    from proactive import engine

    with pytest.raises(AssertionError, match="network"):
        await engine.compose_message("people_health", {"x": 1}, fallback="f")


# ── review fixes: a weekday hint keeps its clock time; a push that partly got through keeps the claim ─────────────────────────
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


def _fake_push_world(monkeypatch, *, cleanup_fails, second="expired"):
    """routers.push with two subscriptions: the first is delivered; the second is expired (410) or raises something unexpected; the
    dead-row cleanup then fails (or not)."""
    import sys
    import types

    import routers.push as push

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class Db:
        def execute(self, sql, params=()):
            if sql.strip().upper().startswith("DELETE"):
                async def go():
                    if cleanup_fails:
                        raise RuntimeError("db gone")
                return go()
            return Rows([{"endpoint": "https://push.invalid/a", "keys_p256dh": "k", "keys_auth": "a"},
                         {"endpoint": "https://push.invalid/b", "keys_p256dh": "k", "keys_auth": "a"}])

        async def commit(self):
            pass

    async def get_db():
        yield Db()

    class WPE(Exception):
        pass

    def webpush(**kw):
        if kw["subscription_info"]["endpoint"].endswith("/b"):
            raise WPE("410 gone") if second == "expired" else RuntimeError("unexpected")

    mod = types.ModuleType("pywebpush")
    mod.webpush, mod.WebPushException = webpush, WPE
    monkeypatch.setitem(sys.modules, "pywebpush", mod)
    monkeypatch.setattr(push, "get_db", get_db)
    monkeypatch.setattr(push, "_get_vapid_keys", lambda: {"private_key": "k"})
    return push


@pytest.mark.parametrize("second,cleanup_fails", [("expired", True), ("unexpected", False)])
@pytest.mark.asyncio
async def test_a_push_that_partly_got_through_still_reports_the_delivery(monkeypatch, second, cleanup_fails):
    push = _fake_push_world(monkeypatch, cleanup_fails=cleanup_fails, second=second)
    assert await push.send_push_to_user(user_id=A, message="m") == 1


@pytest.mark.asyncio
async def test_a_nudge_whose_push_partly_got_through_keeps_its_claim(monkeypatch, claims):
    """The reviewer's case end to end: one subscription delivered, the cleanup of the expired one raised. The real ``_send_push`` and
    ``send_push_to_user`` run; the pending row and the daily claim stay, and the next tick does not repeat the nudge."""
    ph, nine, db = _nudge_world(monkeypatch)
    engine = _engine_world(monkeypatch, db, push=0)
    _fake_push_world(monkeypatch, cleanup_fails=True)

    async def real_send_push(user_id, message, extra=None):
        from routers.push import send_push_to_user
        return await send_push_to_user(user_id=user_id, message=message, extra=extra or {}) or 0

    monkeypatch.setattr(engine, "_send_push", real_send_push)
    (result,) = await ph.PeopleHealthTrigger().check(db, now_utc=nine)
    out = await engine.fire_notification(user_id=result.user_id, message=result.message, trigger_type=result.trigger_type,
                                         item_id=result.item_id, context=result.context, retract_if_undelivered=True)
    assert out is True and len(db.pending) == 1 and len(claims.rows) == 1
    assert await ph.PeopleHealthTrigger().check(db, now_utc=nine + timedelta(minutes=5)) == []


# ── deliveries are counted AS THEY HAPPEN: a cancel mid-lane still knows what already got through ─────────────────────────
def _blocking_push_world(monkeypatch, *, block_on):
    """routers.push with two subscriptions (a, b); the push to ``block_on`` blocks in its executor thread until released, so the
    test can cancel the task while that one is in flight (the other, if earlier, has already been sent)."""
    import sys
    import threading
    import types

    import routers.push as push

    class Rows:
        async def fetchall(self):
            return [{"endpoint": "https://push.invalid/a", "keys_p256dh": "k", "keys_auth": "a"},
                    {"endpoint": "https://push.invalid/b", "keys_p256dh": "k", "keys_auth": "a"}]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class Db:
        def execute(self, sql, params=()):
            return Rows()

        async def commit(self):
            pass

    async def get_db():
        yield Db()

    world = types.SimpleNamespace(release=threading.Event(), sent=[], blocked=threading.Event())

    def webpush(**kw):
        ep = kw["subscription_info"]["endpoint"]
        if ep.endswith("/" + block_on):
            world.blocked.set()
            world.release.wait(10)
            return
        world.sent.append(ep)

    mod = types.ModuleType("pywebpush")
    mod.webpush, mod.WebPushException = webpush, type("WPE", (Exception,), {})
    monkeypatch.setitem(sys.modules, "pywebpush", mod)
    monkeypatch.setattr(push, "get_db", get_db)
    monkeypatch.setattr(push, "_get_vapid_keys", lambda: {"private_key": "k"})
    return world


async def _fire_then_cancel(monkeypatch, claims, *, block_on):
    ph, nine, db = _nudge_world(monkeypatch)
    engine = _engine_world(monkeypatch, db, push=0)
    world = _blocking_push_world(monkeypatch, block_on=block_on)

    async def real_send_push(user_id, message, extra=None):
        from routers.push import send_push_to_user
        return await send_push_to_user(user_id=user_id, message=message, extra=extra or {}) or 0

    monkeypatch.setattr(engine, "_send_push", real_send_push)
    (result,) = await ph.PeopleHealthTrigger().check(db, now_utc=nine)
    task = asyncio.ensure_future(engine.fire_notification(
        user_id=result.user_id, message=result.message, trigger_type=result.trigger_type, item_id=result.item_id,
        context=result.context, retract_if_undelivered=True))
    for _ in range(500):                                   # until the blocked subscription's push is in flight
        if world.blocked.is_set():
            break
        await asyncio.sleep(0.01)
    assert world.blocked.is_set()
    task.cancel()
    world.release.set()
    with pytest.raises(asyncio.CancelledError):            # the cancellation is re-raised, never swallowed
        await task
    return ph, nine, db, world


@pytest.mark.asyncio
async def test_a_cancel_during_the_second_subscription_keeps_the_claim_and_the_row(monkeypatch, claims):
    ph, nine, db, world = await _fire_then_cancel(monkeypatch, claims, block_on="b")
    assert world.sent == ["https://push.invalid/a"]                       # the first subscription HAD been delivered
    assert len(db.pending) == 1 and len(claims.rows) == 1                  # so the row and the daily claim stay
    assert await ph.PeopleHealthTrigger().check(db, now_utc=nine + timedelta(minutes=5)) == []   # a restart does not repeat it


@pytest.mark.asyncio
async def test_a_cancel_before_anything_was_delivered_releases_the_claim(monkeypatch, claims):
    ph, nine, db, world = await _fire_then_cancel(monkeypatch, claims, block_on="a")
    assert world.sent == []
    assert db.pending == [] and claims.rows == {}                          # nothing got through: as before, retried next tick
    assert len(await ph.PeopleHealthTrigger().check(db, now_utc=nine + timedelta(minutes=5))) == 1


@pytest.mark.asyncio
async def test_the_tally_is_per_send_and_a_lane_outside_one_counts_nothing():
    import delivery_tally

    delivery_tally.note()                                  # no tally open: a no-op
    t1, tok1 = delivery_tally.start()
    delivery_tally.note()
    t2, tok2 = delivery_tally.start()                      # a nested / concurrent send has its OWN count
    assert t2.n == 0
    delivery_tally.stop(tok2)
    delivery_tally.note()
    delivery_tally.stop(tok1)
    assert t1.n == 2
