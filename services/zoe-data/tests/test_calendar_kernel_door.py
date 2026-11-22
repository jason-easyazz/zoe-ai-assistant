"""The calendar goes to the Zoe OS kernel (``ZOE_OS_CALENDAR=kernel``; docs/knowledge/zoe-os-calendar-door.md).

* any other flag value: NO kernel call, no socket, no thread, no extra statement: every one of the 59 entry-point scenarios
  gives the record captured from ``main`` before the door existed;
* adding, changing and deleting an event asks first: allowed = made and reported; refused = ``EventRefused``, nothing written,
  each caller says so in its own way; kernel down or slow = made, one ``not_checked`` line;
* only ids, stamps and closed codes cross the socket and reach the log: never a title, a day, a time, a place or a guest;
* a household event of the house's own names no owner, and the panel is never named;
* a spoken change that touched no row (someone else's event) is reported ``failed``; an accepted suggestion is reported only
  once its transaction has ended;
* the erase stamps an ask carries are the ones its caller took before it read the event;
* structure: every caller of the door says who is acting and hands it stamps taken before anything it awaits; every door
  function asks before it writes.

A fake kernel on a real Unix socket (``test_kernel_door.AreaKernel``); the calendar on a SQLite file with the real migration
(``calendar_scenarios``). Each test names the control that was run against it. Invented ids and words only.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

import pytest

import calendar_scenarios as scenarios
import calendar_service as cal
import kernel_door
import os_shadow
import user_erase_gate
from calendar_scenarios import ANN, BOB, DAY, HOUSE, PANEL, REQUEST, WORD
from test_kernel_door import AreaKernel
from test_os_roster_sync import _door_names, _scan
from test_os_shadow import SVC

pytestmark = pytest.mark.ci_safe

ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "calendar_as_on_main.json").read_text("utf-8"))
OTHER_FLAGS = ("ZOE_OS_LISTS", "ZOE_OS_REMINDERS", "ZOE_OS_ALARMS")
SEEDED = 7                          # the events every scenario world starts with


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ANN, BOB]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_CALENDAR", "kernel")
    for other in OTHER_FLAGS:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    yield k
    scenarios._restore()
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def _wait(door, op, n, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(door.ops(op)) < n:
        time.sleep(0.01)
    return door.ops(op)


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]


def _world(log=None):
    return scenarios.World(SVC, [] if log is None else log)


def _member(uid):
    return {"user_id": uid, "role": "user"}


def _events(w) -> dict:
    return {r["id"]: r for r in w.rows()["events"]}


async def _create(w, user=None, **fields):
    from models import EventCreate

    return await w.rest.create_event(EventCreate(**{"title": WORD, "start_date": DAY, **fields}), user=user or _member(ANN),
                                     db=w.db, request=REQUEST)


async def _edit(w, event_id, user=None, **fields):
    from models import EventUpdate

    return await w.rest.update_event(event_id, EventUpdate(**fields), user=user or _member(ANN), db=w.db, request=REQUEST)


async def _delete(w, event_id, user=None):
    return await w.rest.delete_event(event_id, user=user or _member(ANN), db=w.db, request=REQUEST)


# ---- flag off: main's behaviour, exactly ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "off", "shadow", "on", "KERNELS"])
def test_any_other_flag_value_is_main_exactly_for_every_entry_point(door, monkeypatch, value):
    """All 59 scenarios (every statement with its values, commit, hook, receipt, result, the rows afterwards) equal the record
    captured from main, with every way into the kernel wired to fail the test. Nothing in the record is masked or dropped: the
    clock is frozen and ids are counted, so attendee and alarm ids and every stamp are compared as stored. Controls run: with the
    flag check removed from ``calendar_service._gate`` (always ask) the ``never`` stub raises in the first create and the record
    differs; an attendee id, an alarm id (``str(uuid.uuid4())[:-1] + "f"`` in ``replace_attendees`` / ``replace_alarms``) or a
    card's ``created_at`` moved one second in ``card_service.build_card`` each fails this."""
    def never(*a, **k):
        raise AssertionError("the kernel was reached with the flag off")

    if value is None:
        monkeypatch.delenv("ZOE_OS_CALENDAR")
    else:
        monkeypatch.setenv("ZOE_OS_CALENDAR", value)
    for name in ("ask_door", "record_door", "report_door", "door_unchecked", "stamp_snapshot"):
        monkeypatch.setattr(os_shadow, name, never)
    assert scenarios.run_all() == ON_MAIN
    assert door.calls == [] and door.connections == 0 and os_shadow._door_worker is None      # no call, no socket, no thread


