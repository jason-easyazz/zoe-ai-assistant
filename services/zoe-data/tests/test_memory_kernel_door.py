"""``ZOE_OS_MEMORY=kernel``: a MEMBER's change to a memory row goes to the Zoe OS kernel, inside the one writer
(``memory_service.MemoryService``; ``memory_door.py``). Default off.

  * flag off (any value but ``kernel``) = ``main`` exactly: every scenario of ``tests/memory_scenarios.py`` gives the record of
    main, and the kernel is never reached;
  * door on and the kernel allowing = the same record (the door changes nothing the store is made to do);
  * remember / correct / approve ask FIRST; a refusal is final (nothing written, ``MemoryRefused``); a kernel that is down, old
    or slow = the change proceeds, ``not_checked``;
  * a forget (reject, archive, erase) is MADE FIRST and told afterwards whatever the kernel does; a refusal is only the record
    saying objected, and the erase stands;
  * an AUTOMATIC writer is neither asked nor told;
  * only ids and closed codes cross: never the fact, its excerpt, a tag or a reason;
  * the acting member is who asked (a curator on a member's row is the curator); nobody identified asks as the panel and is
    refused; a member inside an open voice-verified panel session is that member.

Each test names the control that was run against it. Invented people and words only.
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

import kernel_door
import memory_actor
import memory_door
import memory_scenarios as scenarios
import os_shadow
import user_erase_gate
from memory_scenarios import ANN, BEN, NEW, OLD, OTHER
from test_kernel_door import AreaKernel

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
ON_MAIN = scenarios.unpack(json.loads((Path(__file__).parent / "fixtures" / "memory_as_on_main.json").read_text("utf-8")))
OTHER_FLAGS = ("ZOE_OS_LISTS", "ZOE_OS_REMINDERS", "ZOE_OS_ALARMS", "ZOE_OS_CALENDAR", "ZOE_OS_NOTES", "ZOE_OS_JOURNAL", "ZOE_OS_PEOPLE",
               "ZOE_OS_MUSIC", "ZOE_OS_HOME")
WORDS = ("Teddy", "Rex", "dog", "quiet", "mornings", "bins", "Tuesday")


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ANN, BEN]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_MEMORY", "kernel")
    for other in OTHER_FLAGS:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.state = state
    yield k
    scenarios._restore()
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def _asks(door, verb=None) -> list:
    return [a for a in door.ops("door_ask") if verb is None or a["verb"] == verb]


def _wait(door, n, verb=None, timeout=3.0) -> list:
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(_asks(door, verb)) < n:
        time.sleep(0.01)
    return _asks(door, verb)


def _reports(door, n, timeout=3.0) -> list:
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(door.ops("door_report")) < n:
        time.sleep(0.01)
    return door.ops("door_report")


def _lines(caplog) -> list:
    return [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage() and "area=memory" in r.getMessage()]


async def _settled(caplog, n, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(_lines(caplog)) < n:
        await asyncio.sleep(0.02)
    return _lines(caplog)


def _rows(w) -> dict:
    return {i: (doc, meta.get("status")) for i, (doc, meta) in w.col.rows.items()}


# ---- flag off; door on and allowing ----------------------------------------------------------------------------------------------
#: does the stored record of main keep the rows at the end? (``memory_scenarios.pack`` keeps them from the round-3 pin on; an
#: older fixture holds the writes only, and then the final rows are compared with this branch's own flag-off run instead)
MAIN_HAS_ROWS = any(e[0] == "row" for rec in ON_MAIN.values() for e in rec)


def _as_main(here: dict) -> dict:
    """Every scenario's WHOLE record (writes, audits, returns AND the rows at the end) in the shape main's fixture is stored in."""
    return here if MAIN_HAS_ROWS else {name: scenarios.writes_only(rec) for name, rec in here.items()}


def _off(monkeypatch) -> dict:
    """Every scenario with the flag off (the kernel never reached), whole records."""
    monkeypatch.setenv("ZOE_OS_MEMORY", "off")
    try:
        return scenarios.run_all()
    finally:
        monkeypatch.setenv("ZOE_OS_MEMORY", "kernel")


@pytest.mark.parametrize("value", [None, "off", "shadow", "1"])
def test_any_other_flag_value_makes_the_store_do_exactly_what_main_did(door, monkeypatch, value):
    """The WHOLE record of every scenario, the final rows included, is main's. Control runs: with the flag check dropped from
    ``memory_door.on`` the ``never`` stub raises and the records differ; round 3, once main's record keeps its rows (the
    round-3 pin), a final row changed with no write (``World.record`` adding a key to every row) fails this - it passed while
    ``writes_only`` dropped the rows."""
    def never(*a, **k):
        raise AssertionError("the kernel was reached with the flag off")

    if value is None:
        monkeypatch.delenv("ZOE_OS_MEMORY")
    else:
        monkeypatch.setenv("ZOE_OS_MEMORY", value)
    monkeypatch.setattr(os_shadow, "ask_door", never)
    monkeypatch.setattr(os_shadow, "record_door", never)
    here = scenarios.run_all()
    assert _as_main(here) == ON_MAIN
    if not MAIN_HAS_ROWS:                                   # (an older fixture: the rows against the plain flag-off run)
        assert here == _off(monkeypatch)
    assert door.ops("door_ask") == [] and door.ops("door_report") == []


@pytest.mark.parametrize("kernel", ["allow", "gone"])
def test_with_the_door_on_and_allowing_the_store_is_made_to_do_exactly_what_main_did(door, monkeypatch, kernel):
    """The door adds no write and changes no result or row when the kernel allows, and none when the kernel is DOWN either
    (every ask is ``not_checked`` and the change proceeds): the whole record of every scenario, the final rows included, is the
    flag-off record and main's. Control runs: with the door writing the row twice when allowed (a second ``_write_row`` in
    ``ingest``) every remember scenario gains a write; round 3, a final row's metadata changed with no write when the door is
    on (``World.record`` adding a key to every row under ``ZOE_OS_MEMORY=kernel``) fails this (it passed while ``writes_only``
    dropped the rows), and with
    ``not_checked`` read as a refusal in ``memory_door.ask`` the ``gone`` case fails."""
    off = _off(monkeypatch)
    if kernel == "gone":
        monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 0.2)
        door.close()
    here = scenarios.run_all()
    assert here == off
    assert _as_main(here) == ON_MAIN
    if kernel == "allow":
        verbs = {a["verb"] for a in door.ops("door_ask")}
        assert verbs == set(os_shadow.AREAS["memory"].verbs)
        assert all(a["area"] == "memory" for a in door.ops("door_ask"))


# ---- asked first: remember, correct, approve ---------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_member_remembering_correcting_and_approving_each_ask_first_and_report_how_it_ended(door):
    """Control run: with the ask moved after the write in ``ingest`` the "asked before anything was written" check fails."""
    w = scenarios.World()
    seen: list = []
    real = w.col.upsert
    w.col.upsert = lambda *a, **k: (seen.append(len(_asks(door))), real(*a, **k))[1]
    row = await w.said(OLD)
    assert seen and seen[0] == 1                                                        # asked before anything was written
    held = await w.call("ingest", OTHER, user_id=ANN, source="digest", confidence=0.6, status="pending")
    await w.call("review", held.id, member=ANN, decision="approve", actor=ANN)
    await w.call("review", row.id, member=ANN, decision="edit", edits=NEW, actor=ANN, source_excerpt="no, my dog is named Rex")
    asks = _asks(door)
    assert [a["verb"] for a in asks] == ["memory.remember", "memory.approve", "memory.correct"]
    assert asks[0]["person"] == ANN and asks[0]["owner"] == ANN and "target" not in asks[0]      # no row yet: no row id
    assert asks[1]["target"] == held.id and asks[2]["target"] == row.id
    assert [r["outcome"] for r in _reports(door, 3)] == ["done", "done", "done"]


@pytest.mark.parametrize("reason", ["not_owner", "adults_only", "unknown_person"])
@pytest.mark.asyncio
async def test_a_refused_change_is_not_made_and_the_caller_is_told_why(door, reason):
    """A kernel "no" is final for every asked verb. Control run: with the refusal not raised in ``memory_door.ask`` the
    refused remember is stored."""
    w = scenarios.World()
    row = await w.said(OLD)
    held = await w.call("ingest", OTHER, user_id=ANN, source="digest", confidence=0.6, status="pending")
    before, writes, reports = _rows(w), len(w.log), len(_reports(door, 1))
    door.door = f"refuse:{reason}"
    acting = memory_actor.member(ANN)
    for call in (lambda: w.svc.ingest(NEW, user_id=ANN, source="voice_fact", acting=acting),
                 lambda: w.svc.review(row.id, decision="edit", edits=NEW, actor=ANN, acting=acting),
                 lambda: w.svc.review(held.id, decision="approve", actor=ANN, acting=acting),
                 lambda: w.svc.supersede_by(ANN, row.id, held.id, actor="voice_fact", acting=acting),
                 lambda: w.svc.flag_challenged(held.id, user_id=ANN, utterance="that is not true", acting=acting),
                 lambda: w.svc.set_curated_standing(ANN, [row.id], protect=True, actor=ANN, acting=acting)):
        with pytest.raises(memory_door.MemoryRefused) as exc:
            await call()
        assert exc.value.reason == reason and exc.value.line() and isinstance(exc.value, kernel_door.Refused)
    assert _rows(w) == before and len(w.log) == writes                                  # nothing written, nothing audited
    assert len(door.ops("door_report")) == reports


def test_the_callers_that_would_hide_a_refusal_say_it_instead():
    """The spoken remember and the brain's three memory tools catch every error as an outage; each now answers a kernel
    refusal with its sentence. Control run: the ``except kernel_door.Refused`` dropped from the spoken remember fails this."""
    spoken = (SVC / "intent_router.py").read_text("utf-8")
    i = spoken.index('source="brain_tool"')
    assert "except kernel_door.Refused as exc:" in spoken[i:i + 1500] and "return exc.line()" in spoken[i:i + 1600]
    tools = (SVC / "mcp_server.py").read_text("utf-8")
    assert tools.count('except kernel_door.Refused as exc:      # the Zoe OS kernel said no (ZOE_OS_MEMORY=kernel)') == 3


@pytest.mark.parametrize("how, reason", [("slow", "timeout"), ("gone", "unreachable"), ("old_kernel", "old_kernel")])
@pytest.mark.asyncio
async def test_a_change_proceeds_not_checked_when_the_kernel_cannot_answer(door, caplog, monkeypatch, how, reason):
    """Control run: with ``not_checked`` treated as a refusal in ``memory_door.ask`` nothing is stored."""
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 0.2)
    if how == "slow":
        door.delay = 0.8
    elif how == "gone":
        door.close()
    else:
        door.door = "old_kernel"
    w = scenarios.World()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        row = await w.said(OLD)
        lines = await _settled(caplog, 1)
    assert row is not None and row.id in w.col.rows
    assert lines and "decision=not_checked" in lines[0] and "verb=memory.remember" in lines[0], (lines, reason)


# ---- forget: made first, told afterwards, never asked ------------------------------------------------------------------------------
@pytest.mark.parametrize("kernel", ["allow", "refuse:unknown_person", "refuse:not_owner", "slow", "gone"])
@pytest.mark.asyncio
async def test_a_forget_happens_exactly_as_with_the_door_off_whatever_the_kernel_does_and_is_told_afterwards(door, caplog, monkeypatch, kernel):
    """The door puts NOTHING in front of a forget: under each kernel condition a reject and an erase end exactly as they do
    with the flag off (the existing fail-closed forget step is main's and is untouched: it alone decides what a kernel that
    is down means for an erase), nothing is asked before the row changes, and the call does not wait for the kernel. A kernel
    "no" (someone off the roster, say) only makes the record say objected: the erase stands. Control run: with the forget
    asked first (``tell_after`` asking and raising) the refusing kernel keeps the row."""
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 1.0)

    async def forget(flag):
        monkeypatch.setenv("ZOE_OS_MEMORY", flag)
        door.door, door.delay = "allow", 0
        os_shadow._reset_for_tests()
        w = scenarios.World()
        row = await w.said(OLD)
        keep = await w.said(OTHER)
        told_before = len(_asks(door, "memory.forget"))
        at_write: list = []
        for name in ("update", "delete"):
            real = getattr(w.col, name)
            setattr(w.col, name, lambda *a, _real=real, **k: (at_write.append(len(_asks(door, "memory.forget")) - told_before), _real(*a, **k))[1])
        if kernel == "slow":
            door.delay = 0.8
        elif kernel == "gone":
            door.close()
        else:
            door.door = kernel
        acting, ended, started = memory_actor.member(ANN), [], time.monotonic()
        for step in (lambda: w.svc.review(row.id, decision="reject", actor=ANN, acting=acting),
                     lambda: w.svc.erase_rows(ANN, [row.id], actor=ANN, acting=acting)):
            try:
                await step()
                ended.append("made")
            except Exception as exc:  # noqa: BLE001 - main's own fail-closed step, compared with the flag off below
                ended.append(type(exc).__name__)
        took = time.monotonic() - started
        scenarios._restore()
        return row, ended, sorted((i, st) for i, (_d, st) in _rows(w).items()), at_write, took, told_before, keep

    with caplog.at_level(logging.INFO, logger="os_shadow"):
        row, ended, rows, at_write, took, told_before, keep = await forget("kernel")
        told = _wait(door, told_before + ended.count("made"), "memory.forget", timeout=2.0)[told_before:] if kernel != "gone" else []
        lines = [l for l in await _settled(caplog, 2 + len(told)) if "verb=memory.forget" in l]
    _row, ended_off, rows_off, _w, took_off, _t, _k = await forget("off")
    assert ended == ended_off and [st for _i, st in rows] == [st for _i, st in rows_off]      # exactly as with the door off
    assert ended[0] == "made" and (keep.id, "approved") in rows                         # the reject always happens
    assert at_write and at_write[0] == 0                                                # nothing was asked before the row changed
    assert took < took_off + 0.5, (took, took_off)                                      # and the kernel was not waited for
    if kernel in ("allow", "refuse:unknown_person", "refuse:not_owner"):
        assert ended == ["made", "made"] and not any(i == row.id for i, _st in rows)
        assert [(a["person"], a["target"], a["owner"]) for a in told] == [(ANN, row.id, ANN)] * 2
        want = "recorded" if kernel == "allow" else "objected"
        assert len(lines) == 2 and all(f"decision={want}" in l for l in lines), lines


@pytest.mark.asyncio
async def test_a_forget_that_did_not_happen_is_not_told(door):
    """The store failing under a reject: the call raises and the kernel hears nothing. Control run: with ``_close`` telling
    whatever the outcome, the failed reject is told."""
    w = scenarios.World()
    row = await w.said(OLD)

    def broken(*a, **k):
        raise RuntimeError("store down")

    w.col.update = w.col.upsert = broken
    with pytest.raises(Exception):
        await w.svc.review(row.id, decision="reject", actor=ANN, acting=memory_actor.member(ANN))
    await asyncio.sleep(0.3)
    assert _asks(door, "memory.forget") == [] and w.col.rows[row.id][1]["status"] == "approved"


@pytest.mark.asyncio
@pytest.mark.parametrize("ids_of", ["none of the member's", "empty"])
async def test_a_forget_that_removed_no_row_is_not_told(door, ids_of):
    """``erase_rows`` answers ``{"rows_removed": 0}`` for an empty request and for ids none of which are the member's: no
    forget happened, so the kernel is told nothing. Control run: with ``memory_door._made`` back to "anything but None /
    False" (the old success check), both calls are told as forgets."""
    w = scenarios.World()
    other = await w.said(OTHER, user=BEN)                                           # another member's row
    ids = [other.id] if ids_of != "empty" else []
    out = await w.svc.erase_rows(ANN, ids, actor=ANN, acting=memory_actor.member(ANN))
    assert out == {"rows_removed": 0} or out.get("rows_removed") == 0
    await asyncio.sleep(0.3)
    assert _asks(door, "memory.forget") == [] and other.id in w.col.rows
    # and a forget that DID remove a row is told (the same call, the member's own row)
    mine = await w.said(OLD)
    await w.svc.erase_rows(ANN, [mine.id], actor=ANN, acting=memory_actor.member(ANN))
    assert len(_wait(door, 1, "memory.forget")) == 1


