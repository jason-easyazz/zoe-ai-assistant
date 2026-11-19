"""The reminders ONE DOOR: every entry point (the screen's REST routes, the brain's MCP tools, the spoken path, an accepted
suggestion) changes a reminder through ``reminder_service`` under one ownership rule, and the three faults fixed with it:

* an accepted reminder suggestion was a raw INSERT with no day or time (it could never fire): it now goes through the door with
  its day and time, in the suggestion's own transaction, and an offer with no readable day or time stays open;
* the spoken "cancel that" (``reminder_cancel``) had no handler: it cancels the reminder the speaker's create just made, by its
  id (never one found by its title), and nothing at all if that create was refused or failed;
* a one-off for a clock time already gone today was stored and never fired: refused on create and on an edit that moves it there
  (a spoken turn is told so in words).

Nothing else changes at any entry point: ``test_reminders_same_as_main.py`` compares every moved entry point with a record
captured from ``main``.

Negative controls run for this file (each broke the code, saw the named test fail, restored): ``refuse_past_due`` made a no-op ->
``test_a_time_already_gone_today_is_refused`` fails; the "moved" condition dropped from the edit's past-time check ->
``test_an_edit_that_does_not_move_the_time_is_as_it_always_was`` fails; the ``reminder_cancel`` branch removed from
``execute_intent`` -> ``test_cancel_that_cancels_exactly_the_reminder_just_set_and_a_second_one_does_nothing`` fails; the suggestion's reminder created with
no ``after_commit`` list (job and broadcast inside the transaction) ->
``test_accepted_suggestion_is_one_transaction_on_one_connection`` fails.
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
    async def schedule_due(_db, row, **_k):
        jobs.append({"due": dict(row).get("id")})

    return {"jobs": jobs, "schedule_due": schedule_due}


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
async def test_snooze_is_owner_only_and_touches_only_the_snooze(quiet):
    db = _Store(visibility="family")                       # B can SEE it, but it is A's
    with pytest.raises(HTTPException) as exc:
        await rs.snooze_reminder_record(db, _actor(B), "rem-1", 10)
    assert exc.value.status_code == 403 and db.updates() == []
    db = _Store(recurring_pattern="FREQ=DAILY", acknowledged=1)
    await rs.snooze_reminder_record(db, _actor(A), "rem-1", 10)
    (sql, _), = db.updates()
    assert all(word not in sql for word in ("due_date", "due_time", "acknowledged", "is_active")) and "snoozed_until = ?" in sql
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
async def test_an_edit_that_does_not_move_the_time_is_as_it_always_was(quiet, monkeypatch):
    """A reminder for earlier today can still be renamed, switched off, or re-saved with its own day and time (an edit form sends
    the whole row; "9am" for a stored 09:00 is the same time): the new rule refuses a MOVE, nothing else. A day already gone
    that is re-sent is the 422 it has always been."""
    monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
    db = _Store(due_date="2026-06-10", due_time="09:00")             # earlier today
    await _edit(db, title="take pills with food")                    # rename
    await _edit(db, title="take pills with food", due_date="2026-06-10", due_time="9am")
    await _edit(db, is_active=False)                                 # switch it off
    assert len(db.updates()) == 3
    db = _Store(due_date="2026-06-09", due_time="09:00")             # yesterday
    await _edit(db, title="take pills with food")
    with pytest.raises(HTTPException) as exc:
        await _edit(db, title="take pills with food", due_date="2026-06-09")
    assert exc.value.status_code == 422 and len(db.updates()) == 1


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


# ── the brain's MCP tools: their rows are written by the store, and are EXACTLY what they were (test_reminders_same_as_main.py) ──
async def _tool(db, name, uid=A, **args):
    import mcp_server

    return await mcp_server._execute_tool(db, name, {"reminder_id": "rem-1", **args}, {"user_id": uid, "source": "transport"})


@pytest.mark.parametrize("visibility", ["personal", "family"])
@pytest.mark.parametrize("tool,args", [("reminder_update", {"title": "x"}), ("reminder_delete", {})])
@pytest.mark.asyncio
async def test_mcp_another_member_cannot_change_a_reminder(monkeypatch, visibility, tool, args):
    import mcp_server

    async def quiet_ui(*_a, **_k):
        return None

    monkeypatch.setattr(mcp_server, "_notify_ui", quiet_ui)

    class _Own(_Store):                                   # the tools look the row up by id AND owner
        async def execute(self, sql, params=()):
            if " ".join(sql.split()).startswith("SELECT id FROM reminders WHERE id=? AND user_id=?"):
                return _Cursor({"id": "rem-1"} if list(params)[1] == self.row["user_id"] else None)
            return await super().execute(sql, params)

    db = _Own(visibility=visibility)                      # A's reminder; B asks
    assert await _tool(db, tool, uid=B, **args) == {"error": "Reminder rem-1 not found"}
    assert db.updates() == [] and db.row["deleted"] == 0


class _OnePool:
    """A pool of ONE connection: a second ``acquire`` waits until the first is released."""

    def __init__(self, state):
        self.state, self.gate, self.conns = state, asyncio.Semaphore(1), []

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                await pool.gate.acquire()
                pool.conns.append(_PgConn(pool.state))
                return pool.conns[-1]

            async def __aexit__(self, *_):
                pool.gate.release()
        return _Ctx()


@pytest.mark.asyncio
async def test_an_accepted_suggestion_never_holds_its_connection_while_the_job_borrows_another(monkeypatch):
    """Scheduling a due reminder borrows a pool connection of its own (``schedule_reminder``). The accept runs its after-commit
    steps only once its own connection is back in the pool, and the job check reads through a handle that borrows a connection
    per statement, so even a pool of ONE completes (with ten accepts at once and a pool of ten, each would otherwise hold one and
    wait for another). The real ``schedule_due_reminder`` runs. Control run: with the steps run inside the ``pool.acquire()``
    block again, or the job check handed the suggestion's own connection, this times out."""
    state = _PgState(slots='{"title": "book the vet", "time": "15:00"}')       # a time and no day: due within a day, so it IS scheduled
    pool, scheduled = _OnePool(state), []

    async def allow(*_a, **_k):
        return None

    async def schedule_reminder(**kw):                         # the scheduler's own borrow, as ``proactive.triggers.reminders`` makes it
        async with pool.acquire():
            scheduled.append(kw["item_id"])

    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: pool)
    monkeypatch.setattr("reminder_service.require_feature_access", allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", allow)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", schedule_reminder)
    monkeypatch.setattr("anticipation.mode", lambda: "off")
    out = await asyncio.wait_for(pending_suggestions.execute_suggestion("sug-1", A), timeout=3)
    assert out["ok"] is True and scheduled == list(state.reminders) and len(scheduled) == 1
    assert pool.gate._value == 1                               # every borrowed connection was given back