def test_with_the_door_on_and_allowing_every_entry_point_still_issues_mains_statements(door):
    """The door adds no statement of its own even when it is on (every caller hands it the event it already read), and nothing
    a person hears or the store holds changes when the kernel allows. Control run: with ``delete_event_record`` reading the row
    itself before it asks, ``rest_delete_own`` gains a statement and this fails; the same attendee-id, alarm-id and card-stamp
    changes as the flag-off test each fail this too."""
    assert scenarios.run_all() == ON_MAIN
    asked = {a["verb"] for a in door.ops("door_ask")}
    assert asked == {"event.create", "event.edit", "event.delete"} and {a["area"] for a in door.ops("door_ask")} == {"calendar"}


# ---- asked first -------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_change_asks_with_ids_only_and_is_reported_after_the_write(door, caplog):
    """Control run: with ``_gate`` dropped from ``update_event_record`` there is no ``event.edit`` ask and this fails."""
    w = _world()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        made = await _create(w, start_time="15:00", location="shed", description="bring the womble", visibility="personal")
        await _edit(w, made["id"], title=WORD + " two", start_time="09:30")
        await _delete(w, made["id"])
    asks = _wait(door, "door_ask", 3)
    assert [x["verb"] for x in asks] == ["event.create", "event.edit", "event.delete"]
    for x in asks:
        assert set(x) == {"area", "verb", "person", "target", "owner", "visibility", "stamps"}
        assert (x["area"], x["person"], x["owner"], x["visibility"], x["target"]) == ("calendar", ANN, ANN, "private", made["id"])
        assert x["stamps"] == {ANN: os_shadow.erase_stamp(ANN)}
    assert [r["outcome"] for r in _wait(door, "door_report", 3)] == ["done"] * 3
    sent = json.dumps(door.calls)
    assert all(word not in sent for word in (WORD, DAY, "15:00", "09:30", "shed", "bring"))     # never a title, day, time, place or note
    lines = _lines(caplog)
    assert len(lines) == 3 and all("area=calendar" in line and "decision=allow reason=-" in line for line in lines)
    assert all(word not in "".join(lines) for word in (WORD, ANN, made["id"]))


@pytest.mark.asyncio
async def test_a_household_event_says_so_and_another_members_names_them(door):
    """Production's ``family`` is told as ``household``; the owner is whose calendar the STORED event is on. Control run: with
    ``owner=user_id`` (the actor) passed by the route instead of the row's owner, the edit's ``owner`` is BOB and this fails."""
    w = _world()
    await _edit(w, "ev-ann-family", _member(BOB), location="shed")
    await _delete(w, "ev-ann-family", _member(BOB))
    for x in _wait(door, "door_ask", 2):
        assert (x["person"], x["owner"], x["visibility"], x["target"]) == (BOB, ANN, "household", "ev-ann-family")
        assert set(x["stamps"]) == {ANN, BOB}


@pytest.mark.asyncio
async def test_the_houses_own_event_names_no_owner_and_the_panel_is_never_named(door):
    """An event made at the panel is kept under the household's own marker, which is not a person: the ask says ``household``
    and tells no owner. Control run: with the marker sent as the owner the ask carries ``owner`` (and the real kernel would
    refuse every panel event as ``unknown_person``)."""
    w = _world()
    made = await _create(w, PANEL)
    await _edit(w, "ev-house", PANEL, title=WORD)
    await _delete(w, made["id"], _member(BOB))
    first, second, third = _wait(door, "door_ask", 3)
    for x in (first, second):
        assert (x.get("via"), x["person"], x["visibility"]) == ("panel", "panel", "household") and "owner" not in x and "stamps" not in x
    assert (third["person"], third["visibility"], set(third["stamps"])) == (BOB, "household", {BOB}) and "owner" not in third
    sent = json.dumps(door.calls)
    assert all(word not in sent for word in ("panel-zed", HOUSE, "__household__", "guest"))


