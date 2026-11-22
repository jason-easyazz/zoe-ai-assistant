"""A reminder door never waits for a SECOND pool connection while it holds one, and the brain's snooze tool works.

1. POOL. On main every caller of ``create_reminder_record`` (the REST route, the MCP tool, the spoken path, the background
   makers) holds its connection while the door schedules the job, and ``proactive.triggers.reminders`` borrowed another for each
   of its statements; edit, delete, snooze and done did the same through cancel / reschedule. With as many such callers at once
   as the pool has connections, each holds one and waits for another. Now the door's job calls run on the caller's own
   connection (``on_connection``). Each test below gives the entry point a pool of ONE connection: it must finish inside the
   timeout with its job scheduled (or cancelled).

2. SNOOZE TOOL. On main ``reminder_snooze`` raised ``UnboundLocalError`` before its write (``_execute_tool`` bound ``datetime``
   locally in another arm). It now snoozes exactly as the screen does (ONE snooze: ``snoozed_until``, a UTC instant, no clock
   offset), and the job it schedules fires at that instant through the real fire path.

Control run for each test is named in its docstring (the code broken, the test seen to fail, restored). Invented ids and words.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import reminder_service as rs
from models import ReminderCreate, ReminderUpdate, SnoozeBody
from proactive.scheduler import CancelResult
from proactive.triggers import reminders as trig

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
OWNER, OTHER = "member-ova", "member-adu"
RID, WORD = "rem_bar_0001", "womble"
TIMEOUT_S = 3.0


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class World:
    """One household database behind a pool of ONE connection: a reminder row, the scheduled-job rows, and what was registered
    with / cancelled in the scheduler. ``borrow()`` is what every ``get_db`` / ``get_compat_db`` hands out."""

    def __init__(self, **row):
        self.gate = asyncio.Semaphore(1)
        self.row = {"id": RID, "user_id": OWNER, "title": WORD, "description": None, "visibility": "personal", "due_date": None,
                    "due_time": "08:00", "recurring_pattern": None, "priority": "medium", "is_active": 1, "acknowledged": 0,
                    "deleted": 0, "snoozed_until": None, "schedule_generation": 3, **row}
        self.made: dict = {}                        # reminders created through the door
        self.jobs: dict = {"sched-old": {"id": "sched-old", "item_id": RID, "fired": 0, "apscheduler_job_id": "reminder-sched-old",
                                         "schedule_generation": 3, "claimed_at": None}}
        self.registered, self.cancelled, self.sql = [], [], []

    @contextlib.asynccontextmanager
    async def borrow(self):
        await self.gate.acquire()
        try:
            yield _Handle(self)
        finally:
            self.gate.release()

    def reminder(self, rid):
        return self.row if rid == RID else self.made.get(rid)


class _Handle:
    def __init__(self, world):
        self.w = world

    def execute(self, sql, params=()):
        w, norm, p = self.w, " ".join(sql.split()), list(params)
        w.sql.append(norm)
        if norm.startswith("SELECT"):
            if "FROM users" in norm:
                return _Cur([{"id": p[0], "role": "user", "name": "Member"}])
            if "FROM proactive_scheduled" in norm:
                if "WHERE item_id" in norm:
                    return _Cur([dict(j) for j in w.jobs.values() if j["item_id"] == p[0] and not j["fired"]])
                return _Cur([dict(w.jobs[p[0]])] if p[0] in w.jobs else [])
            row = next((w.reminder(x) for x in p if w.reminder(x) is not None), None)
            if row is not None and "visibility = 'family'" in norm and not (row["visibility"] == "family" or row["user_id"] == p[0]):
                row = None
            if row is not None and "AND user_id=?" in norm and row["user_id"] != p[1]:
                row = None
            return _Cur([dict(row)] if row else [])
        if norm.startswith("INSERT INTO reminders"):
            w.made[p[0]] = {"id": p[0], "user_id": p[1], "title": p[2], "due_date": p[7], "due_time": p[8], "recurring_pattern": p[9],
                            "visibility": p[10], "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None,
                            "schedule_generation": 0}
        elif norm.startswith("INSERT INTO proactive_scheduled"):
            w.jobs[p[0]] = {"id": p[0], "user_id": p[1], "message": p[2], "send_at": p[3], "apscheduler_job_id": p[4], "fired": 0,
                            "item_id": p[5], "schedule_generation": p[6], "claimed_at": None}
        elif norm.startswith("DELETE FROM proactive_scheduled"):
            w.jobs.pop(p[0], None)
        elif norm.startswith("UPDATE proactive_scheduled SET claimed_at"):
            job = w.jobs.get(p[1])
            if job is None or job["fired"] or job["claimed_at"]:
                return _Cur([])
            job["claimed_at"] = p[0]
            return _Cur([dict(job)])
        elif norm.startswith("UPDATE proactive_scheduled"):
            for job in w.jobs.values():
                if job["id"] in p:
                    job["fired"] = 1
        elif norm.startswith("UPDATE reminders SET snoozed_until = ?"):
            w.row["snoozed_until"] = p[0]
            w.row["schedule_generation"] += 1
        elif norm.startswith("UPDATE reminders") and "schedule_generation" in norm:
            w.row["schedule_generation"] += 1
        return _Cur([])

    async def commit(self):
        return None


@pytest.fixture
def world(monkeypatch):
    w = World()

    async def noop(*_a, **_k):
        return None

    async def run_blocking(_call, *args, **kwargs):         # the scheduler itself: no thread, no jobstore
        if _call is trig.register_job:
            w.registered.append(kwargs["job_id"])
            return None
        w.cancelled.append(args[0])
        return CancelResult.REMOVED

    import db_compat
    import mcp_server

    async def actor(db, name, args, ctx):
        return {"user_id": (ctx or {}).get("user_id", OWNER), "role": "user"}

    monkeypatch.setattr(db_compat, "get_compat_db", w.borrow)          # what the job module borrows when it holds nothing
    monkeypatch.setattr(mcp_server, "_pg_get_db", w.borrow)
    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    monkeypatch.setattr(mcp_server, "_notify_ui", noop)
    monkeypatch.setattr(trig, "run_blocking", run_blocking)
    monkeypatch.setattr(rs, "require_feature_access", noop)
    monkeypatch.setattr(rs.broadcaster, "broadcast", noop)
    return w


def _user(who=OWNER):
    return {"user_id": who, "role": "user"}


async def _held(world, call):
    """Run an entry point the way the service does: the caller already HOLDS the pool's one connection."""
    async with world.borrow() as db:
        return await asyncio.wait_for(call(db), timeout=TIMEOUT_S)


