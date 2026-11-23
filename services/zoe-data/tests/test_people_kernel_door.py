"""Contacts go to the Zoe OS kernel (``ZOE_OS_PEOPLE=kernel``; docs/knowledge/zoe-os-people-door.md).

* any other flag value: NO kernel call, no socket, no thread, no extra statement: every one of the 38 entry-point scenarios
  gives the record captured from ``main`` before the door existed;
* adding, changing and deleting a contact, and changing its relationships, asks first: allowed = made and reported; refused =
  ``ContactRefused``, nothing written, each caller says so in its own way; kernel down or slow = made, ``not_checked``;
* a merge changes two contacts: both are asked first and neither is touched if either is refused;
* only ids, stamps and closed codes cross the socket and reach the log: never a name, a number, a birthday or a note;
* the automatic makers and the erasers are not asked; a member's action that reaches the shared mechanics is;
* structure: every caller of the door either says the statement is part of a change already asked, or says who is acting and
  hands over stamps taken before anything it awaits.

A fake kernel on a real Unix socket (``test_kernel_door.AreaKernel``); the two tables on a SQLite file (``people_scenarios``).
Each test names the control that was run against it. Invented ids and words only.
"""
from __future__ import annotations

import ast
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

import pytest

import kernel_door
import os_shadow
import people_scenarios as scenarios
import people_service
import user_erase_gate
from people_scenarios import ANN, BOB, WORD
from test_kernel_door import AreaKernel
from test_os_roster_sync import _door_names, _takes_first
from test_os_shadow import SVC

pytestmark = pytest.mark.ci_safe

ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "people_as_on_main.json").read_text("utf-8"))
OTHER_FLAGS = ("ZOE_OS_LISTS", "ZOE_OS_REMINDERS", "ZOE_OS_ALARMS", "ZOE_OS_CALENDAR", "ZOE_OS_NOTES", "ZOE_OS_JOURNAL")
#: the door's own read of whose a stored contact is (flag on only; ``people_service.ask_about``)
LOOKUP = "SELECT user_id, visibility FROM people WHERE id = ? AND deleted = 0 AND (visibility = 'family' OR user_id = ?)"


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ANN, BOB]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_PEOPLE", "kernel")
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


def _people(w) -> dict:
    return {r["id"]: r for r in w.rows()["people"]}


# ---- flag off: main's behaviour, exactly ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "off", "on", "KERNELS"])
def test_any_other_flag_value_is_main_exactly_for_every_entry_point(door, monkeypatch, value):
    """All 38 scenarios equal the record captured from main, with every way into the kernel wired to fail the test. Control
    run: with the flag check removed from ``people_service.ask_about`` (always look up) the screen's delete gains a statement
    and the record differs."""
    def never(*a, **k):
        raise AssertionError("the kernel was reached with the flag off")

    monkeypatch.delenv("ZOE_OS_PEOPLE") if value is None else monkeypatch.setenv("ZOE_OS_PEOPLE", value)
    for name in ("ask_door", "record_door", "report_door", "door_unchecked", "stamp_snapshot"):
        monkeypatch.setattr(os_shadow, name, never)
    assert scenarios.run_all() == ON_MAIN
    # no ask, no report, no door thread (the one thing the kernel still hears is a FORGET, which is relayed whatever any
    # area's flag says: the day review's undo scenario erases a memory row)
    assert door.ops("door_ask") == [] and door.ops("door_report") == [] and os_shadow._door_worker is None
    assert {op for _, op, _ in door.calls} <= {"forget"}


def test_with_the_door_on_and_allowing_only_the_doors_own_lookup_is_added(door):
    """With the door on and the kernel allowing, every entry point issues main's statements and gives main's results. The one
    addition is the door's own read of whose a stored contact is, on the screen's routes that hold only an id (flag on only).
    Control run: with ``write_contact`` issuing a second read of its own, the filtered record differs."""
    here = scenarios.run_all()
    seen = 0
    for name, log in here.items():
        kept = [e for e in log if not (e[0] == "sql" and e[1] == LOOKUP)]
        seen += len(log) - len(kept)
        assert kept == ON_MAIN[name], name
    assert seen >= 6                                           # update, delete, the relationship route: asked about by id
    asks = door.ops("door_ask")
    assert {a["verb"] for a in asks} == set(os_shadow.PEOPLE_DOOR_VERBS) and {a["area"] for a in asks} == {"people"}