@pytest.mark.parametrize("reason", ["not_owner", "adults_only", "unknown_person"])
@pytest.mark.asyncio
async def test_a_kernel_refusal_is_final_nothing_is_written_and_each_caller_says_so(door, reason):
    """The screen answers 403 through the one app handler; the brain's tools return an error result; a spoken turn says the
    sentence; an accepted suggestion, the day review and an import raise (their callers keep the thing pending). Control run:
    with ``EventRefused`` not raised on a refusal (the decision ignored) the event is created and this fails."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from database import get_db
    from panel_principal import require_member_or_panel

    w = _world()
    before = _events(w)
    door.door = f"refuse:{reason}"
    line = cal.EventRefused(reason).line()
    assert WORD not in line and ANN not in line and isinstance(cal.EventRefused(reason), kernel_door.Refused)
    for call in (lambda: _create(w), lambda: _edit(w, "ev-ann-private", title=WORD), lambda: _delete(w, "ev-ann-private"),
                 lambda: _edit(w, "ev-ann-family", attendees=[{"display_name": "Quib"}]),
                 lambda: w.sugg._execute_action(w.pg, "calendar_create", {"title": WORD, "start_date": DAY}, ANN),
                 lambda: w.omi.write_calendar_event(ANN, {"id": "cand_bar_0001", "day_local": DAY,
                                                          "payload": {"title": WORD, "date": DAY}}, "review")):
        with pytest.raises(cal.EventRefused) as exc:
            await call()
        assert exc.value.reason == reason
    import calendar_ical

    with pytest.raises(cal.EventRefused):
        await calendar_ical.store_parsed(w.db, [{"event": {"title": WORD, "start_date": DAY}, "attendees": [], "alarms": []}],
                                         user_id=ANN, actor=ANN)
    tool = w.mcp._execute_tool
    assert await tool(w.db, "calendar_create_event", {"title": WORD, "start_date": DAY}, {"user_id": ANN}) == \
        {"error": line + " Nothing was added.", "status": "refused"}
    assert await tool(w.db, "calendar_update_event", {"event_id": "ev-ann-private", "title": WORD}, {"user_id": ANN}) == \
        {"error": line + " Nothing was changed.", "status": "refused"}
    assert await tool(w.db, "calendar_delete_event", {"event_id": "ev-ann-private"}, {"user_id": ANN}) == \
        {"error": line + " Nothing was deleted.", "status": "refused"}
    assert await w.router._execute_calendar_create_direct(w.router.Intent("calendar_create", {"title": WORD, "date": DAY}), ANN) == line
    for name in ("create", "move_own", "delete_own", "move_another_members"):
        out = await _spoken(w, name)
        assert out["spoken_summary"] == line and out["actions"] == [] and out["intent"]["status"] == "refused", name
    assert _events(w) == before and w.rows()["event_attendees"] == [] and door.ops("door_report") == []
    assert not [e for e in w.log if e[0] in ("receipt", "broadcast", "notify_ui", "notify_ui_channel")]      # nothing was announced

    app = FastAPI()
    app.include_router(w.rest.router)
    app.add_exception_handler(kernel_door.Refused, kernel_door.refused_response)
    app.dependency_overrides[require_member_or_panel] = lambda: _member(ANN)
    app.dependency_overrides[get_db] = lambda: w.db
    with TestClient(app) as client:
        out = client.post("/api/calendar/events", json={"title": WORD, "start_date": DAY})
        assert (out.status_code, out.json()) == (403, {"detail": line})
    assert _events(w) == before


def _spoken(w, name):
    """One spoken request as ``_resolve_with_db`` gets it (where a refusal is answered): the intent, and the calendar showing DAY."""
    from datetime import date

    S = w.sky.SkybridgeIntent
    intent = {"create": S("calendar", "create_event", start_date=date.fromisoformat(DAY), title=WORD, target_time="15:00"),
              "move_own": S("calendar", "update_time", target_time="09:00", target_text="snark"),
              "delete_own": S("calendar", "delete_event", target_text="snark"),
              "move_another_members": S("calendar", "update_time", target_time="09:00", target_text="quango")}[name]
    showing = {"intent": {"domain": "calendar"}, "cards": [{"content": {"source": "calendar_show", "start_date": DAY, "events": []}}]}
    return w.sky._resolve_with_db(intent, ANN, w.pg, context=showing)


@pytest.mark.parametrize("how,reason", [("close", "unavailable"), ("old_kernel", "protocol"), ("slow", "timeout")])
@pytest.mark.asyncio
async def test_a_kernel_that_cannot_answer_lets_a_change_proceed_not_checked(door, caplog, how, reason):
    """Control run: with ``not_checked`` treated as a refusal in ``_gate`` all three raise."""
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    w = _world()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        made = await _create(w)
        await _delete(w, "ev-ann-private")
    rows = _events(w)
    assert made["id"] in rows and rows["ev-ann-private"]["deleted"] == 1
    assert f"area=calendar verb=event.create decision=not_checked reason={reason}" in _lines(caplog)[0]


@pytest.mark.asyncio
async def test_a_writer_that_is_told_nobody_is_not_checked_and_says_so(door, caplog):
    """A caller that names no actor cannot be judged: the write proceeds and the log says ``no_actor`` (the structural test
    below keeps every real caller from being one). Control run: with the ``no_actor`` branch asking as the owner, an ask is sent."""
    w = _world()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        rec = await cal.create_event_record(w.db, user_id=ANN, title=WORD, start_date=DAY, stamps_at=cal.stamps_now())
    assert rec["id"] in _events(w) and door.ops("door_ask") == []
    assert "area=calendar verb=event.create decision=not_checked reason=no_actor" in _lines(caplog)[0]


# ---- how a write ended ----------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_spoken_change_that_touched_no_row_is_reported_failed(door):
    """The spoken statements name the speaker in their WHERE: on another member's household event they change nothing, and the
    kernel hears ``failed``, not ``done``. Control run: with ``_own_row_write`` reporting ``done`` whatever the count this sees
    ``done`` twice."""
    w = _world()
    said = scenarios.scenarios()
    assert "cannot move it" in (await said["spoken_move_another_members_household_event"](w))["spoken_summary"]
    assert (await said["spoken_move_own"](w))["spoken_summary"] == "Moved snark to 09:00."
    theirs, own = _wait(door, "door_ask", 2)
    assert (theirs["person"], theirs["owner"], theirs["visibility"], theirs["target"]) == (ANN, BOB, "household", "ev-bob-family")
    assert (own["person"], own["owner"], own["visibility"]) == (ANN, ANN, "private")
    assert [r["outcome"] for r in _wait(door, "door_report", 2)] == ["failed", "done"]


@pytest.mark.asyncio
async def test_a_write_that_raises_is_reported_failed(door):
    """Control run: with ``kernel_door.reported`` dropped from ``delete_event_record`` this sees no report."""
    w = _world()

    class Broken:
        async def execute(self, sql, params=()):
            raise RuntimeError("store down")

    with pytest.raises(RuntimeError):
        await cal.delete_event_record(Broken(), event_id="ev-ann-private", deleted_at="x", actor=ANN, owner=ANN,
                                      visibility="personal", stamps_at=cal.stamps_now())
    assert [r["outcome"] for r in _wait(door, "door_report", 1)] == ["failed"]


@pytest.mark.parametrize("ends", ["commits", "rolls_back"])
@pytest.mark.asyncio
async def test_an_accepted_suggestion_is_reported_only_once_its_transaction_has_ended(door, ends):
    """The suggestion's event is written inside the suggestion's own transaction (``execute_suggestion`` holds the reports):
    the kernel hears nothing while the row could still be undone. Control run: with ``kernel_door.wrote`` ignoring the held
    block (reporting at once), the report is seen before the block ends."""
    w = _world()
    try:
        async with kernel_door.reports_after():
            out = await w.sugg._execute_action(w.pg, "calendar_create", {"title": WORD, "start_date": DAY}, ANN)
            assert out["event_id"] and len(_wait(door, "door_ask", 1)) == 1
            await asyncio.sleep(0.05)
            assert door.ops("door_report") == []
            if ends == "rolls_back":
                raise RuntimeError("the transaction failed")
    except RuntimeError:
        pass
    assert [r["outcome"] for r in _wait(door, "door_report", 1)] == ["done" if ends == "commits" else "failed"]


# ---- ordered against a forget ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_ask_carries_the_stamps_its_caller_took_before_it_read_the_event(door):
    """A route takes its stamps first; the event (and so whose it is) is read afterwards. A forget of that member confirmed in
    between must not make the ask look newer than the forget. Control run: with ``_gate`` taking a fresh snapshot instead of
    the caller's, the ask carries the moved-on stamp."""
    w = _world()
    stamps_at = cal.stamps_now()                              # taken BEFORE ...
    before = os_shadow.erase_stamp(BOB)
    os_shadow._forget_marks[BOB] += 1                          # ... the kernel confirms a whole-member forget of BOB
    await cal.update_event_record(w.db, event_id="ev-bob-family", updates=["updated_at = NOW()"], params=["ev-bob-family"],
                                  actor=ANN, owner=BOB, visibility="family", stamps_at=stamps_at)
    (edit,) = _wait(door, "door_ask", 1)
    assert edit["owner"] == BOB and edit["stamps"][BOB] == before and os_shadow.erase_stamp(BOB) > before