def _new_jobs(world):
    return [j for j in world.jobs.values() if j["id"] != "sched-old"]


# ---- 1. a pool of one: every entry point finishes, with its job scheduled or cancelled --------------------------------------------
def _rest():
    from routers import reminders as rest

    return rest


def _tool(name, **args):
    import mcp_server

    return lambda db: mcp_server._execute_tool(db, name, args, {"user_id": OWNER})


CREATES = {
    "rest": lambda db: _rest().create_reminder(ReminderCreate(title=WORD, due_time="15:00"), user=_user(), db=db),
    "tool": _tool("reminder_create", title=WORD, due_time="15:00"),
    "door_as_the_background_makers_call_it": lambda db: rs.create_reminder_record(
        ReminderCreate(title=WORD, due_time="15:00"), user=_user(), db=db),
}


@pytest.mark.parametrize("name", sorted(CREATES))
@pytest.mark.asyncio
async def test_a_create_schedules_its_job_on_the_callers_own_connection(world, name):
    """Control run: with ``on_connection`` made a no-op (main's behaviour: the job module borrows) every case times out."""
    await _held(world, CREATES[name])
    (made,) = world.made
    (job,) = _new_jobs(world)
    assert job["item_id"] == made and world.registered == [job["apscheduler_job_id"]] and world.gate._value == 1


@pytest.mark.asyncio
async def test_a_spoken_create_schedules_its_job_on_its_own_connection(world, monkeypatch):
    """The spoken path borrows its one connection itself (``get_db_ctx``). Control run: as above, it times out."""
    import intent_router
    from intent_router import Intent, execute_intent

    async def user(_db, uid):
        return _user(uid)

    monkeypatch.setattr("database.get_db_ctx", world.borrow)
    monkeypatch.setattr(intent_router, "_load_direct_execution_user", user)
    reply = await asyncio.wait_for(execute_intent(Intent("reminder_create", {"title": WORD, "time": "15:00"}), OWNER), timeout=TIMEOUT_S)
    assert reply.startswith("Reminder set") and len(_new_jobs(world)) == 1 and world.gate._value == 1


@pytest.mark.asyncio
async def test_a_late_promise_reminder_schedules_its_own_job_on_the_callers_connection(world):
    """``commitments._make_reminder`` makes the row through the door (``schedule=False``) and schedules its own +45 s job.
    Control run: with its ``on_connection`` removed this times out."""
    import commitments

    now = datetime.now(timezone.utc)
    rid = await _held(world, lambda db: commitments._make_reminder(db, OWNER, WORD, now))
    (job,) = _new_jobs(world)
    assert rid and job["item_id"] == rid and world.registered == [job["apscheduler_job_id"]]