# ---- asked first -------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_change_asks_with_ids_only_and_is_reported_after_the_write(door, caplog):
    """Control run: with ``ask_about`` dropped from the screen's delete wrapper there is no ``contact.delete`` ask and this fails."""
    w = scenarios.World([])
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        made = await _run(w, "screen_create")
        await _run(w, "screen_update_own")
        await _run(w, "screen_delete_own")
    asks = _wait(door, "door_ask", 3)
    assert [x["verb"] for x in asks] == ["contact.create", "contact.edit", "contact.delete"]
    for x in asks:
        assert set(x) == {"area", "verb", "person", "target", "owner", "visibility", "stamps"}
        assert (x["area"], x["person"], x["owner"], x["stamps"]) == ("people", ANN, ANN, {ANN: os_shadow.erase_stamp(ANN)})
    assert [(x["target"], x["visibility"]) for x in asks] == [(made["id"], "household"), ("p-ann-private", "private"),
                                                               ("p-ann-private", "household")]     # (the update shared it)
    assert [r["outcome"] for r in _wait(door, "door_report", 3)] == ["done"] * 3
    sent = json.dumps(door.calls)
    assert all(word not in sent for word in (WORD, "wombles", "1990", "friend", "Quango"))        # never a name, a note or a birthday
    lines = _lines(caplog)
    assert len(lines) == 3 and all("area=people" in line and "decision=allow reason=-" in line for line in lines)
    assert all(word not in "".join(lines) for word in (WORD, ANN, "p-ann-private"))


@pytest.mark.asyncio
async def test_another_members_household_contact_names_its_owner_and_a_private_one_is_never_asked_about(door, caplog):
    """The ask is about the contact AS STORED: a household contact in ANN's book changed by BOB says owner ANN. A contact the
    caller cannot see is not asked about: the route answers "not found" as it always did, and the kernel hears nothing.
    Control run: with the visibility scope dropped from the door's lookup, BOB's try at ANN's private contact is asked."""
    w = scenarios.World([])
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        await _run(w, "screen_update_a_household_contact_by_another_member")
        with pytest.raises(Exception) as exc:
            await _run(w, "screen_update_a_private_contact_by_another_member")
    assert getattr(exc.value, "status_code", None) == 404
    (ask,) = _wait(door, "door_ask", 1)
    assert (ask["person"], ask["owner"], ask["visibility"], ask["target"], set(ask["stamps"])) == (BOB, ANN, "household", "p-ann-family", {ANN, BOB})
    assert "area=people verb=contact.edit decision=not_checked reason=no_row" in _lines(caplog)[-1]