def test_a_caller_copies_nothing_with_every_door_off(monkeypatch):
    """Flag off: taking the stamps reads flags and nothing else. Control run: with the snapshot taken unconditionally this fails."""
    for name in ("ZOE_OS_CALENDAR", *OTHER_FLAGS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(os_shadow, "stamp_snapshot", lambda: (_ for _ in ()).throw(AssertionError("copied with every door off")))
    assert cal.stamps_now() is None


# ---- structure --------------------------------------------------------------------------------------------------------------------
#: every function of the door that writes an event for a person; the first three are also told WHO is acting
DOOR_FNS = ("create_event_record", "update_event_record", "delete_event_record", "tool_update_event_record",
            "tool_delete_event_record", "spoken_create_event_record", "spoken_move_event_record", "spoken_delete_event_record",
            "suggested_create_event_record")
TOLD_AN_ACTOR = DOOR_FNS[:3]
READS_THE_ROW = ("update_event_record", "delete_event_record", "spoken_move_event_record", "spoken_delete_event_record")


def _modules() -> list:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return sorted(p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts))


def callers_that_leave_something_out(src: str) -> list:
    """``(function, door function, what is missing)`` for every call of a door function (by any alias, followed to a fixed
    point) that does not say who is acting (the three that are told), or whose it is (the four whose caller read the row)."""
    tree = ast.parse(src)
    by_name = {name: door_fn for door_fn in DOOR_FNS for name in _door_names(tree, door_fn)}
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            door_fn = by_name.get(getattr(call.func, "id", getattr(call.func, "attr", "")))
            if door_fn is None:
                continue
            given = {k.arg for k in call.keywords}
            need = ({"actor"} if door_fn in TOLD_AN_ACTOR else set()) | ({"owner", "visibility"} if door_fn in READS_THE_ROW else set())
            out += [(fn.name, door_fn, missing) for missing in sorted(need - given)]
    return sorted(set(out))


