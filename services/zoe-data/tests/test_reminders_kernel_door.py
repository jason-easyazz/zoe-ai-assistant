"""The reminders door asks the Zoe OS kernel (``ZOE_OS_REMINDERS=kernel``; docs/knowledge/zoe-os-reminders-door.md).

* any other flag value: NO kernel call, no socket, no thread, no extra statement: the statements each door issues equal a
  fixture captured from the code BEFORE the door existed;
* the kernel allows: the change is made and its outcome reported after the write; only ids, stamps and closed codes cross;
* the kernel refuses: ``ReminderRefused``, nothing is written and no scheduled job is touched; each caller says so its own way;
* the kernel cannot be reached or is slow: the change is made, one ``not_checked`` line, never an exception;
* every ask carries the erase stamp of every member it names, taken BEFORE the first await that reads the reminder's owner;
* a report goes out only after the caller's own transaction ended (an accepted suggestion);
* structure: every function that writes the table asks first; every caller that tells the door who a reminder is for takes its
  stamps before anything it awaits; nobody else puts an ask to the kernel.

A fake kernel on a real Unix socket (``test_kernel_door.AreaKernel``). Each test names the control that was run against it (the
code broken, the test seen to fail, the code restored). Invented ids and words only.
"""
from __future__ import annotations

import ast
import json
import logging
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import kernel_door
import os_shadow
import reminder_service as rs
import user_erase_gate
from models import ReminderCreate
from test_kernel_door import AreaKernel
from test_os_roster_sync import _scan, _takes_first
from test_os_shadow import SVC

pytestmark = pytest.mark.ci_safe

OWNER, KID, OTHER = "member-ova", "member-kip", "member-adu"
RID, WORD = "rem_bar_0001", "womble"
FROZEN = datetime(2026, 6, 10, 4, 0, tzinfo=timezone.utc)

#: What each door issued BEFORE the door existed (captured on the one-door commit, where ``reminder_service`` holds no kernel
#: code at all): an independent record, so a change that alters flag-on and flag-off alike still fails the flag-off test.
BEFORE_THE_DOOR = {
    "create_reminder_record": [
        "INSERT INTO reminders ( id, user_id, title, description, reminder_type, category, priority, due_date, due_time, "
        "recurring_pattern, is_active, acknowledged, snoozed_until, visibility, deleted ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, ?, 0)",
        "INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, NOW())",
        "COMMIT",
        "SELECT * FROM reminders WHERE id = ?"],
    "update_reminder_record": [
        "SELECT * FROM reminders WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0 AND id = ?",
        "UPDATE reminders SET title = ?, updated_at = NOW(), schedule_generation = COALESCE(schedule_generation, 0) + 1 WHERE id = ?",
        "COMMIT",
        "SELECT * FROM reminders WHERE id = ?"],
    "delete_reminder_record": [
        "SELECT * FROM reminders WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0 AND id = ?",
        "UPDATE reminders SET deleted = 1, updated_at = NOW() WHERE id = ?",
        "COMMIT"],
    "snooze_reminder_record": [
        "SELECT * FROM reminders WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0 AND id = ?",
        "UPDATE reminders SET snoozed_until = ?, acknowledged = 0, updated_at = NOW(), "
        "schedule_generation = COALESCE(schedule_generation, 0) + 1 WHERE id = ?",
        "INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, NOW())",
        "COMMIT",
        "SELECT * FROM reminders WHERE id = ?"],
    "acknowledge_reminder_record": [
        "SELECT * FROM reminders WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0 AND id = ?",
        "UPDATE reminders SET acknowledged = 1, updated_at = NOW() WHERE id = ?",
        "INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, NOW())",
        "COMMIT",
        "SELECT * FROM reminders WHERE id = ?"],
}


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)