@pytest.mark.parametrize("reason", ["not_owner", "adults_only", "unknown_person"])
@pytest.mark.asyncio
async def test_a_kernel_refusal_is_final_nothing_is_written_and_each_caller_says_so(door, reason):
    """The screen answers 403 through the one app handler; the brain's tools return an error result; a spoken turn says the
    sentence; the panel's introduce files nothing; an accepted offer, a correction and the day review raise (their callers keep
    the thing pending or say it failed). Control run: with the refusal not raised in ``kernel_door.gate`` the contact is
    created and this fails; with Skybridge's flag-on path put back to the old order (the fact saved, then the edit asked at its
    write) this fails on ``memory_fact``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth import get_current_user
    from database import get_db

    w = scenarios.World([])
    before = w.rows()
    door.door = f"refuse:{reason}"
    line = people_service.ContactRefused(reason).line()
    assert all(word not in line for word in (WORD, ANN))
    for name in ("screen_create", "screen_update_own", "screen_delete_own", "screen_update_a_household_contact_by_another_member",
                 "screen_relation_with_a_new_stub", "said_rename", "spoken_fact_about_a_new_person", "spoken_fact_about_their_own_contact",
                 "offer_accepted", "offer_accepted_for_a_stub", "offer_accepted_for_a_stub_with_a_relation", "corrected_to_a_pet",
                 "reviewed_relation"):
        with pytest.raises(people_service.ContactRefused) as exc:
            await _run(w, name)
        assert exc.value.reason == reason and isinstance(exc.value, kernel_door.Refused), name
    assert await _run(w, "tool_create") == {"error": line + " Nothing was saved.", "status": "refused"}
    assert await _run(w, "tool_update") == {"error": line + " Nothing was changed.", "status": "refused"}
    assert await _run(w, "tool_delete") == {"error": line + " Nothing was deleted.", "status": "refused"}
    assert await _run(w, "said_add") == line
    assert await _run(w, "introduced_a_new_person") == ""                    # no contact filed, no card to open
    assert await _run(w, "corrected_name") == "failed"                       # the correction says the contact did not change
    intent = w.sky.SkybridgeIntent("people", "remember_fact", person_name="Quango Zorb", fact_text="likes wombles")
    out = await w.sky._resolve_with_db(intent, ANN, w.pg)
    assert out["spoken_summary"] == line and out["actions"] == [] and out["intent"]["status"] == "refused"
    assert w.rows() == before and door.ops("door_report") == []
    # (the screen's memory mirror is recorded as ``memory``, Skybridge's spoken fact as ``memory_fact``: review of #2700, the
    # spoken fact was saved before the edit was asked and this list did not name it)
    assert not [e for e in w.log if e[0] in ("broadcast", "notify_ui", "notify_ui_channel", "memory", "memory_fact", "mirror",
                                             "health", "archive")]

    app = FastAPI()
    app.include_router(w.people.router)
    app.add_exception_handler(kernel_door.Refused, kernel_door.refused_response)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": ANN, "role": "user"}
    app.dependency_overrides[get_db] = lambda: w.db
    with TestClient(app) as client:
        out = client.post("/api/people/", json={"name": WORD})
        assert (out.status_code, out.json()) == (403, {"detail": line})
        out = client.delete("/api/people/p-ann-private")
        assert (out.status_code, out.json()) == (403, {"detail": line})
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
        await _run(w, "screen_delete_own")
        await _run(w, "tool_create")
    rows = _people(w)
    assert rows["p-ann-private"]["deleted"] == 1 and len(rows) == 6
    assert f"area=people verb=contact.delete decision=not_checked reason={reason}" in _lines(caplog)[0]


# ---- a merge is a change to BOTH contacts ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("refused", [None, "contact.edit", "contact.delete"])
@pytest.mark.asyncio
async def test_a_merge_asks_about_both_contacts_and_touches_neither_if_either_is_refused(door, monkeypatch, refused):
    """Kept contact = ``contact.edit``; folded contact = ``contact.delete``. Both are asked BEFORE the merge runs. With the
    second refused, the first (already allowed) is closed ``failed`` and the merge never starts. Control run: with the merge
    started before the second ask, ``ran`` is not empty when the delete is refused."""
    w, ran = scenarios.World([]), []
    real = people_service.ask_contact

    async def ask(verb, **kw):
        if verb == refused:
            raise people_service.ContactRefused("not_owner")
        return await real(verb, **kw)

    async def merge(source_id, target_id, user, db):
        ran.append((source_id, target_id))
        return {"ok": True}

    monkeypatch.setattr(people_service, "ask_contact", ask)
    monkeypatch.setattr(w.people, "_merge_person_endpoint", merge)
    monkeypatch.setenv("ZOE_PERSON_MERGE_ENABLED", "1")          # merge disabled is a 404 before any ask (the next test)
    call = w.people.merge_person_endpoint("p-ann-stub", "p-ann-private", user={"user_id": ANN, "role": "user"}, db=w.db)
    if refused:
        with pytest.raises(people_service.ContactRefused):
            await call
        assert ran == []
        want = [] if refused == "contact.edit" else ["failed"]
        assert [r["outcome"] for r in _wait(door, "door_report", len(want), timeout=1.0)] == want
    else:
        assert await call == {"ok": True} and ran == [("p-ann-stub", "p-ann-private")]
        asks = _wait(door, "door_ask", 2)
        assert [(a["verb"], a["target"]) for a in asks] == [("contact.edit", "p-ann-private"), ("contact.delete", "p-ann-stub")]
        assert [r["outcome"] for r in _wait(door, "door_report", 2)] == ["done", "done"]


@pytest.mark.parametrize("people_flag", ["kernel", None])
@pytest.mark.asyncio
async def test_a_merge_while_merge_is_disabled_is_404_and_asks_nothing(door, monkeypatch, people_flag):
    """``ZOE_PERSON_MERGE_ENABLED`` off: the 404 it always was, flag on or off, even with a kernel that refuses everything; the
    kernel is never asked (review of #2700: the wrapper asked first, so a refusal turned the disabled merge into a 403). Control
    run: with the merge check removed from the wrapper ``merge_person_endpoint`` (so the inner one runs after the two asks) the
    flag-on case raises ``ContactRefused`` and this fails."""
    from fastapi import HTTPException

    monkeypatch.delenv("ZOE_PERSON_MERGE_ENABLED", raising=False)
    if people_flag is None:
        monkeypatch.delenv("ZOE_OS_PEOPLE", raising=False)
    door.door = "refuse:not_owner"
    w = scenarios.World([])
    with pytest.raises(HTTPException) as exc:
        await w.people.merge_person_endpoint("p-ann-stub", "p-ann-private", user={"user_id": ANN, "role": "user"}, db=w.db)
    assert (exc.value.status_code, exc.value.detail) == (404, "Person merge is disabled")
    assert door.ops("door_ask") == [] and not [e for e in w.log if e[0] == "sql"]


# ---- a spoken fact: every ask before anything is written ---------------------------------------------------------------------------
@pytest.mark.parametrize("refused", [None, "contact.create", "contact.edit"])
@pytest.mark.parametrize("scenario", ["spoken_fact_about_a_new_person", "spoken_fact_about_their_own_contact"])
@pytest.mark.asyncio
async def test_a_spoken_fact_asks_create_and_edit_before_it_saves_anything(door, monkeypatch, scenario, refused):
    """Skybridge's "remember that <name> ...": a new contact is a create AND an edit; both are asked before the contact is made,
    the fact is saved to memory or the card is changed. A refusal of either leaves nothing behind: no row, no memory fact, and
    an already-allowed create is closed ``failed`` (review of #2700: the fact was saved, and a new contact committed, before the
    edit was asked). Control run: with the up-front edit ask dropped (the edit asked at its own write, as before the review) the
    new-person edit-refused case fails: the contact was made and the fact saved."""
    w = scenarios.World([])
    before = w.rows()
    real = people_service.ask_contact

    async def ask(verb, **kw):
        if verb == refused:
            raise people_service.ContactRefused("not_owner")
        return await real(verb, **kw)

    monkeypatch.setattr(people_service, "ask_contact", ask)
    new = scenario == "spoken_fact_about_a_new_person"
    if refused == "contact.create" and not new:
        refused = None                                             # an existing contact is not created: nothing to refuse
    if refused:
        with pytest.raises(people_service.ContactRefused):
            await _run(w, scenario)
        assert w.rows() == before and not [e for e in w.log if e[0] in ("memory_fact", "show_people")]
        assert not [e for e in w.log if e[0] == "pg" and not e[1].startswith("SELECT")]
        want = ["failed"] if refused == "contact.edit" and new else []
        assert [r["outcome"] for r in _wait(door, "door_report", len(want), timeout=1.0)] == want
        return
    await _run(w, scenario)
    asks = _wait(door, "door_ask", 2 if new else 1)
    assert [a["verb"] for a in asks] == (["contact.create", "contact.edit"] if new else ["contact.edit"])
    assert len({a["target"] for a in asks}) == 1
    assert [r["outcome"] for r in _wait(door, "door_report", len(asks))] == ["done"] * len(asks)
    assert [e[0] for e in w.log if e[0] in ("memory_fact", "show_people")] == ["memory_fact", "show_people"]


# ---- who is not asked ----------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_shared_mechanics_ask_for_a_members_action_and_never_for_automatic_work_or_an_eraser(door, caplog):
    """``contacts_store`` serves a member's action (the day review's approval, a voice tag, a settled review: ``actor`` named,
    asked first) and automatic work (a card import, voice learning: nobody named, never asked, and nothing logged). The undo of
    an approved relationship is a forget's work: not asked. Control run: with ``actor=actor`` dropped from ``ensure_person``'s
    call of ``_create_person`` the member's create is not asked and this fails."""
    import contacts_store

    w = scenarios.World([])
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        auto, _ = await contacts_store.ensure_person(w.db, ANN, "Tansy Wren", relationship="neighbour")           # automatic
        await _run(w, "reviewed_relation_undo")                                                                   # the eraser
        assert door.ops("door_ask") == [] and _lines(caplog) == []
        stamps_at = people_service.stamps_now()
        mine, created = await contacts_store.ensure_person(w.db, ANN, "Yarrow Finch", relationship="friend", actor=ANN, stamps_at=stamps_at)
        await contacts_store.ensure_person(w.db, ANN, "Plover", relationship="cousin", actor=ANN, stamps_at=stamps_at)
    asks = _wait(door, "door_ask", 2)
    assert created and [(a["verb"], a["target"], a["owner"], a["visibility"]) for a in asks] == [
        ("contact.create", mine, ANN, "private"), ("contact.edit", "p-ann-stub", ANN, "private")]
    assert "Yarrow" not in json.dumps(door.calls) and auto in _people(w)
    door.door = "refuse:unknown_person"
    before = _people(w)
    with pytest.raises(people_service.ContactRefused):
        await contacts_store.ensure_person(w.db, ANN, "Sorrel Pike", actor=ANN, stamps_at=people_service.stamps_now())
    assert _people(w) == before


