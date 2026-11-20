"""Notes and the journal go to the Zoe OS kernel, a flag each (``ZOE_OS_NOTES=kernel``, ``ZOE_OS_JOURNAL=kernel``;
docs/knowledge/zoe-os-notes-journal-door.md).

* any other flag value: NO kernel call, no socket, no thread, no extra statement: every one of the 30 entry-point scenarios
  gives the record captured from ``main`` before the doors existed; and each flag turns on its own area only;
* adding, changing and deleting a note or an entry asks first: allowed = made and reported; refused = ``NoteRefused`` /
  ``JournalRefused``, nothing written, each caller says so in its own way; kernel down or slow = made, one ``not_checked`` line;
* only ids, stamps and closed codes cross the socket and reach the log: never a title, a text, a mood or a tag;
* an accepted suggestion is reported only once its transaction has ended;
* the erase stamps an ask carries are the ones its caller took before it read the row;
* structure: every caller hands the door stamps taken before anything it awaits and (the screen) says who is acting and whose
  the row is; every door function asks before it writes.

A fake kernel on a real Unix socket (``test_kernel_door.AreaKernel``); the two tables on a SQLite file
(``notes_journal_scenarios``). Each test names the control that was run against it. Invented ids and words only.
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

import journal_service
import kernel_door
import note_service
import notes_journal_scenarios as scenarios
import os_shadow
import user_erase_gate
from notes_journal_scenarios import ANN, BOB, WORD
from test_kernel_door import AreaKernel
from test_os_roster_sync import _door_names, _scan
from test_os_shadow import SVC

pytestmark = pytest.mark.ci_safe

ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "notes_journal_as_on_main.json").read_text("utf-8"))
FLAGS = ("ZOE_OS_NOTES", "ZOE_OS_JOURNAL")
OTHER_FLAGS = ("ZOE_OS_LISTS", "ZOE_OS_REMINDERS", "ZOE_OS_ALARMS", "ZOE_OS_CALENDAR")
TEXT = "bring the womble"


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ANN, BOB]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    for flag in FLAGS:
        monkeypatch.setenv(flag, "kernel")
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


def _run(w, name):
    return scenarios.scenarios()[name](w)


# ---- flag off: main's behaviour, exactly ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "off", "on", "KERNELS"])
def test_any_other_flag_value_is_main_exactly_for_every_entry_point(door, monkeypatch, value):
    """All 30 scenarios equal the record captured from main, with every way into the kernel wired to fail the test. Control
    run: with the flag check removed from ``kernel_door.gate`` (always ask) the ``never`` stub raises and the record differs."""
    def never(*a, **k):
        raise AssertionError("the kernel was reached with the flag off")

    for flag in FLAGS:
        monkeypatch.delenv(flag) if value is None else monkeypatch.setenv(flag, value)
    for name in ("ask_door", "record_door", "report_door", "door_unchecked", "stamp_snapshot"):
        monkeypatch.setattr(os_shadow, name, never)
    assert scenarios.run_all() == ON_MAIN
    assert door.calls == [] and door.connections == 0 and os_shadow._door_worker is None      # no call, no socket, no thread


def test_with_both_doors_on_and_allowing_every_entry_point_still_issues_mains_statements(door):
    """The doors add no statement of their own (every caller hands over the row it already read). Control run: with
    ``delete_note_record`` reading the row itself before it asks, ``note_rest_delete`` gains a statement and this fails."""
    assert scenarios.run_all() == ON_MAIN
    asks = door.ops("door_ask")
    assert {a["verb"] for a in asks} == {*os_shadow.NOTE_DOOR_VERBS, *os_shadow.JOURNAL_DOOR_VERBS}
    assert {a["area"] for a in asks} == {"notes", "journal"}


@pytest.mark.parametrize("on, asked", [("ZOE_OS_NOTES", "notes"), ("ZOE_OS_JOURNAL", "journal")])
@pytest.mark.asyncio
async def test_each_flag_turns_on_its_own_area_only(door, monkeypatch, on, asked):
    """Control run: with both areas given the notes flag, a journal create asks with only ``ZOE_OS_NOTES`` on."""
    for flag in FLAGS:
        monkeypatch.setenv(flag, "kernel" if flag == on else "off")
    w = scenarios.World([])
    await _run(w, "note_rest_create")
    await _run(w, "journal_rest_create")
    assert [a["area"] for a in _wait(door, "door_ask", 1)] == [asked]


# ---- asked first -------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_change_asks_with_ids_only_and_is_reported_after_the_write(door, caplog):
    """Control run: with ``_gate`` dropped from ``update_note_record`` there is no ``note.edit`` ask and this fails."""
    w = scenarios.World([])
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        for name in ("note_rest_create_for_the_house", "note_rest_edit", "note_rest_delete", "journal_rest_create", "journal_rest_edit",
                     "journal_rest_delete"):
            await _run(w, name)
    asks = _wait(door, "door_ask", 6)
    assert [(x["area"], x["verb"]) for x in asks] == [("notes", "note.create"), ("notes", "note.edit"), ("notes", "note.delete"),
                                                      ("journal", "journal.create"), ("journal", "journal.edit"), ("journal", "journal.delete")]
    for x in asks:
        assert set(x) == {"area", "verb", "person", "target", "owner", "visibility", "stamps"}
        assert (x["person"], x["owner"], x["stamps"]) == (ANN, ANN, {ANN: os_shadow.erase_stamp(ANN)})
    assert [x["visibility"] for x in asks] == ["household", "private", "private", "private", "private", "private"]
    assert [x["target"] for x in asks[1:3]] == ["note-ann", "note-ann"] and [x["target"] for x in asks[4:]] == ["entry-ann", "entry-ann"]
    assert [r["outcome"] for r in _wait(door, "door_report", 6)] == ["done"] * 6
    sent = json.dumps(door.calls)
    assert all(word not in sent for word in (WORD, "wombles", "calm", "home", "snark"))      # never a title, a text, a mood or a tag
    lines = _lines(caplog)
    assert len(lines) == 6 and all("decision=allow reason=-" in line for line in lines)
    assert all(word not in "".join(lines) for word in (WORD, ANN, "note-ann"))


@pytest.mark.parametrize("reason", ["not_owner", "adults_only", "unknown_person"])
@pytest.mark.asyncio
async def test_a_kernel_refusal_is_final_nothing_is_written_and_each_caller_says_so(door, reason):
    """The screen answers 403 through the one app handler; the brain's tools return an error result; a spoken turn says the
    sentence; an accepted suggestion raises (its caller keeps it pending). Control run: with the refusal not raised in
    ``kernel_door.gate`` (the decision ignored) the note is created and this fails."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import get_current_user
    from database import get_db

    w = scenarios.World([])
    before = w.rows()
    door.door = f"refuse:{reason}"
    note_line, journal_line = note_service.NoteRefused(reason).line(), journal_service.JournalRefused(reason).line()
    assert all(word not in note_line + journal_line for word in (WORD, ANN))
    for name, kind in (("note_rest_create", note_service.NoteRefused), ("note_rest_edit", note_service.NoteRefused),
                       ("note_rest_patch", note_service.NoteRefused), ("note_rest_delete", note_service.NoteRefused),
                       ("note_suggestion_accepted", note_service.NoteRefused), ("journal_rest_create", journal_service.JournalRefused),
                       ("journal_rest_edit", journal_service.JournalRefused), ("journal_rest_delete", journal_service.JournalRefused)):
        with pytest.raises(kind) as exc:
            await _run(w, name)
        assert exc.value.reason == reason and isinstance(exc.value, kernel_door.Refused), name
    assert await _run(w, "note_tool_create") == {"error": note_line + " Nothing was saved.", "status": "refused"}
    assert await _run(w, "note_tool_update") == {"error": note_line + " Nothing was changed.", "status": "refused"}
    assert await _run(w, "note_tool_delete") == {"error": note_line + " Nothing was deleted.", "status": "refused"}
    assert await _run(w, "journal_tool_create") == {"error": journal_line + " Nothing was saved.", "status": "refused"}
    assert await _run(w, "note_spoken_create") == note_line and await _run(w, "journal_spoken_create") == journal_line
    assert w.rows() == before and door.ops("door_report") == []
    assert not [e for e in w.log if e[0] in ("receipt", "broadcast", "notify_ui", "notify_ui_channel", "memory")]      # nothing announced or mirrored

    app = FastAPI()
    app.include_router(w.notes.router)
    app.include_router(w.journal.router)
    app.add_exception_handler(kernel_door.Refused, kernel_door.refused_response)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": ANN, "role": "user"}
    app.dependency_overrides[get_db] = lambda: w.db
    with TestClient(app) as client:
        out = client.post("/api/notes/", json={"content": TEXT})
        assert (out.status_code, out.json()) == (403, {"detail": note_line})
        out = client.post("/api/journal/entries", json={"content": TEXT})
        assert (out.status_code, out.json()) == (403, {"detail": journal_line})
    assert w.rows() == before