class DB:
    """Records every statement; one reminder row (``rem_bar_0001``, for ``owner``). ``on_read`` runs while the row is read."""

    def __init__(self, owner=OWNER, visibility="family", on_read=None, fail_write=False):
        self.sql, self.on_read, self.fail_write = [], on_read, fail_write
        self.row = {"id": RID, "user_id": owner, "title": WORD, "visibility": visibility, "due_date": "2099-06-15", "due_time": "08:00",
                    "recurring_pattern": None, "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None}

    async def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        self.sql.append(norm)
        if norm.startswith("SELECT"):
            if self.on_read and "visibility = 'family' OR" in norm:
                self.on_read()
            return _Cur([dict(self.row)])
        if self.fail_write and "reminders" in norm:
            raise RuntimeError("write failed")
        return _Cur([])

    async def commit(self):
        self.sql.append("COMMIT")

    @property
    def writes(self):
        return [s for s in self.sql if not s.startswith("SELECT") and s != "COMMIT"]


def _actor(who):
    return {"user_id": who, "role": "user"}


#: door function -> (the wire verb, a call of it on ``db`` by ``who``)
DOORS = {
    "create_reminder_record": ("reminder.create", lambda db, who, **kw: rs.create_reminder_record(
        ReminderCreate(title=WORD, due_date="2099-06-16", due_time="15:00", visibility=db.row["visibility"]), user=_actor(who), db=db,
        schedule=False, **kw)),
    "update_reminder_record": ("reminder.edit", lambda db, who: rs.update_reminder_record(db, _actor(who), RID, {"title": WORD + " two"})),
    "delete_reminder_record": ("reminder.delete", lambda db, who: rs.delete_reminder_record(db, _actor(who), RID)),
    "snooze_reminder_record": ("reminder.snooze", lambda db, who: rs.snooze_reminder_record(db, _actor(who), RID, 10, now_utc=FROZEN)),
    "acknowledge_reminder_record": ("reminder.done", lambda db, who: rs.acknowledge_reminder_record(db, _actor(who), RID)),
}


@pytest.fixture
def quiet(monkeypatch):
    """No role matrix, panels or scheduler; ``jobs`` records every touch of a scheduled job."""
    jobs = []

    async def noop(*a, **k):
        return None

    async def cancel(rid):
        jobs.append(("cancel", rid))

    async def schedule(**kw):
        jobs.append(("schedule", kw.get("item_id")))

    monkeypatch.setattr(rs, "require_feature_access", noop)
    monkeypatch.setattr(rs.broadcaster, "broadcast", noop)
    monkeypatch.setattr(rs, "_cancel_reminder_jobs_safe", cancel)
    monkeypatch.setattr(rs, "_reschedule_reminder_due_safe", noop)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", schedule)
    return jobs


@pytest.fixture
def door(monkeypatch, quiet):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [OWNER, KID, OTHER]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_REMINDERS", "kernel")
    monkeypatch.delenv("ZOE_OS_LISTS", raising=False)
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.jobs = quiet
    yield k
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def _reports(door, n, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(door.ops("door_report")) < n:
        time.sleep(0.01)
    return [(a["ask"], a["outcome"]) for a in door.ops("door_report")]


# ---- flag off: main's behaviour, exactly ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(DOORS))
@pytest.mark.parametrize("value", [None, "off", "shadow", "on", "KERNELS"])
@pytest.mark.asyncio
async def test_any_other_flag_value_makes_no_kernel_call_no_thread_and_the_same_statements(door, monkeypatch, name, value):
    """Control run: with ``_gate``'s flag check removed (always ask), the ``never`` stub raises here for every door; with a second
    ``db.commit()`` planted in the delete door the comparison with ``BEFORE_THE_DOOR`` fails for it."""
    async def never(*a, **k):
        raise AssertionError("the kernel was asked with the flag off")

    def never_sync(*a, **k):
        raise AssertionError("the door was entered with the flag off")

    allowed = DB()
    await DOORS[name][1](allowed, OWNER)                       # flag on, allowed: the statements to compare against
    asked = len(door.ops("door_ask"))
    assert asked == 1
    _reports(door, 1)
    os_shadow._reset_for_tests()                               # the door thread is gone: any use from here would start it again
    connections = door.connections
    monkeypatch.setattr(os_shadow, "ask_door", never)
    monkeypatch.setattr(os_shadow, "report_door", never_sync)
    monkeypatch.setattr(os_shadow, "door_unchecked", never_sync)
    monkeypatch.setattr(os_shadow, "stamp_snapshot", never_sync)
    if value is None:
        monkeypatch.delenv("ZOE_OS_REMINDERS")
    else:
        monkeypatch.setenv("ZOE_OS_REMINDERS", value)
    off = DB()
    await DOORS[name][1](off, OWNER)
    assert off.sql == BEFORE_THE_DOOR[name] and off.writes     # the statements of the code before the door, exactly
    assert allowed.sql == BEFORE_THE_DOOR[name]                # ... and the door itself reads nothing, even when it is on
    assert len(door.ops("door_ask")) == asked and door.connections == connections      # no kernel call, no socket
    assert os_shadow._door_worker is None                      # no thread


# ---- allowed: ids only, reported after the write ----------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(DOORS))
@pytest.mark.asyncio
async def test_every_door_asks_once_with_ids_only_and_reports_done(door, caplog, name):
    """Control run: with the ``_gate`` call removed from one door (delete), its case fails at ``len(asks) == 1``."""
    verb = DOORS[name][0]
    db = DB(owner=KID, visibility="family")
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        await DOORS[name][1](db, KID)
    (ask,) = door.ops("door_ask")
    assert set(ask) == {"area", "verb", "person", "target", "owner", "visibility", "stamps"}
    assert (ask["area"], ask["verb"], ask["person"], ask["owner"], ask["visibility"]) == ("reminders", verb, KID, KID, "household")
    assert ask["stamps"] == {KID: os_shadow.erase_stamp(KID)}
    assert (ask["target"] == RID) is (name != "create_reminder_record") and len(ask["target"]) <= 80
    assert _reports(door, 1) == [(1, "done")] and door.ops("door_report")[0]["stamps"] == ask["stamps"]
    sent = json.dumps(door.calls)
    assert WORD not in sent and "2099" not in sent and "15:00" not in sent            # never the title, the day or the time
    lines = [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]
    assert len(lines) == 1 and f"area=reminders verb={verb} decision=allow reason=- ask_ms=" in lines[0]
    assert all(word not in "".join(lines) for word in (WORD, KID, RID))
    assert db.writes


@pytest.mark.asyncio
async def test_the_ask_names_the_person_the_reminder_is_for_and_the_shared_panel_is_never_named(door):
    """Control run: with ``owner=row.get("user_id")`` replaced by the actor's id in the edit door, ``owner`` is wrong here."""
    await DOORS["update_reminder_record"][1](DB(owner=KID, visibility="family"), OTHER)
    await DOORS["delete_reminder_record"][1](DB(owner=OWNER, visibility="family"), "guest-kiosk-7")
    first, second = door.ops("door_ask")
    assert (first["person"], first["owner"], set(first["stamps"])) == (OTHER, KID, {OTHER, KID})
    assert second.get("via") == "panel" and second["person"] == "panel" and set(second["stamps"]) == {OWNER}
    assert "kiosk" not in json.dumps(door.calls)
    await DOORS["update_reminder_record"][1](DB(owner=KID, visibility="personal"), KID)
    assert door.ops("door_ask")[2]["visibility"] == "private"


# ---- refused: final, nothing written, no job touched ------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(DOORS))
@pytest.mark.parametrize("reason", ["not_owner", "adults_only", "unknown_person"])
@pytest.mark.asyncio
async def test_a_kernel_refusal_is_final_nothing_is_written_and_no_job_is_touched(door, caplog, name, reason):
    """The control is the test below (the same call with the ask stubbed to allow DOES write and touch the job)."""
    door.door = f"refuse:{reason}"
    db = DB(owner=KID)                                          # KID's own row: this service's own rule lets every verb through
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        with pytest.raises(rs.ReminderRefused) as exc:
            await DOORS[name][1](db, KID)
    assert exc.value.reason == reason and exc.value.area == "reminders"
    assert db.writes == [] and "COMMIT" not in db.sql and door.jobs == []
    assert door.ops("door_report") == []
    assert [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()][0].startswith(
        f"ZOE_OS_ACT area=reminders verb={DOORS[name][0]} decision=refuse reason={reason}")
    line = exc.value.line()
    assert line and WORD not in line and KID not in line and isinstance(exc.value, kernel_door.Refused)


@pytest.mark.asyncio
async def test_the_refuse_test_has_teeth_a_door_whose_ask_always_allows_writes_the_refused_change(door, monkeypatch):
    door.door = "refuse:not_owner"

    async def always(*a, **k):
        return ("allow", "", None, None)

    monkeypatch.setattr(os_shadow, "ask_door", always)
    db = DB(owner=KID)
    await DOORS["delete_reminder_record"][1](db, KID)
    assert db.writes and door.jobs == [("cancel", RID)]


# ---- the kernel is down, slow or erased: the change proceeds ----------------------------------------------------------------------
@pytest.mark.parametrize("how,reason", [("close", "unavailable"), ("old_kernel", "protocol"), ("slow", "timeout"), ("erased", "erased")])
@pytest.mark.asyncio
async def test_a_kernel_that_cannot_answer_lets_the_change_proceed_not_checked(door, caplog, monkeypatch, how, reason):
    """``old_kernel``: a kernel from before ``door_ask`` answers "unknown op". Control run: with ``not_checked`` treated as a
    refusal in ``_gate`` all four raise."""
    if how == "slow":
        door.delay = 0.5
    elif how == "erased":
        door.door = "refuse:" + os_shadow.KERNEL_ERASED
    else:
        door.door = how
    db = DB()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        await DOORS["snooze_reminder_record"][1](db, OWNER)
    assert db.sql == BEFORE_THE_DOOR["snooze_reminder_record"]
    (line,) = [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]
    assert "decision=not_checked" in line and "area=reminders" in line
    assert f"reason={reason}" in line or (how == "close" and "reason=" in line)
    assert door.ops("door_report") == []                       # nothing was allowed, so nothing is reported


@pytest.mark.asyncio
async def test_a_write_that_fails_is_reported_failed_never_done(door):
    """Control run: with ``_reported`` reporting ``done`` on every exit this sees ``done``."""
    with pytest.raises(RuntimeError):
        await DOORS["update_reminder_record"][1](DB(fail_write=True), OWNER)
    assert _reports(door, 1) == [(1, "failed")]


# ---- ordered against a forget: the stamps are taken before anything that names the owner is read ----------------------------------
@pytest.mark.parametrize("name", sorted(n for n in DOORS if n != "create_reminder_record"))
@pytest.mark.asyncio
async def test_an_owner_forgotten_while_the_reminder_row_is_read_keeps_the_stamp_from_before(door, name):
    """The door reads who the reminder is for from the row; a forget of that member confirmed while the read is awaited must not
    make the ask look newer than the forget. Control run: with ``stamps_at = stamps_now()`` moved below ``load_for_actor`` in
    one door (snooze), its case fails (the ask carries the moved-on stamp)."""
    before = os_shadow.erase_stamp(KID)

    def forgotten():                                           # the kernel confirmed a whole-member forget of KID meanwhile
        os_shadow._forget_marks[KID] += 1

    await DOORS[name][1](DB(owner=KID, visibility="family", on_read=forgotten), KID)
    assert door.ops("door_ask")[0]["stamps"][KID] == before and os_shadow.erase_stamp(KID) > before


@pytest.mark.asyncio
async def test_a_caller_that_tells_the_door_who_the_reminder_is_for_hands_over_its_own_earlier_stamps(door):
    """Control run: with ``stamps_at`` ignored by ``create_reminder_record`` (always its own snapshot) the stamp is the later one."""
    earlier = rs.stamps_now()
    before = os_shadow.erase_stamp(KID)
    os_shadow._forget_marks[KID] += 1                          # KID's forget is confirmed while the caller is on its way to the door
    await DOORS["create_reminder_record"][1](DB(owner=KID), KID, stamps_at=earlier)
    assert door.ops("door_ask")[0]["stamps"][KID] == before
    await DOORS["create_reminder_record"][1](DB(owner=KID), KID)          # no caller snapshot: taken at the door's first statement
    assert door.ops("door_ask")[1]["stamps"][KID] == os_shadow.erase_stamp(KID)


@pytest.mark.asyncio
async def test_an_ask_is_never_queued_while_a_member_it_names_is_being_erased(door, caplog):
    """Control run: with BOTH erase-gate reads removed (``os_shadow._ask`` on the loop, ``_door_ask`` on the door thread) the ask
    reaches the kernel. (Either one alone still holds it back; which of the two an ask needs is the lists tests' subject.)"""
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        with user_erase_gate.erase_span(KID):
            await DOORS["update_reminder_record"][1](DB(owner=KID), OWNER)      # KID is the person it is for, not the actor
    assert door.ops("door_ask") == []
    assert "decision=not_checked reason=erasing" in [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()][0]


@pytest.mark.asyncio
async def test_a_member_the_roster_does_not_name_triggers_the_roster_rewrite(door, monkeypatch):
    """Every member an ask names (the actor and the person the reminder is for) is handed to the roster trigger, as for lists.
    Control run: with ``_roster_wanted(tuple(stamps))`` given only the actor, the owner is missing here."""
    seen = []
    monkeypatch.setattr(os_shadow, "_roster_wanted", lambda members: seen.append(set(members)))
    await DOORS["update_reminder_record"][1](DB(owner="member-new", visibility="family"), OWNER)
    assert seen == [{OWNER, "member-new"}]


# ---- the watch-only hook is silent while the door is on ---------------------------------------------------------------------------
def test_the_watch_only_reminder_hook_is_silent_while_the_door_is_on(door, monkeypatch):
    """The door is then the kernel's one record of a reminder; the hook would record it twice. Control run: with
    ``REMINDER_SHADOW_VERBS`` emptied the first assertion fails."""
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "list_add,reminder_create")
    assert os_shadow.wants("reminder_create") is False and os_shadow.wants("list_add") is True
    monkeypatch.setenv("ZOE_OS_REMINDERS", "off")
    assert os_shadow.wants("reminder_create") is True


# ---- each caller says the refusal in its own shape --------------------------------------------------------------------------------
async def _noop(*a, **k):
    return None


def test_the_rest_routes_answer_403_with_the_plain_sentence(door, monkeypatch):
    """Control run: with ``ReminderRefused`` made a plain ``Exception`` (not a ``kernel_door.Refused``) the one handler does not
    cover it and the route raises."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import reminders

    door.door = "refuse:adults_only"
    db = DB(owner=KID)
    app = FastAPI()
    app.include_router(reminders.router)
    app.add_exception_handler(kernel_door.Refused, kernel_door.refused_response)
    app.dependency_overrides[reminders.get_current_user] = lambda: _actor(KID)
    app.dependency_overrides[reminders.get_db] = lambda: db
    with TestClient(app) as client:
        for method, path, body in (("delete", f"/api/reminders/{RID}", None), ("put", f"/api/reminders/{RID}", {"title": WORD}),
                                   ("post", f"/api/reminders/{RID}/snooze", {"snooze_minutes": 10}),
                                   ("post", f"/api/reminders/{RID}/acknowledge", None),
                                   ("post", "/api/reminders/", {"title": WORD, "due_date": "2099-06-16"})):
            out = getattr(client, method)(path, **({"json": body} if body else {}))
            assert out.status_code == 403 and out.json() == {"detail": rs.ReminderRefused("adults_only").line()}, path
    assert db.writes == []


@pytest.mark.asyncio
async def test_the_brain_tools_return_an_error_result_and_change_nothing(door, monkeypatch):
    """Control run: with ``kernel_door.Refused`` dropped from the tools' ``except`` the refusal escapes as an exception."""
    import mcp_server

    async def actor(db, name, args, ctx):
        return _actor(KID)

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    monkeypatch.setattr(mcp_server, "_notify_ui", _noop)
    door.door = "refuse:not_owner"
    line = rs.ReminderRefused("not_owner").line()
    db = DB(owner=KID)
    for tool, args in (("reminder_update", {"title": WORD}), ("reminder_delete", {}), ("reminder_snooze", {"minutes": 5})):
        out = await mcp_server._execute_tool(db, tool, {"reminder_id": RID, **args})
        assert out == {"error": line + " Nothing was changed.", "status": "refused"}, tool
    out = await mcp_server._execute_tool(db, "reminder_create", {"title": WORD, "due_date": "2099-06-16"})
    assert out == {"error": line + " Nothing was made.", "status": "refused"}
    assert db.writes == [] and len(door.ops("door_ask")) == 4


@pytest.mark.asyncio
async def test_a_spoken_turn_is_told_plainly(door, monkeypatch):
    """Control run: with the ``ReminderRefused`` branch removed from ``_execute_reminder_create_direct`` the refusal is swallowed
    by the broad ``except`` and the turn falls through (None), so this fails."""
    from contextlib import asynccontextmanager

    import intent_router
    from intent_router import Intent, execute_intent

    db = DB(owner=KID)

    @asynccontextmanager
    async def ctx():
        yield db

    async def user(_db, uid):
        return _actor(uid)

    monkeypatch.setattr("database.get_db_ctx", ctx)
    monkeypatch.setattr(intent_router, "_load_direct_execution_user", user)
    door.door = "refuse:unknown_person"
    line = rs.ReminderRefused("unknown_person").line()
    assert await execute_intent(Intent("reminder_create", {"title": WORD, "date": "2099-06-16"}), KID) == line
    assert await execute_intent(Intent("reminder_cancel", {"title": WORD}), KID) == line
    assert db.writes == []


# ---- an accepted suggestion: reported only after its own transaction ends ---------------------------------------------------------
@pytest.mark.asyncio
async def test_an_accepted_suggestion_reports_only_after_its_own_transaction_ends(door, monkeypatch):
    """The suggestion's transaction holds the reminder insert: the kernel hears ``done`` after that commit and ``failed`` (never
    ``done``) when it rolls back. Control run: without ``kernel_door.reports_after`` around that transaction the rolled-back
    accept is reported ``done``."""
    import pending_suggestions
    from test_reminders_one_door import _PgPool, _PgState

    seen = []
    real = os_shadow.report_door
    state = _PgState()
    pool = _PgPool(state)
    monkeypatch.setattr(os_shadow, "report_door",
                        lambda area, ask, outcome, stamps=None: (seen.append((pool.conns[-1].depth, outcome)), real(area, ask, outcome, stamps)))
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: pool)
    monkeypatch.setattr("proactive.triggers.reminder_scan.schedule_due_reminder", _noop)
    monkeypatch.setattr("anticipation.mode", lambda: "off")
    state.fail_after_insert = True                              # the reminder is inserted, then the transaction rolls back
    assert not (await pending_suggestions.execute_suggestion("s1", KID))["ok"]
    assert seen == [(0, "failed")] and state.reminders == {}
    state.fail_after_insert = False
    assert (await pending_suggestions.execute_suggestion("s1", KID))["ok"]
    assert seen == [(0, "failed"), (0, "done")]                 # depth 0: outside the transaction, after its commit
    assert [a["verb"] for a in door.ops("door_ask")] == ["reminder.create"] * 2