# ---- ordered against a forget ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_ask_carries_the_stamps_its_caller_took_before_it_read_the_contact(door):
    """Control run: with ``kernel_door.gate`` taking a fresh snapshot instead of the caller's, the ask carries the moved-on stamp."""
    w = scenarios.World([])
    stamps_at = people_service.stamps_now()                    # taken BEFORE ...
    before = os_shadow.erase_stamp(BOB)
    os_shadow._forget_marks[BOB] += 1                          # ... the kernel confirms a whole-member forget of BOB
    await people_service.ask_about(w.db, "contact.edit", actor=ANN, person_id="p-bob-family", stamps_at=stamps_at)
    (edit,) = _wait(door, "door_ask", 1)
    assert edit["owner"] == BOB and edit["stamps"][BOB] == before and os_shadow.erase_stamp(BOB) > before


@pytest.mark.asyncio
async def test_a_fault_in_the_doors_own_lookup_raises_and_nothing_is_changed(door):
    """Control run: with the lookup's failure swallowed (the change proceeding unasked) the contact is deleted."""
    w = scenarios.World([])
    real = w.db.execute

    def execute(sql, params=()):
        if " ".join(sql.split()) == LOOKUP:
            raise RuntimeError("store down")
        return real(sql, params)

    w.db.execute = execute
    with pytest.raises(RuntimeError):
        await _run(w, "screen_delete_own")
    assert _people(w)["p-ann-private"]["deleted"] == 0 and door.ops("door_ask") == []