def test_every_caller_says_who_is_acting_and_takes_its_stamps_before_anything_it_awaits():
    """Over EVERY module of the service, for every door function: its caller hands it ``stamps_at=stamps_at`` taken from
    ``stamps_now()`` before that caller's first await, says who is acting, and (for a change to a stored event) whose it is.
    Controls (planted below): a caller with no ``actor=``, one behind two aliases, one that stamps after an await. Control run
    on the real tree: with ``stamps_at=stamps_at`` dropped from the spoken move this fails naming ``skybridge_service.py``;
    with ``actor=user_id`` dropped from the day review it fails naming ``omi_review_apply.py``."""
    modules = _modules()
    assert len(modules) > 100 and {SVC / "routers" / "calendar.py", SVC / "mcp_server.py", SVC / "calendar_ical.py"} <= set(modules)
    seen = 0
    for path in modules:
        rel = path.relative_to(SVC).as_posix()
        if rel == "calendar_service.py":
            continue
        src = path.read_text("utf-8")
        named = [door_fn for door_fn in DOOR_FNS if door_fn in src]      # (an alias chain starts at the name itself: no name, no caller)
        if not named:
            continue
        assert callers_that_leave_something_out(src) == [], rel
        for door_fn in named:
            assert _scan(src, door_fn) == [], (rel, door_fn)
            seen += src.count(door_fn + "(")
    assert seen >= 13                                          # the scan saw the callers it is about
    planted = ("import calendar_service as cal\nfrom calendar_service import create_event_record as make\nbuild = make\n"
               "async def bare(db):\n    stamps_at = stamps_now()\n    await cal.create_event_record(db, user_id='u', stamps_at=stamps_at)\n"
               "async def aliased(db):\n    stamps_at = stamps_now()\n    await build(db, user_id='u', stamps_at=stamps_at)\n"
               "async def blind(db):\n    stamps_at = stamps_now()\n    await cal.delete_event_record(db, event_id='e', actor='u', stamps_at=stamps_at)\n"
               "async def late(db):\n    await db.fetch('x')\n    stamps_at = stamps_now()\n"
               "    await cal.update_event_record(db, event_id='e', actor='u', owner='o', visibility='v', stamps_at=stamps_at)\n"
               "async def good(db):\n    stamps_at = stamps_now()\n    await db.fetch('x')\n"
               "    await cal.update_event_record(db, event_id='e', actor='u', owner='o', visibility='v', stamps_at=stamps_at)\n")
    assert callers_that_leave_something_out(planted) == [("aliased", "create_event_record", "actor"), ("bare", "create_event_record", "actor"),
                                                         ("blind", "delete_event_record", "owner"), ("blind", "delete_event_record", "visibility")]
    assert _scan(planted, "update_event_record") == ["late"] and _scan(planted, "create_event_record") == []