# ---- structure --------------------------------------------------------------------------------------------------------------------
def _modules() -> list:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return sorted(p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts))


def _ungated(src: str) -> list:
    """The functions that write the ``reminders`` table without, EARLIER in the same function, a ``_gate`` call whose result is
    handed to an ``async with _reported(...)`` that encloses the write, or without ``stamps_now()`` taken before the first await."""
    from test_reminders_table_one_writer import WRITE

    out = []
    for fn in [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        writes = [n for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str) and WRITE.search(n.value)]
        if not writes:
            continue
        gates = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_gate"]
        blocks = [n for n in ast.walk(fn) if isinstance(n, ast.AsyncWith) and any(
            isinstance(i.context_expr, ast.Call) and getattr(i.context_expr.func, "id", "") == "_reported" for i in n.items)]
        inside = all(any(any(c is w for c in ast.walk(b)) for b in blocks) for w in writes)
        first = min(w.lineno for w in writes)
        stamped = any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "stamps_now"
                      and n.lineno < min([a.lineno for a in ast.walk(fn) if isinstance(a, ast.Await)] or [10 ** 9])
                      for n in ast.walk(fn))
        if len(gates) != 1 or gates[0].lineno >= first or not inside or not stamped:
            out.append(fn.name)
    return out


def test_every_function_that_writes_the_reminders_table_asks_first_and_reports():
    """Control (planted below): a writer with no gate, one whose write sits outside the reported block, one that gates after
    its write, and one that takes its stamps after an await are all reported."""
    assert _ungated((SVC / "reminder_service.py").read_text("utf-8")) == []
    planted = ("async def bare(db):\n    stamps_at = stamps_now()\n    await db.execute('UPDATE reminders SET x = 1')\n"
               "async def outside(db):\n    stamps_at = stamps_now()\n    asked = await _gate('v')\n"
               "    async with _reported(asked):\n        pass\n    await db.execute('UPDATE reminders SET x = 1')\n"
               "async def late(db):\n    stamps_at = stamps_now()\n    async with _reported(None):\n"
               "        await db.execute('DELETE FROM reminders WHERE id = 1')\n    asked = await _gate('v')\n"
               "async def unstamped(db):\n    await db.fetch('x')\n    stamps_at = stamps_now()\n    asked = await _gate('v')\n"
               "    async with _reported(asked):\n        await db.execute('UPDATE reminders SET x = 1')\n"
               "async def good(db):\n    stamps_at = stamps_now()\n    asked = await _gate('v')\n"
               "    async with _reported(asked):\n        await db.execute('UPDATE reminders SET x = 1')\n")
    assert _ungated(planted) == ["bare", "outside", "late", "unstamped"]
    fns = {n.name for n in ast.walk(ast.parse((SVC / "reminder_service.py").read_text("utf-8")))
           if isinstance(n, ast.AsyncFunctionDef) and n.name.endswith("_reminder_record")}
    assert fns == set(DOORS)                                    # every door has a behaviour test above