@pytest.mark.parametrize("how,reason", [("close", "unavailable"), ("old_kernel", "protocol"), ("slow", "timeout")])
@pytest.mark.asyncio
async def test_a_kernel_that_cannot_answer_lets_a_change_proceed_not_checked(door, caplog, how, reason):
    """Control run: with ``not_checked`` treated as a refusal in ``kernel_door.gate`` all three raise."""
    if how == "slow":
        door.delay = 0.5
    else:
        door.door = how
    w = scenarios.World([])
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        await _run(w, "note_rest_delete")
        await _run(w, "journal_spoken_create")
    rows = w.rows()
    assert rows["notes"][0]["deleted"] == 1 and len(rows["journal_entries"]) == 3
    assert f"area=notes verb=note.delete decision=not_checked reason={reason}" in _lines(caplog)[0]
    assert "area=journal verb=journal.create decision=not_checked" in _lines(caplog)[1]


@pytest.mark.asyncio
async def test_a_session_with_nobody_identified_asks_as_the_panel_and_a_writer_told_nobody_is_not_checked(door, caplog):
    """A guest id is not a person: the ask says ``via=panel`` and never the id. A caller that names no actor cannot be judged:
    the write proceeds, logged ``no_actor`` (the structural test keeps every real caller from being one). Control run: with
    the panel mapping dropped from ``kernel_door.gate`` the guest id is sent as the person."""
    w = scenarios.World([])
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        await note_service.create_note_record(w.db, note_id="note-new", user_id="guest", title=None, content=TEXT, category="general",
                                              tags_json=None, visibility="personal", actor="guest", stamps_at=note_service.stamps_now())
        await journal_service.delete_entry_record(w.db, entry_id="entry-ann", stamps_at=journal_service.stamps_now())
    (ask,) = _wait(door, "door_ask", 1)
    assert (ask.get("via"), ask["person"]) == ("panel", "panel") and "owner" not in ask and "guest" not in json.dumps(door.calls)
    assert "area=journal verb=journal.delete decision=not_checked reason=no_actor" in _lines(caplog)[-1]