# ---- structure --------------------------------------------------------------------------------------------------------------------
DOOR_FNS = ("write_contact", "ask_contact", "ask_about")
#: the shared mechanics a member's action can reach, and the callers that are automatic (no ``actor``): by (path, function)
MECHANICS = ("_create_person", "ensure_person")
AUTOMATIC = {("contacts_store.py", "import_card"), ("named_guest_learning.py", "_name_cluster")}


def _modules() -> dict:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return {p.relative_to(SVC).as_posix(): p for p in sorted(SVC.rglob("*.py")) if not skip.intersection(p.relative_to(SVC).parts)}


def door_calls_that_say_too_little(src: str, fns=DOOR_FNS) -> list:
    """``(function, door function, what is wrong)`` for every call of a door function (by any alias, followed to a fixed
    point): it must say ``under=True``, or name the actor AND pass ``stamps_at=stamps_at`` taken first in that function."""
    tree = ast.parse(src)
    by_name = {name: fn for fn in fns for name in _door_names(tree, fn)}
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            door_fn = by_name.get(getattr(call.func, "id", getattr(call.func, "attr", "")))
            if door_fn is None:
                continue
            kw = {k.arg: k.value for k in call.keywords}
            if isinstance(kw.get("under"), ast.Constant) and kw["under"].value is True:
                continue
            if "actor" not in kw:
                out.append((fn.name, door_fn, "no actor"))
            if getattr(kw.get("stamps_at"), "id", "") != "stamps_at":
                out.append((fn.name, door_fn, "no stamps"))
            elif not _takes_first(fn):
                out.append((fn.name, door_fn, "stamps not taken first"))
    return sorted(set(out))