@pytest.mark.asyncio
async def test_an_admins_whole_member_forget_names_whose_memory_was_erased(door):
    """An administrator erasing ANOTHER member (``delete_user``, as the route lets an admin) is told with that member as the
    owner, as the member's own erase is (the kernel's erase-stamp order then records nothing naming them). Controls run: with
    ``delete_user`` telling ``owner=None`` for another member's erase (the old code) the admin's told forget has no owner and
    this fails; with the door's report back INSIDE the erase span (the old decorator order) both told forgets are dropped
    unsent (``os_shadow.record_door``: ``erasing``) and this fails."""
    w = scenarios.World()
    await w.said(OLD)
    keep = await w.said(OTHER, user=BEN)
    assert await w.svc.delete_user(ANN, actor=BEN, reason="right to be forgotten", acting=memory_actor.member(BEN)) == 1
    mine = await w.said("User's cat is named Womble.", user=BEN)
    assert await w.svc.delete_user(BEN, actor=BEN, acting=memory_actor.member(BEN)) == 2
    told = _wait(door, 2, "memory.forget")
    assert [(a["person"], a.get("owner")) for a in told] == [(BEN, ANN), (BEN, BEN)]
    assert keep.id not in w.col.rows and mine.id not in w.col.rows


@pytest.mark.asyncio
async def test_a_whole_member_forget_that_removed_no_row_is_not_told(door):
    """``delete_user`` answers the number of rows removed: an admin's erase of a member with no rows answers 0, a forget that
    did not happen, and the kernel is told nothing (the erase of a member WITH rows, right after, is told). Control run: with
    ``memory_door._made`` back to the identity check (``out is False``: an int 0 counts as made) the empty erase is told."""
    w = scenarios.World()
    keep = await w.said(OTHER, user=BEN)
    assert await w.svc.delete_user(ANN, actor=BEN, acting=memory_actor.member(BEN)) == 0
    await asyncio.sleep(0.3)
    assert _asks(door, "memory.forget") == [] and keep.id in w.col.rows
    assert await w.svc.delete_user(BEN, actor=BEN, acting=memory_actor.member(BEN)) == 1
    assert [a.get("owner") for a in _wait(door, 1, "memory.forget")] == [BEN]