# ---- how a write ended ----------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_write_that_raises_is_reported_failed(door):
    """Control run: with ``kernel_door.write`` not reporting a failure this sees no report."""
    class Broken:
        def execute(self, sql, params=()):
            async def fail():
                raise RuntimeError("store down")
            return fail()

    for call in (lambda: note_service.delete_note_record(Broken(), note_id="note-ann", actor=ANN, owner=ANN, visibility="personal",
                                                         stamps_at=note_service.stamps_now()),
                 lambda: journal_service.delete_entry_record(Broken(), entry_id="entry-ann", actor=ANN, owner=ANN,
                                                             stamps_at=journal_service.stamps_now())):
        with pytest.raises(RuntimeError):
            await call()
    assert [r["outcome"] for r in _wait(door, "door_report", 2)] == ["failed", "failed"]


@pytest.mark.parametrize("ends", ["commits", "rolls_back"])
@pytest.mark.asyncio
async def test_an_accepted_note_suggestion_is_reported_only_once_its_transaction_has_ended(door, ends):
    """Control run: with ``kernel_door.wrote`` ignoring the held block (reporting at once), the report is seen before the block ends."""
    w = scenarios.World([])
    try:
        async with kernel_door.reports_after():
            assert (await _run(w, "note_suggestion_accepted"))["note_id"] and len(_wait(door, "door_ask", 1)) == 1
            await asyncio.sleep(0.05)
            assert door.ops("door_report") == []
            if ends == "rolls_back":
                raise RuntimeError("the transaction failed")
    except RuntimeError:
        pass
    assert [r["outcome"] for r in _wait(door, "door_report", 1)] == ["done" if ends == "commits" else "failed"]


# ---- ordered against a forget ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_ask_carries_the_stamps_its_caller_took_before_it_read_the_row(door):
    """Control run: with ``kernel_door.gate`` taking a fresh snapshot instead of the caller's, the ask carries the moved-on stamp."""
    w = scenarios.World([])
    stamps_at = note_service.stamps_now()                     # taken BEFORE ...
    before = os_shadow.erase_stamp(BOB)
    os_shadow._forget_marks[BOB] += 1                          # ... the kernel confirms a whole-member forget of BOB
    await note_service.update_note_record(w.db, note_id="note-bob", updates=["updated_at = NOW()"], params=["note-bob"], actor=ANN,
                                          owner=BOB, visibility="family", stamps_at=stamps_at)
    (edit,) = _wait(door, "door_ask", 1)
    assert edit["owner"] == BOB and edit["stamps"][BOB] == before and os_shadow.erase_stamp(BOB) > before


# ---- structure --------------------------------------------------------------------------------------------------------------------
#: door function -> what its caller must say besides ``stamps_at`` (the screen's are told who is acting, and whose a stored row is)
NOTE_FNS = {"create_note_record": {"actor"}, "update_note_record": {"actor", "owner", "visibility"},
            "delete_note_record": {"actor", "owner", "visibility"}, "said_note_record": set(), "tool_update_note_record": set(),
            "tool_delete_note_record": set(), "suggested_note_record": set()}