def test_every_caller_that_makes_a_reminder_takes_its_stamps_before_anything_it_awaits():
    """Structural, over EVERY module of the service: each function that calls ``create_reminder_record`` (the one door that is
    TOLD who the reminder is for) hands it ``stamps_at=stamps_at``, taken from ``stamps_now()`` before that function's first
    ``await``. The scan is the lists one (``test_os_roster_sync._scan``), so aliases are resolved to a fixed point.

    Control (planted below): a caller that awaits first, one that passes nothing, and callers hidden behind one, two and three
    names are all reported. Control run on the real tree: with ``stamps_at=stamps_at`` dropped from ``commitments._make_reminder``
    this fails naming ``commitments.py``."""
    modules = _modules()
    assert len(modules) > 100 and SVC / "routers" / "reminders.py" in modules and SVC / "commitments.py" in modules
    callers = 0
    for path in modules:
        src = path.read_text("utf-8")
        assert _scan(src, "create_reminder_record") == [], path.name
        callers += "create_reminder_record(" in src and path.name != "reminder_service.py"
    assert callers >= 8                                         # the scan saw the callers it is about
    planted = ("from reminder_service import create_reminder_record as make\nimport reminder_service as rs\nbuild = make\nstart = build\n"
               "async def late(db):\n    await db.fetch('x')\n    stamps_at = stamps_now()\n"
               "    await create_reminder_record(p, user=u, db=db, stamps_at=stamps_at)\n"
               "async def silent(db):\n    stamps_at = stamps_now()\n    await create_reminder_record(p, user=u, db=db)\n"
               "async def renamed(db):\n    await db.fetch('x')\n    await make(p, user=u, db=db)\n"
               "async def through_module(db):\n    await db.fetch('x')\n    await rs.create_reminder_record(p, user=u, db=db)\n"
               "async def twice(db):\n    await db.fetch('x')\n    await build(p, user=u, db=db)\n"
               "async def thrice(db):\n    await db.fetch('x')\n    await start(p, user=u, db=db)\n"
               "async def good(db):\n    stamps_at = rs.stamps_now()\n    await db.fetch('x')\n"
               "    await start(p, user=u, db=db, stamps_at=stamps_at)\n")
    assert _scan(planted, "create_reminder_record") == ["late", "silent", "renamed", "through_module", "twice", "thrice"]
    assert _takes_first is not None