CHANGES = {
    # name -> (the call, whether the old job is cancelled, how many new jobs)
    "rest_edit_moves_the_time": (lambda db: _rest().update_reminder(RID, ReminderUpdate(due_time="16:00"), user=_user(), db=db), True, 1),
    "rest_delete": (lambda db: _rest().delete_reminder(RID, user=_user(), db=db), True, 0),
    "rest_snooze": (lambda db: _rest().snooze_reminder(RID, SnoozeBody(snooze_minutes=10), user=_user(), db=db), True, 1),
    "rest_done": (lambda db: _rest().acknowledge_reminder(RID, user=_user(), db=db), True, 0),
    "tool_delete": (_tool("reminder_delete", reminder_id=RID), True, 0),
    "tool_snooze": (_tool("reminder_snooze", reminder_id=RID, minutes=10), True, 1),
}


@pytest.mark.parametrize("name", sorted(CHANGES))
@pytest.mark.asyncio
async def test_a_change_cancels_and_reschedules_on_the_callers_own_connection(world, name):
    """Control run: with ``on_connection`` made a no-op every case times out (cancelling borrows too)."""
    call, cancels, new = CHANGES[name]
    await _held(world, call)
    assert ("sched-old" not in world.jobs) is cancels and world.cancelled == (["reminder-sched-old"] if cancels else [])
    assert len(_new_jobs(world)) == new and len(world.registered) == new and world.gate._value == 1


@pytest.mark.asyncio
async def test_done_on_a_recurring_reminder_cancels_and_reschedules_on_the_callers_own_connection(world):
    world.row.update(recurring_pattern="FREQ=DAILY", due_date=datetime.now(timezone.utc).date().isoformat())
    await _held(world, lambda db: _rest().acknowledge_reminder(RID, user=_user(), db=db))
    assert "sched-old" not in world.jobs and world.gate._value == 1


@pytest.mark.asyncio
async def test_outside_a_door_the_job_module_borrows_as_it_always_did(world):
    """The scheduler firing a job, the scan and start-up hold no connection: they borrow one per block, and a door's connection
    never leaks to them. Control run: with the reset dropped from ``on_connection`` the second call runs on the stale handle."""
    async with world.borrow() as db:
        with trig.on_connection(db):
            assert await trig._reminder_generation(RID) == 3
    assert trig._CALLERS_DB.get() is None and world.gate._value == 1
    assert await asyncio.wait_for(trig._reminder_generation(RID), timeout=TIMEOUT_S) == 3      # borrowed, and given back
    assert world.gate._value == 1


# ---- 2. the brain's snooze tool: it works, and it is the screen's snooze --------------------------------------------------------
@pytest.mark.asyncio
async def test_the_snooze_tool_no_longer_crashes_and_writes_the_screens_row(world):
    """Control run: with the tool's arm reading ``datetime.now()`` again while another arm says ``import datetime`` (main's
    code), this raises ``UnboundLocalError``."""
    before = datetime.now(timezone.utc)
    out = await _held(world, _tool("reminder_snooze", reminder_id=RID, minutes=30))
    assert out["id"] == RID and out["status"] == "snoozed" and set(out) == {"id", "snoozed_until", "status"}
    snoozed = datetime.fromisoformat(out["snoozed_until"].replace("Z", "+00:00"))
    assert timedelta(minutes=29, seconds=50) <= snoozed - before <= timedelta(minutes=30, seconds=10)       # a UTC instant, no offset
    (update,) = [s for s in world.sql if s.startswith("UPDATE reminders")]
    assert update == ("UPDATE reminders SET snoozed_until = ?, updated_at = NOW(), "
                      "schedule_generation = COALESCE(schedule_generation, 0) + 1 WHERE id = ?")    # the screen's statement: no due date / time
    (job,) = _new_jobs(world)
    assert job["send_at"] == snoozed.strftime("%Y-%m-%dT%H:%M:%SZ") and job["schedule_generation"] == 4