JOURNAL_FNS = {"create_entry_record": {"actor"}, "update_entry_record": {"actor", "owner"}, "delete_entry_record": {"actor", "owner"},
               "tool_entry_record": set(), "said_entry_record": set()}
DOOR_FNS = {**NOTE_FNS, **JOURNAL_FNS}


def _modules() -> list:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return sorted(p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts))


def callers_that_leave_something_out(src: str) -> list:
    """``(function, door function, what is missing)`` for every call of a door function, by any alias (followed to a fixed point)."""
    tree = ast.parse(src)
    by_name = {name: door_fn for door_fn in DOOR_FNS for name in _door_names(tree, door_fn)}
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            door_fn = by_name.get(getattr(call.func, "id", getattr(call.func, "attr", "")))
            if door_fn is not None:
                out += [(fn.name, door_fn, missing) for missing in sorted(DOOR_FNS[door_fn] - {k.arg for k in call.keywords})]
    return sorted(set(out))


def test_every_caller_says_who_is_acting_and_takes_its_stamps_before_anything_it_awaits():
    """Over EVERY module of the service, for every door function of both doors. Controls (planted below): a caller with no
    ``actor=``, one behind two aliases, one that stamps after an await. Control run on the real tree: with
    ``stamps_at=stamps_at`` dropped from the spoken journal entry this fails naming ``intent_router.py``; with ``owner=``
    dropped from the screen's note delete it fails naming ``routers/notes.py``."""
    modules = _modules()
    assert len(modules) > 100 and {SVC / "routers" / "notes.py", SVC / "routers" / "journal.py", SVC / "mcp_server.py"} <= set(modules)
    seen = 0
    for path in modules:
        rel = path.relative_to(SVC).as_posix()
        if rel in ("note_service.py", "journal_service.py"):
            continue
        src = path.read_text("utf-8")
        named = [door_fn for door_fn in DOOR_FNS if door_fn in src]      # (an alias chain starts at the name itself: no name, no caller)
        if not named:
            continue
        assert callers_that_leave_something_out(src) == [], rel
        for door_fn in named:
            assert _scan(src, door_fn) == [], (rel, door_fn)
            seen += src.count(door_fn + "(")
    assert seen >= 15                                          # the scan saw the callers it is about
    planted = ("import note_service\nfrom journal_service import create_entry_record as make\nbuild = make\n"
               "async def bare(db):\n    stamps_at = stamps_now()\n    await note_service.create_note_record(db, note_id='n', stamps_at=stamps_at)\n"
               "async def aliased(db):\n    stamps_at = stamps_now()\n    await build(db, entry_id='e', stamps_at=stamps_at)\n"
               "async def blind(db):\n    stamps_at = stamps_now()\n    await note_service.delete_note_record(db, note_id='n', actor='u', stamps_at=stamps_at)\n"
               "async def late(db):\n    await db.fetch('x')\n    stamps_at = stamps_now()\n"
               "    await note_service.said_note_record(db, note_id='n', stamps_at=stamps_at)\n"
               "async def good(db):\n    stamps_at = stamps_now()\n    await db.fetch('x')\n"
               "    await note_service.said_note_record(db, note_id='n', stamps_at=stamps_at)\n")
    assert callers_that_leave_something_out(planted) == [("aliased", "create_entry_record", "actor"), ("bare", "create_note_record", "actor"),
                                                         ("blind", "delete_note_record", "owner"), ("blind", "delete_note_record", "visibility")]
    assert _scan(planted, "said_note_record") == ["late"] and _scan(planted, "create_note_record") == []


def test_every_door_function_asks_before_it_writes():
    """In ``note_service.py`` and ``journal_service.py``: each door function calls ``_gate`` BEFORE its statement, and nothing
    else in either module writes. Control (planted below): a writer that writes first, and one that never asks. Control run on
    the real tree: with the ask dropped from ``said_entry_record`` this fails."""
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

    for module, fns in (("note_service.py", NOTE_FNS), ("journal_service.py", JOURNAL_FNS)):
        got = shape((SVC / module).read_text("utf-8"))
        assert {name: got[name] for name in fns} == dict.fromkeys(fns, "asks_first"), module
        assert not [name for name, kind in got.items() if kind in ("asks_late", "writes_unasked")], module
    planted = shape("async def a(db):\n    await db.execute('UPDATE notes SET x = 1')\n    await _gate('v')\n"
                    "async def b(db):\n    await db.execute(f'DELETE FROM notes WHERE id = {1}')\n"
                    "async def c(db):\n    await _gate('v')\n    await db.execute('INSERT INTO notes (id) VALUES (1)')\n")
    assert planted == {"a": "asks_late", "b": "writes_unasked", "c": "asks_first"}
