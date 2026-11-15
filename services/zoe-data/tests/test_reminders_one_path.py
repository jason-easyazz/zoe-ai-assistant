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
@pytest.mark.asyncio
async def test_accepted_suggestion_goes_through_the_one_writer(monkeypatch):
    made = []

    async def create(payload, *, user, db, **_k):
        made.append((payload.title, payload.due_date, payload.due_time, user["user_id"]))
        return {"id": "rem-9", "due_date": "2099-06-16", "due_time": "15:00"}

    monkeypatch.setattr("reminder_service.create_reminder_record", create)
    monkeypatch.setattr(pending_suggestions, "get_db_ctx", _ctx(_Store()))
    act, no_raw = pending_suggestions._execute_action, object()  # the raw conn has no execute: a raw INSERT would raise
    out = await act(no_raw, "reminder_create", {"title": "book the vet", "date": "2099-06-16", "time": "3pm"}, A)
    assert out["reminder_id"] == "rem-9" and made == [("book the vet", "2099-06-16", "3pm", A)]
    with pytest.raises(ValueError, match="reminder_needs_time"):  # nothing to fire at: refused, the offer stays open
        await act(no_raw, "reminder_create", {"title": "book the vet"}, A)
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


@pytest.mark.asyncio
async def test_the_keyword_path_says_the_refusal(monkeypatch):
    async def past(*_a, **_k):
        raise rs.ReminderPastError("due_date 'x' is already past", rs.SPOKEN["past_day"])

    monkeypatch.setattr("reminder_service.create_reminder_record", past)
    monkeypatch.setattr("database.get_db_ctx", _ctx(_Store()))
    reply = await execute_intent(Intent("reminder_create", {"title": "call the plumber", "date": "2001-01-01"}), A)
    assert reply == rs.SPOKEN["past_day"]


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
    def execute(self, *_a, **_k):
        row = {"id": "p1", "name": "Person One", "user_id": A, "last_contacted_at": None, "context": ""}

        class _C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def fetchall(self):
                return [row]
        return _C()


@pytest.mark.asyncio
async def test_people_health_nudge_fires_once_per_day(monkeypatch):
    from proactive.triggers import people_health as ph

    monkeypatch.setattr(ph, "_ZOE_TZ", timezone(timedelta(hours=8)))
    trig, nine = ph.PeopleHealthTrigger(), datetime(2026, 6, 10, 1, 0, tzinfo=timezone.utc)  # 09:00 local
    assert len(await trig.check(_PeopleDb(), now_utc=nine)) == 1
    assert await trig.check(_PeopleDb(), now_utc=nine + timedelta(minutes=5)) == []        # the next slow-loop tick
    assert len(await trig.check(_PeopleDb(), now_utc=nine + timedelta(days=1))) == 1        # tomorrow fires again