def test_every_answer_a_doored_method_gives_is_read_as_made_or_not():
    """The return shapes of the seven doored methods (``ingest`` / ``review``: a row or None; ``supersede_by`` /
    ``flag_challenged``: a bool; ``delete_user`` / ``set_curated_standing``: a count; ``erase_rows``: ``{"rows_removed": n}``):
    no real change reads as not made, and no empty one as made. Control run: ``_made`` without its bool / int lines fails on
    ``0``."""
    import memory_service

    row = memory_service.MemoryRef(id="m1", text="x", metadata={})
    made = [row, True, 1, 3, {"rows_removed": 2, "audit_removed": 0}]
    not_made = [None, False, 0, {"rows_removed": 0}, {"rows_removed": None}, {"rows_removed": "x"}]
    assert [memory_door._made(v) for v in made] == [True] * len(made)
    assert [memory_door._made(v) for v in not_made] == [False] * len(not_made)


@pytest.mark.parametrize("flag,who", [("off", "member"), ("kernel", "automatic"), ("off", "automatic")])
def test_erase_rows_reads_its_ids_exactly_as_main_does_off_the_door(door, monkeypatch, flag, who):
    """With the door off, or for an automatic writer, ``erase_rows`` does not copy ``ids`` up front: an iterator is consumed
    only where main consumes it (after the user check), so a call that fails that check leaves it untouched. Control run: with
    the up-front ``ids = list(ids)`` back outside ``memory_door.on`` this fails (the iterator is drained before the refusal)."""
    import memory_service

    monkeypatch.setenv("ZOE_OS_MEMORY", flag)
    os_shadow._reset_for_tests()
    w = scenarios.World()
    pulled: list = []

    def ids():
        for i in ("a1", "a2"):
            pulled.append(i)
            yield i

    acting = memory_actor.member(ANN) if who == "member" else memory_actor.AUTOMATIC
    with pytest.raises(memory_service.MemoryServiceError):
        asyncio.run(w.svc.erase_rows("", ids(), actor="x", acting=acting))
    scenarios._restore()
    assert pulled == []