def test_every_door_function_asks_before_it_writes_and_nothing_else_in_the_door_asks():
    """In ``calendar_service.py``: each door function calls ``_gate`` BEFORE its statement, and the parts of an event
    (``replace_attendees`` / ``replace_alarms``: written under the event's own ask) and every reader hold no ask at all.
    Control (planted below): a writer that writes first, and one that never asks. Control run on the real tree: with the ask
    dropped from ``spoken_create_event_record`` this fails."""
    def shape(src: str) -> dict:
        out = {}
        for fn in [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            gate = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_gate"]
            texts = [(n.lineno, "".join(p.value if isinstance(p, ast.Constant) and isinstance(p.value, str) else "x" for p in n.values))
                     for n in ast.walk(fn) if isinstance(n, ast.JoinedStr)]
            texts += [(n.lineno, n.value) for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            writes = [ln for ln, text in texts if text.lstrip().upper().startswith(("INSERT INTO", "UPDATE ", "DELETE FROM"))]
            out[fn.name] = ("asks_first" if gate and writes and max(gate) < min(writes) else "asks_late" if gate else
                            "writes_unasked" if writes else "-")
        return out

    got = shape((SVC / "calendar_service.py").read_text("utf-8"))
    assert {name: got[name] for name in DOOR_FNS} == dict.fromkeys(DOOR_FNS, "asks_first")
    assert {name for name, kind in got.items() if kind == "writes_unasked"} == {"replace_attendees", "replace_alarms"}
    assert not [name for name, kind in got.items() if kind == "asks_late"]
    planted = shape("async def a(db):\n    await db.execute('UPDATE events SET x = 1')\n    await _gate('v')\n"
                    "async def b(db):\n    await db.execute(f'DELETE FROM events WHERE id = {1}')\n"
                    "async def c(db):\n    await _gate('v')\n    await db.execute('INSERT INTO events (id) VALUES (1)')\n")
    assert planted == {"a": "asks_late", "b": "writes_unasked", "c": "asks_first"}