def test_every_caller_of_the_door_says_enough():
    """Over EVERY module of the service. Controls (planted below): a caller with no actor, one with no stamps, one that stamps
    after an await, one behind an alias. Control run on the real tree: with ``stamps_at=stamps_at`` dropped from the spoken
    rename this fails naming ``intent_router.py``; with ``under=True`` dropped from the screen's delete it fails naming
    ``routers/people.py``."""
    modules = _modules()
    assert len(modules) > 100
    seen = 0
    for rel, path in modules.items():
        if rel in ("people_service.py", "contacts_store.py"):      # the door; and the shared mechanics, which pass their caller's
            continue                                               # actor and stamps on (their callers: the next test)
        src = path.read_text("utf-8")
        if not any(fn in src for fn in DOOR_FNS):
            continue
        assert door_calls_that_say_too_little(src) == [], rel
        seen += sum(src.count(fn + "(") for fn in DOOR_FNS)
    assert seen >= 25
    planted = ("import people_service\nfrom people_service import write_contact as put\nsend = put\n"
               "async def bare(db):\n    stamps_at = stamps_now()\n    await people_service.write_contact(db, 'tool_create', (), stamps_at=stamps_at)\n"
               "async def blind(db):\n    await people_service.write_contact(db, 'tool_create', (), actor='u')\n"
               "async def late(db):\n    await db.fetch('x')\n    stamps_at = stamps_now()\n"
               "    await send(db, 'tool_create', (), actor='u', stamps_at=stamps_at)\n"
               "async def part(db):\n    await people_service.write_contact(db, 'screen_delete', (), under=True)\n"
               "async def good(db):\n    stamps_at = stamps_now()\n    await db.fetch('x')\n"
               "    await people_service.ask_about(db, 'contact.edit', actor='u', person_id='p', stamps_at=stamps_at)\n")
    assert door_calls_that_say_too_little(planted) == [("bare", "write_contact", "no actor"), ("blind", "write_contact", "no stamps"),
                                                       ("late", "write_contact", "stamps not taken first")]


def test_the_shared_mechanics_are_reached_with_an_actor_or_from_a_named_automatic_caller():
    """Every call of ``contacts_store._create_person`` / ``ensure_person`` either names the acting member (and hands over
    stamps), or sits in a function listed here as automatic. Control run: with ``actor=user_id`` dropped from the day review's
    ``write_person`` this fails naming ``omi_review_apply.py``."""
    offenders, automatic = [], set()
    for rel, path in _modules().items():
        src = path.read_text("utf-8")
        if not any(fn in src for fn in MECHANICS):
            continue
        tree = ast.parse(src)
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                         and getattr(n.func, "id", getattr(n.func, "attr", "")) in MECHANICS]:
                kw = {k.arg: k.value for k in call.keywords}
                if {"actor", "stamps_at"} <= set(kw):
                    passes_on = rel == "contacts_store.py" and fn.name in MECHANICS         # ensure_person -> _create_person
                    if getattr(kw["stamps_at"], "id", "") != "stamps_at" or not (passes_on or _takes_first(fn)):
                        offenders.append((rel, fn.name, "stamps not taken first"))
                    continue
                if (rel, fn.name) in AUTOMATIC:
                    automatic.add((rel, fn.name))
                else:
                    offenders.append((rel, fn.name))
    assert not offenders, offenders
    assert automatic == AUTOMATIC                              # each named exception is still there (none is stale)


def test_the_door_asks_before_it_writes():
    """In ``people_service.write_contact`` the ask comes before the statement is issued, and ``under`` is the only way past it.
    Control run: with the ask moved after ``kernel_door.write`` this fails."""
    src = (SVC / "people_service.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "write_contact")
    calls = [(n.lineno, getattr(n.func, "id", getattr(n.func, "attr", ""))) for n in ast.walk(fn) if isinstance(n, ast.Call)]
    asks = [ln for ln, name in calls if name == "ask_contact"]
    writes = [ln for ln, name in calls if name == "write" ]
    assert len(asks) == 1 and len(writes) == 1 and asks[0] < writes[0]
    assert set(people_service.SQL) | set(people_service._BUILT) >= people_service._CREATES | people_service._DELETES
    assert {people_service.verb_of(e) for e in (*people_service.SQL, *people_service._BUILT)} == set(os_shadow.PEOPLE_DOOR_VERBS)