# ---- automatic writers -----------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_automatic_writer_is_neither_asked_nor_told(door):
    """Control run: with ``memory_door.on`` true for AUTOMATIC the extractor's ingest raises (no member id to send)."""
    w = scenarios.World()
    row = await w.said(OLD)                                                              # the member: one ask
    new = await w.call("ingest", NEW, user_id=ANN, source="chat_regex", confidence=0.8)
    await w.call("review", row.id, decision="edit", edits=NEW, actor="chat_regex")
    other = await w.call("ingest", OTHER, user_id=ANN, source="digest", confidence=0.5)
    await w.call("review", other.id, decision="reject", actor="operator")
    await w.call("erase_rows", ANN, [other.id], actor="synthesis_undo")
    await w.call("sweep_soft_archive", user_id=ANN, actor="decay_sweep", min_age_days=0, score_threshold=1.0)
    assert new is not None
    await asyncio.sleep(0.2)
    assert [a["verb"] for a in _asks(door)] == ["memory.remember"]
    for kind in ("member", "automatic"):
        assert kind in {who for who, _n in __import__("memory_writers").SITES.values()}


# ---- what crosses ------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_ask_carries_ids_and_closed_codes_only_never_the_memory(door, caplog):
    """Control run: with the row's text sent as the target (``_ids`` not filtering to an id) the words assertion fails."""
    w = scenarios.World()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        row = await w.said(OLD, tags=["dog"], metadata={"note": "Teddy"})
        house = await w.call("ingest", "The bins go out on Tuesday.", member=ANN, user_id=ANN, source="voice_fact", confidence=0.9,
                             scope="shared", source_excerpt="the bins go out on Tuesday")
        await w.call("review", row.id, member=ANN, decision="edit", edits=NEW, actor=ANN, note="Rex is the dog",
                     source_excerpt="no, my dog is named Rex")
        await w.call("review", house.id, member=ANN, decision="archive", actor=ANN)
        lines = await _settled(caplog, 4)
    asks = _wait(door, 4)
    allowed = {"area", "verb", "person", "target", "owner", "visibility", "stamps", "via", "turn"}
    assert all(set(a) <= allowed for a in asks), [sorted(a) for a in asks]
    assert [a.get("visibility") for a in asks] == ["private", "household", "private", "household"]
    sent = json.dumps(door.calls, default=str) + " ".join(lines)
    assert not [word for word in WORDS if word.lower() in sent.lower()]
    assert all(ANN in json.dumps(a) for a in asks) and len(lines) == 4