def test_nobody_but_a_door_puts_an_ask_to_the_kernel_and_the_service_never_imports_the_lab():
    """Control (planted below): an aliased import of the ask, and an import from ``labs``, are both reported."""
    def offends(src: str) -> list:
        out = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.module in ("os_shadow", "kernel_door"):
                out += [a.name for a in node.names if a.name in ("ask_door", "ask_list", "_ask", "report_door", "report_list")]
            elif isinstance(node, ast.Attribute) and node.attr in ("ask_door", "ask_list", "_ask") and getattr(node.value, "id", "") != "self":
                out.append(node.attr)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                out += [n for n in names if n.split(".")[0] in ("labs", "zoe_os")]
        return out

    doors = {"list_service.py", "reminder_service.py", "os_shadow.py", "kernel_door.py"}
    for path in _modules():
        if path.name not in doors:
            assert offends(path.read_text("utf-8")) == [], path.name
    assert offends("from os_shadow import ask_door as a\n") == ["ask_door"]
    assert offends("import os_shadow as o\nasync def f():\n    await o.ask_door('reminders', 'v', 'u')\n") == ["ask_door"]
    assert offends("from labs.zoe_os import door\nimport zoe_os.wire\n") == ["labs.zoe_os", "zoe_os.wire"]
    for name in doors:                                          # ... and a door module itself never reaches into the lab
        assert [o for o in offends((SVC / name).read_text("utf-8")) if o.split(".")[0] in ("labs", "zoe_os")] == [], name