# ── fault 2: the spoken "cancel that" is bound to the reminder the create just made, by id ─────────────────────────────────
class _Rows:
    """Several reminder rows. An INSERT adds one; a read is by id (with the visibility filter) only: a read by TITLE fails the test."""

    def __init__(self, *rows):
        self.rows = {r["id"]: {"user_id": A, "title": "take pills", "visibility": "personal", "due_date": "2099-06-15",
                               "due_time": "08:00", "recurring_pattern": None, "is_active": 1, "acknowledged": 0, "deleted": 0,
                               "snoozed_until": None, **r} for r in rows}
        self.fail_insert = False

    async def execute(self, sql, params=()):
        norm, p = " ".join(sql.split()), list(params)
        assert "title" not in norm.split("WHERE", 1)[-1].lower() or not norm.startswith("SELECT"), "a reminder was looked up by its title"
        if norm.startswith("SELECT"):
            if "FROM users" in norm:
                return _Cursor({"id": p[0], "role": "user", "name": "Member"})
            row = self.rows.get(p[-1])
            seen = row is not None and not ("deleted = 0" in norm and row["deleted"]) and (
                row["visibility"] == "family" or row["user_id"] == p[0] if "visibility = 'family'" in norm
                else row["user_id"] == p[0] if "user_id = ?" in norm else True)
            return _Cursor(dict(row) if seen else None)
        if norm.startswith("INSERT INTO reminders"):
            if self.fail_insert:
                raise RuntimeError("connection lost")
            self.rows[p[0]] = {"id": p[0], "user_id": p[1], "title": p[2], "due_date": p[7], "due_time": p[8], "visibility": p[10],
                               "recurring_pattern": None, "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None}
        elif norm.startswith("UPDATE reminders SET deleted = 1"):
            self.rows[p[-1]]["deleted"] = 1
        return _Cursor()

    async def commit(self):
        pass

    def live(self):
        return sorted(rid for rid, r in self.rows.items() if not r["deleted"])


@pytest.fixture
def said(monkeypatch, quiet):
    """A spoken conversation: ``say(text-free intent)`` runs an intent for A; ``follow_up()`` resolves "cancel that" through the
    real conversation context, activated (as on the voice and chat paths) BEFORE the create runs."""
    import intent_router
    from conversation_context import ConversationContext

    store = _Rows({"id": "older", "created_at": "2026-01-01"}, {"id": "newer-done", "created_at": "2099-01-01", "acknowledged": 1})
    ctx = ConversationContext()
    monkeypatch.setattr("database.get_db_ctx", _ctx(store))
    monkeypatch.setattr("proactive.triggers.reminder_scan.schedule_due_reminder", quiet["schedule_due"])
    intent_router._JUST_SET_REMINDER.clear()

    async def create(who=A, **slots):
        intent = Intent("reminder_create", {"title": "take pills", "date": "2099-06-16", "time": "15:00", **slots})
        ctx.activate(intent.name, intent.slots, "remind me to take pills")          # the context is set at detection
        return await execute_intent(intent, who)

    async def cancel_that(who=A, text="cancel that"):
        name, slots = ctx.resolve_coreference(text)
        if text == "cancel that":
            assert name == "reminder_cancel"
        if name is None:
            return None                                  # not a follow-up at all: normal interpretation
        try:
            return await execute_intent(Intent(name, slots), who)
        except Exception:  # noqa: BLE001 - whatever the old fall-through does with an unbound turn is not these tests' subject
            return None

    return store, create, cancel_that


@pytest.mark.asyncio
async def test_cancel_that_cancels_exactly_the_reminder_just_set_and_a_second_one_does_nothing(said):
    """An older live reminder and a newer one already marked done share the title: neither is touched (the fake fails the test
    on any read of a reminder by its title). Control run: with the cancel handed the older same-title row's id instead of the
    noted one, the wrong reminder goes and this fails. (A second "cancel that" finds the row gone, or no note, either way.)"""
    store, create, cancel_that = said
    assert (await create()).startswith("Reminder set")
    (made,) = [rid for rid in store.rows if rid not in ("older", "newer-done")]
    assert await cancel_that() == "Reminder cancelled: take pills."
    assert store.rows[made]["deleted"] == 1 and store.live() == ["newer-done", "older"]
    assert await cancel_that() == rs.SPOKEN["cancel_nothing"]                        # a second "cancel that": nothing left to cancel
    assert store.live() == ["newer-done", "older"]


@pytest.mark.parametrize("how", ["past_time", "write_failed", "role_refused"])
@pytest.mark.asyncio
async def test_cancel_that_after_a_create_that_did_not_happen_cancels_nothing_and_says_so(said, monkeypatch, how):
    """A refused or failed create leaves nothing to cancel, however many older reminders share its title, and even when an
    EARLIER create of the same title succeeded a moment ago. Controls run: with the earlier note NOT dropped when a new create is
    attempted (so "that" stays bound across a create that did not happen) the earlier reminder is cancelled here; with a lookup
    by title put back for the no-id case, the fake's title guard trips and the reply is not "nothing to cancel"."""
    store, create, cancel_that = said
    assert (await create()).startswith("Reminder set")                              # an earlier, good create of the same title
    before = store.live()
    if how == "past_time":
        monkeypatch.setattr("proactive.triggers.reminder_scan._ZOE_TZ", timezone(timedelta(hours=8)))
        monkeypatch.setattr("reminder_service.normalize_due_date", lambda raw, **k: "2026-06-10")

        def gone(*_a, **_k):
            raise rs.ReminderPastError("already past", rs.SPOKEN["past_time"])

        monkeypatch.setattr("reminder_service.refuse_past_due", gone)
        assert await create() == rs.SPOKEN["past_time"]
    elif how == "write_failed":
        store.fail_insert = True
        assert not (await create() or "").startswith("Reminder set")
        store.fail_insert = False
    else:
        async def refuse(*_a, **_k):
            raise HTTPException(status_code=403, detail="not allowed")

        monkeypatch.setattr("reminder_service.require_feature_access", refuse)
        with pytest.raises(HTTPException):
            await create()
    reply = await cancel_that()
    assert reply == rs.SPOKEN["cancel_nothing"] and "cancelled" not in reply.lower()
    assert store.live() == before


#: utterance -> is it an affirmative "cancel the thing you just made"? Only those may delete the reminder just set.
SAID_AFTER_A_REMINDER = {
    # the whole utterance is the command, a little politeness allowed
    "cancel that": True, "Cancel that.": True, "cancel it": True, "delete that reminder": True, "never mind that one": True,
    "oh, please cancel that": True, "actually cancel it please": True, "remove the reminder": True, "scrap that": True,
    # negations
    "don't cancel that": False, "do not delete it": False, "no need to cancel that": False, "never mind, don't cancel it": False,
    "never cancel that": False, "please don't remove that reminder": False,
    # questions
    "how do I delete an old reminder?": False, "can I cancel that later?": False, "did you cancel that?": False, "cancel that?": False,
    "can you tell me how to delete it": False,
    # other sentences that merely contain a cancel word
    "cancel my subscription reminder for friday": False, "remind me to cancel the gym": False, "clear the table after dinner": False,
    "cancel that one for me tomorrow": False, "delete it and the one before it": False,
}


@pytest.mark.parametrize("text", sorted(SAID_AFTER_A_REMINDER))
@pytest.mark.asyncio
async def test_only_an_affirmative_cancel_of_that_deletes_the_reminder_just_set(said, text):
    """Within two minutes of a spoken create, ONLY a whole-utterance, affirmative "cancel that / it / the reminder" is bound to
    the reminder just made. A negation, a question, or a sentence that merely contains a cancel word leaves the row untouched
    and is never answered "Reminder cancelled" (it goes to normal interpretation, as on main). Control run: with the binding
    back on any cancel word anywhere (``_CANCEL_RE.search``), "don't cancel that" and every other negative here delete the
    reminder and this fails for them."""
    from conversation_context import ConversationContext

    store, create, cancel_that = said
    assert (await create()).startswith("Reminder set")
    (made,) = [rid for rid in store.rows if rid not in ("older", "newer-done")]
    ctx = ConversationContext()
    ctx.activate("reminder_create", {"title": "take pills"}, "remind me to take pills")
    bound = (ctx.resolve_coreference(text)[1] or {}).get("after") == "reminder_create"
    assert bound is SAID_AFTER_A_REMINDER[text]
    reply = await cancel_that(text=text)
    if SAID_AFTER_A_REMINDER[text]:
        assert reply == "Reminder cancelled: take pills." and store.rows[made]["deleted"] == 1
    else:
        assert store.rows[made]["deleted"] == 0 and "cancelled" not in str(reply or "").lower()
    assert store.rows["older"]["deleted"] == 0 and store.rows["newer-done"]["deleted"] == 0


@pytest.mark.asyncio
async def test_the_note_of_the_reminder_just_set_holds_ids_and_a_number_never_a_word(said):
    """The reply's title is read from the row when the cancel runs, so the note needs no words. Control run: with the title put
    back into the note's tuple this fails (a string that is not the id is in the cache)."""
    import intent_router

    store, create, cancel_that = said
    assert (await create(title="womble the quince")).startswith("Reminder set")
    (made,) = [rid for rid in store.rows if rid not in ("older", "newer-done")]
    assert list(intent_router._JUST_SET_REMINDER) == [A]
    entry = intent_router._JUST_SET_REMINDER[A]
    assert entry[0] == made and len(entry) == 2 and isinstance(entry[1], float)
    assert "womble" not in repr(intent_router._JUST_SET_REMINDER) and "quince" not in repr(intent_router._JUST_SET_REMINDER)
    assert await cancel_that() == "Reminder cancelled: womble the quince."        # the words come from the row, at that moment
    assert store.rows[made]["deleted"] == 1 and intent_router._JUST_SET_REMINDER == {}


@pytest.mark.asyncio
async def test_an_expired_note_is_removed_on_any_access_not_only_its_own_lookup(said, monkeypatch):
    """Control run: with the prune dropped from ``_note_reminder_just_set`` the other member's stale note outlives a new note."""
    import intent_router

    store, create, cancel_that = said
    assert (await create()).startswith("Reminder set")
    clock = [intent_router.time.monotonic()]
    monkeypatch.setattr(intent_router.time, "monotonic", lambda: clock[0])
    clock[0] += intent_router._JUST_SET_WINDOW_S + 1
    intent_router._note_reminder_just_set(B, "rem-b")                                # ANOTHER member's access: A's stale note goes
    assert list(intent_router._JUST_SET_REMINDER) == [B]
    clock[0] += intent_router._JUST_SET_WINDOW_S + 1
    before = store.live()
    assert await cancel_that() == rs.SPOKEN["cancel_nothing"] and store.live() == before      # a lookup prunes too
    assert intent_router._JUST_SET_REMINDER == {}


@pytest.mark.asyncio
async def test_a_member_forget_drops_the_note_and_a_row_that_has_gone_is_nothing_to_cancel(said):
    """``forget_redact.clear_turn_caches`` is what ``MemoryService.delete_user`` and the entity forget run for the in-process
    per-member caches. Controls run: with ``intent_router`` taken out of its list the note survives the forget; with the row read
    dropped from the cancel, a reminder deleted since it was set is answered "couldn't find" instead of "nothing to cancel"."""
    import forget_redact
    import intent_router

    store, create, cancel_that = said
    assert (await create()).startswith("Reminder set")
    intent_router._note_reminder_just_set(B, "rem-b")
    assert forget_redact.clear_turn_caches(A) >= 1
    assert list(intent_router._JUST_SET_REMINDER) == [B]                             # only the forgotten member's note went
    before = store.live()
    assert await cancel_that() == rs.SPOKEN["cancel_nothing"] and store.live() == before
    assert (await create()).startswith("Reminder set")
    (made,) = [rid for rid in store.live() if rid not in before]
    store.rows[made]["deleted"] = 1                                                  # deleted elsewhere (the screen, a forget) since
    assert await cancel_that() == rs.SPOKEN["cancel_nothing"] and A not in intent_router._JUST_SET_REMINDER


@pytest.mark.asyncio
async def test_cancel_that_is_per_member_and_never_reaches_a_guest_or_a_timer(said):
    """Another member's "cancel that" has no reminder of THEIR making to cancel; a guest is asked who they are; and after a TIMER
    the turn goes where it always went (this executor does not answer it). Control run: with the ``after=reminder_create``
    condition dropped from ``execute_intent`` the timer's follow-up is answered here and this fails."""
    import intent_router
    from conversation_context import ConversationContext

    store, create, cancel_that = said
    assert (await create()).startswith("Reminder set")
    before = store.live()
    assert await cancel_that(B) == rs.SPOKEN["cancel_nothing"] and store.live() == before
    assert (await cancel_that("guest")).startswith("I need to know who you are") and store.live() == before
    ctx = ConversationContext()
    ctx.activate("timer_set", {"title": "take pills"}, "set a timer")
    name, slots = ctx.resolve_coreference("cancel that")
    assert (name, slots) == ("reminder_cancel", {"title": "take pills"})

    async def never(*_a, **_k):
        raise AssertionError("the reminder cancel answered a timer's follow-up")

    saved, intent_router._execute_reminder_cancel_direct = intent_router._execute_reminder_cancel_direct, never
    try:
        await execute_intent(Intent(name, slots), A)
    except AssertionError:
        raise
    except Exception:  # noqa: BLE001 - whatever the old fall-through does with it is not this test's subject
        pass
    finally:
        intent_router._execute_reminder_cancel_direct = saved
    assert store.live() == before


# ── fault 1, the WHEN of an accepted suggestion: its slots and its hint are merged ────────────────────────────────────────────
@pytest.mark.parametrize("slots,expected", [
    ({"date": "friday", "when_hint": "3pm"}, ("friday", "15:00")),                 # a day slot, the time only in the hint
    ({"time": "3pm", "when_hint": "friday"}, ("friday", "3pm")),                   # a time slot, the day only in the hint
    ({"due_date": "2099-06-16", "when_hint": "at 7:30am"}, ("2099-06-16", "07:30")),
    ({"date": "friday", "time": "9am", "when_hint": "saturday at 3pm"}, ("friday", "9am")),   # both slots: they win, whatever the hint
    ({"date": "friday", "when_hint": "saturday at 3pm"}, ("friday", "15:00")),     # a slot wins for ITS field; the hint fills the other
    ({"when_hint": "friday at 3pm"}, ("friday", "15:00")),                         # the hint alone
    ({"date": "friday"}, ("friday", None)),                                        # a day and nothing else: a day
    ({"date": "friday", "when_hint": "in the morning"}, (None, None)),             # a hint nobody can read: the offer stays open
    ({}, (None, None)),
    # a hint counts only when ALL of it was read: "next week at 3pm" is not "3 pm today"
    ({"when_hint": "next week at 3pm"}, (None, None)),
    ({"when_hint": "friday afternoon"}, (None, None)),
    ({"date": "friday", "when_hint": "next week at 3pm"}, (None, None)),           # ... whatever the slots say
    ({"when_hint": "sometime soonish"}, (None, None)),
])
def test_an_offers_slots_and_its_hint_are_merged(slots, expected):
    """The producer keeps the model's slots and adds ``when_hint`` beside them. Control run: with the old either/or rule (the hint
    read only when there is NO slot) the first, second, third and fifth cases lose the hint's half; with a PARTLY read hint
    accepted (the grammar's own result taken whenever it read anything), "next week at 3pm" comes back as today at 15:00."""
    assert pending_suggestions._suggested_when("book the vet", slots) == expected


@pytest.mark.asyncio
async def test_an_accepted_offer_with_a_day_slot_and_a_time_hint_fires_at_that_time(monkeypatch):
    from datetime import date

    state = _PgState(slots='{"title": "book the vet", "date": "friday", "when_hint": "3pm"}')
    _accept_world(monkeypatch, state)
    assert (await pending_suggestions.execute_suggestion("sug-1", A))["ok"] is True
    (row,) = state.reminders.values()
    assert row["due_time"] == "15:00" and date.fromisoformat(row["due_date"]).weekday() == 4       # a Friday, at 15:00
    state = _PgState(slots='{"title": "book the vet", "date": "friday", "when_hint": "in the morning"}')
    _accept_world(monkeypatch, state)
    assert await pending_suggestions.execute_suggestion("sug-1", A) == {"ok": False, "error": "reminder_needs_time"}
    assert state.reminders == {} and state.resolved == 0


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