@pytest.mark.asyncio
async def test_the_acting_member_is_who_asked_not_whose_row_it_is(door):
    """A curator, an admin, a guardian: the ask names THEM as the person and the row's owner as the owner. Control run: with
    the owner sent as the person the assertion fails."""
    w = scenarios.World()
    row = await w.said(OLD)
    await w.call("review", row.id, member=BEN, decision="edit", edits=NEW, actor="curator", origin="curator")
    await w.call("review", row.id, member=BEN, decision="reject", actor=BEN, origin="admin")
    asks = _wait(door, 3)
    assert [(a["verb"], a["person"], a["owner"]) for a in asks[1:]] == [("memory.correct", BEN, ANN), ("memory.forget", BEN, ANN)]


# ---- the panel -------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_nobody_identified_asks_as_the_panel_and_a_voice_verified_member_asks_as_themself(door, monkeypatch):
    """How a verified panel session is represented today (``voice_session.py``): one per panel, in process memory, holding the
    member id; while it is open the turn's user IS that member, and with it closed the turn's user is ``guest``. So the door
    needs no panel rule of its own: the member's id asks as the member, and ``guest`` asks as the panel and is refused (the
    store then drops a guest's fact exactly as it always did). Control run: with ``_ids`` sending ``guest`` as a person the
    panel assertion fails."""
    import voice_session

    monkeypatch.setenv("ZOE_VOICE_SESSION", "1")
    voice_session.reset()
    voice_session._open_or_renew("panel-kitchen", ANN, voice_session._clock(), "ok")
    speaker = voice_session.active_member("panel-kitchen")
    assert speaker == ANN
    w = scenarios.World()
    assert await w.said(OLD, user=speaker) is not None                                   # inside the session: the member
    voice_session._close("panel-kitchen", "unverified_voice")
    assert voice_session.active_member("panel-kitchen") is None
    door.door = "refuse:not_owner"
    assert await w.svc.ingest(NEW, user_id="guest", source="voice_fact", acting=memory_actor.member("guest")) is None
    asks = _asks(door)
    assert asks[0]["person"] == ANN and "via" not in asks[0]
    assert asks[1].get("via") == "panel" and asks[1]["person"] == "panel" and "owner" not in asks[1]       # the word, never an id
    assert len(w.col.rows) == 1
    voice_session.reset()