@pytest.mark.parametrize("who,args,expected", [
    (OTHER, {"minutes": 5}, {"error": f"Reminder {RID} not found"}),                                # another member's private reminder
    (OWNER, {"minutes": 0}, {"error": "minutes must be between 1 and 10080", "status": "refused"}),
    (OWNER, {"minutes": "soon"}, {"error": "minutes must be a whole number", "status": "refused"}),
])
@pytest.mark.asyncio
async def test_the_snooze_tool_refuses_plainly_and_writes_nothing(world, who, args, expected):
    """Owner only, like the screen (main's tool had no owner check at all, but never reached its write). Control run: with the
    bounds check removed, ``minutes=0`` snoozes to now."""
    import mcp_server

    out = await _held(world, lambda db: mcp_server._execute_tool(db, "reminder_snooze", {"reminder_id": RID, **args}, {"user_id": who}))
    assert out == expected and not [s for s in world.sql if s.startswith("UPDATE reminders")] and _new_jobs(world) == []


@pytest.mark.parametrize("done,delivered", [(0, True), (1, False)])
@pytest.mark.asyncio
async def test_a_snoozed_reminder_fires_at_its_snooze_through_the_real_fire_path(world, monkeypatch, done, delivered):
    """The job the snooze schedules carries the reminder's NEW generation, so the real ``_fire_reminder`` claims it and delivers.
    A reminder already marked done is not re-armed by a snooze (the screen's behaviour, unchanged here): its job is voided at
    fire time. Control run: with the job stamped with generation 0 instead of the reminder's current one, the first case is
    voided as superseded and this fails."""
    sent = []

    async def fire(**kw):
        sent.append((kw["item_id"], kw["trigger_type"]))

    monkeypatch.setattr("proactive.engine.fire_notification", fire)
    world.row["acknowledged"] = done
    await _held(world, _tool("reminder_snooze", reminder_id=RID, minutes=10))
    (job,) = _new_jobs(world)
    await asyncio.wait_for(trig._fire_reminder(job["id"], OWNER, WORD), timeout=TIMEOUT_S)        # the scheduler: holds no connection
    assert sent == ([(RID, "reminder")] if delivered else [])


# ---- the crash class: a name bound in one arm of _execute_tool and read in another ------------------------------------------------
def _own(node):
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(n))


def _binds(node) -> dict:
    out: dict = {}
    for n in _own(node):
        names: list = []
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names = [n.id]
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [(a.asname or a.name).split(".")[0] for a in n.names]
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names = [n.name]
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [n.name]
        for name in names:
            out[name] = min(out.get(name, 10 ** 9), n.lineno)
    return out


def unbound_across_arms(src: str, fname: str) -> list:
    """``(name, line)`` for every local of ``fname`` READ in one arm of its top-level ``if / elif`` chain but BOUND only in
    another arm (and not before the chain): Python makes the name local to the whole function, so that read raises
    ``UnboundLocalError`` whenever its own arm runs."""
    fn = [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fname][0]
    local, params = _binds(fn), {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    comp = {t.id for n in _own(fn) if isinstance(n, ast.comprehension) for t in ast.walk(n.target) if isinstance(t, ast.Name)}
    arms, top = [], {}
    for st in fn.body:
        if not isinstance(st, ast.If):
            for name, line in _binds(ast.Module(body=[st], type_ignores=[])).items():
                top[name] = min(top.get(name, 10 ** 9), line)
            continue
        node = st
        while True:
            arms.append(ast.Module(body=node.body, type_ignores=[]))
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                node = node.orelse[0]
            else:
                if node.orelse:
                    arms.append(ast.Module(body=node.orelse, type_ignores=[]))
                break
    out = set()
    for arm in arms:
        bound = _binds(arm)
        for n in _own(arm):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in local and n.id not in params | comp \
                    and n.id not in bound and not (n.id in top and top[n.id] < n.lineno):
                out.add((n.id, n.lineno))
    return sorted(out)


def test_no_tool_arm_reads_a_name_that_only_another_arm_binds():
    """The class of the snooze crash, over the whole of ``_execute_tool``. Control (planted below): main's shape is reported.
    Control run on the real file: with main's two halves put back (an ``import datetime`` in one arm, a ``datetime.now()`` in the
    snooze arm) that read is reported."""
    assert unbound_across_arms((SVC / "mcp_server.py").read_text("utf-8"), "_execute_tool") == []
    planted = ("from datetime import datetime\n"
               "async def _execute_tool(db, name):\n"
               "    actor = 1\n"
               "    if name == 'clock':\n        import datetime\n        return datetime.datetime.now()\n"
               "    elif name == 'snooze':\n        return datetime.now()\n"
               "    elif name == 'fine':\n        import json\n        return json.dumps(actor)\n")
    assert unbound_across_arms(planted, "_execute_tool") == [("datetime", 8)]