# ---- reports -----------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_change_the_store_did_not_make_is_reported_failed(door):
    """An allowed ask whose write was dropped or raised is closed ``failed``. Control run: with ``_close`` always saying
    ``done`` both of these say ``done``."""
    w = scenarios.World()
    acting = memory_actor.member(ANN)
    assert await w.svc.ingest("My card number is 4111 1111 1111 1111.", user_id=ANN, source="voice_fact", acting=acting) is None
    with pytest.raises(Exception):
        await w.svc.ingest(OLD, user_id=ANN, source="voice_fact", scope="no-such-scope", acting=acting)
    assert [r["outcome"] for r in _reports(door, 2)] == ["failed", "failed"]


# ---- structure ---------------------------------------------------------------------------------------------------------------------
def test_every_method_a_member_reaches_has_the_door_and_the_rest_have_only_automatic_callers():
    """Control run: with ``@_memory_door.reporting`` dropped from ``review`` this fails; a member site added on a method that
    has no door (``relink_entity``) fails it too."""
    import memory_writers

    tree = ast.parse((SVC / "memory_service.py").read_text("utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MemoryService")
    doored = set()
    for fn in cls.body:
        if isinstance(fn, ast.AsyncFunctionDef) and fn.name in memory_writers.MUTATING:
            decorated = any(ast.unparse(d) == "_memory_door.reporting" for d in fn.decorator_list)
            calls = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and ast.unparse(n.func.value) == "_memory_door"}
            assert decorated == bool(calls & {"ask", "tell_after"}), fn.name
            if decorated:
                doored.add(fn.name)
    assert doored == {"ingest", "review", "supersede_by", "set_curated_standing", "flag_challenged", "erase_rows", "delete_user"}
    reached = {method for (_mod, _fn, method), (who, _n) in memory_writers.SITES.items() if who in ("member", "passes_on")}
    assert reached <= doored, reached - doored
    assert tuple(os_shadow.AREAS["memory"].verbs) == (memory_door.REMEMBER, memory_door.CORRECT, memory_door.APPROVE, memory_door.FORGET)
    src = (SVC / "memory_door.py").read_text("utf-8")
    assert "labs" not in src.replace("docs/knowledge", "") and "forget_for_erase(" not in src.replace("``os_shadow.forget_for_erase``", "")
