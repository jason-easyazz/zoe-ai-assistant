"""The lists door asks the Zoe OS kernel (``ZOE_OS_LISTS=kernel``; docs/architecture/zoe-os-cutover-plan.md, "Changed 2026-11-18").

* any other flag value: NO kernel call, no extra query, the same statements as before;
* the kernel allows: the change is made and its outcome reported after the commit; only ids cross the socket;
* the kernel refuses: nothing is written, ``ListRefused``, and each caller says so in its own shape;
* the kernel cannot be reached or is slow: the change is made, one ``not_checked`` line, never an exception;
* a forget is ordered against an ask by the KERNEL: every ask and report carries an erase stamp for each member it names, every
  whole-member forget carries that member's stamp, and the kernel's ``erased`` answer lets the change proceed ``not_checked``;
* every function that writes a list table asks (a structural scan with a planted negative control);
* the watch-only list hooks are silent while the door is on.

A fake kernel on a real Unix socket (``test_os_shadow.FakeKernel``). Invented ids and words only.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest

import list_service
import os_shadow
import user_erase_gate
from test_os_shadow import SVC, FakeKernel

pytestmark = pytest.mark.ci_safe

OWNER, KID = "member-ova", "member-kip"
WORD, NAME = "womble", "Vellum"


class DoorKernel(FakeKernel):
    door = "allow"

    def _answer(self, conn, skill, req):
        if req["op"] == "list_ask":
            if self.door.startswith("refuse:"):
                return self._refuse(conn, req, self.door.split(":", 1)[1])
            if self.door == "close":
                raise ConnectionError
            if self.door == "old_kernel":
                from test_os_shadow import _send
                return _send(conn, {"v": 1, "id": req["id"], "ok": False, "error": {"code": "bad_request", "reason": "unknown op"}})
            self._seq += 1
            return self._ok(conn, req, {"ask": self._seq, "seq": self._seq})
        if req["op"] == "list_report":
            return self._ok(conn, req, {"seq": 1, "outcome": req["args"]["outcome"]})
        return super()._answer(conn, skill, req)

    def ops(self, op):
        return [a for _, o, a in self.calls if o == op]

    def reports(self, n, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(self.ops("list_report")) < n:
            time.sleep(0.01)
        return [(a["ask"], a["outcome"]) for a in self.ops("list_report")]


@pytest.fixture
def door(monkeypatch):
    k = DoorKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [OWNER, KID]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LISTS", "kernel")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.state = state
    yield k
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


class _Cur:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)


class DB:
    """Records every statement; one list row (``L1``) for the door's own lookup. ``transaction`` only when asked for."""

    def __init__(self, owner=OWNER, visibility="family", items=()):
        self.sql, self.fetched, self.owner, self.visibility, self.items = [], [], owner, visibility, list(items)
        self.depth = self.committed = 0

    async def execute(self, sql, *params):
        self.sql.append(" ".join(sql.split()))
        return _Cur([{"id": "L1"}] if sql.lstrip().upper().startswith("SELECT") and "FROM lists" in sql else [])

    async def fetch(self, sql, *args):
        self.fetched.append(" ".join(sql.split()))
        if "FROM list_items WHERE list_id" in sql:
            return self.items
        return [{"id": "L1", "user_id": self.owner, "visibility": self.visibility}]

    async def commit(self):
        pass

    @property
    def writes(self):
        return [s for s in self.sql if s.split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]


class TxDB(DB):
    fail_commit = False

    def transaction(self):
        db = self

        class _T:
            async def __aenter__(self):
                db.depth += 1

            async def __aexit__(self, exc_type, *a):
                db.depth -= 1
                if exc_type is None and db.depth == 0:
                    if db.fail_commit:
                        raise RuntimeError("commit failed")
                    db.committed += 1
                return False
        return _T()


#: every door function: (the kernel verb it asks, how to call it as ``who``)
DOORS = {
    "create_list_record": ("list.create", lambda db, who: list_service.create_list_record(
        db, user_id=who, name=NAME, list_type="personal", visibility="personal", stamps_at=list_service.stamps_now())),
    "create_item_record": ("item.add", lambda db, who: list_service.create_item_record(db, list_id="L1", text=WORD, user_id=who)),
    "remove_item_record": ("item.remove", lambda db, who: list_service.remove_item_record(db, user_id=who, item_id="I1", list_id="L1")),
    "set_item_checked": ("item.tick", lambda db, who: list_service.set_item_checked(db, user_id=who, item_id="I1", completed=1)),
    "update_item_record": ("item.edit", lambda db, who: list_service.update_item_record(
        db, user_id=who, item_id="I1", list_id="L1", updates=["text = ?"], params=[WORD, "I1", "L1"])),
    "update_list_record": ("list.rename", lambda db, who: list_service.update_list_record(
        db, user_id=who, list_id="L1", updates=["name = ?"], params=[NAME, "L1"])),
    "delete_list_record": ("list.delete", lambda db, who: list_service.delete_list_record(db, user_id=who, list_id="L1")),
    "set_list_visibility_record": ("list.share", lambda db, who: list_service.set_list_visibility_record(
        db, user_id=who, list_id="L1", share=True)),
    "rename_item_on_list": ("item.edit", lambda db, who: list_service.rename_item_on_list(
        db, user_id=who, list_id="L1", old_text="wombel", new_text=WORD)),
    "add_items_to_list": ("item.add", lambda db, who: list_service.add_items_to_list(
        db, text=f"{WORD} and quince", user_id=who, list_type="shopping", list_name="Shopping", new_list_visibility="family")),
}


#: the statements each door function issued BEFORE it could ask the kernel (generated once from the one-door branch's
#: ``list_service`` with these same calls; SQL text only). Regenerate only for a deliberate change to a list write.
BEFORE_THE_DOOR = json.loads((Path(__file__).parent / "fixtures" / "lists_door_flag_off_statements.json").read_text())


def _db():
    return TxDB(items=[{"id": "I1", "text": "wombel", "completed": 0}])


# ---- flag off: main's behaviour, exactly ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(DOORS))
@pytest.mark.parametrize("value", [None, "off", "shadow", "on", "KERNELS"])
@pytest.mark.asyncio
async def test_any_other_flag_value_makes_no_kernel_call_no_extra_query_and_the_same_statements(door, monkeypatch, name, value):
    async def never(*a, **k):
        raise AssertionError("the kernel was asked with the flag off")

    allowed = _db()
    await DOORS[name][1](allowed, OWNER)                       # flag on, allowed: the statements to compare against
    asked = len(door.ops("list_ask"))
    assert asked >= 1
    monkeypatch.setattr(os_shadow, "ask_list", never)
    monkeypatch.setattr(os_shadow, "report_list", never)
    monkeypatch.setattr(os_shadow, "door_unchecked", never)
    if value is None:
        monkeypatch.delenv("ZOE_OS_LISTS")
    else:
        monkeypatch.setenv("ZOE_OS_LISTS", value)
    off = _db()
    await DOORS[name][1](off, OWNER)
    door_lookups = [s for s in allowed.fetched if s not in off.fetched]
    assert len(door.ops("list_ask")) == asked and off.sql == allowed.sql and off.writes
    # ... and against an INDEPENDENT record: what the code before the door issued for the same call (captured from the one-door
    # branch, where no kernel ask exists; a change that alters flag-on and flag-off alike still fails here)
    assert {"sql": off.sql, "fetched": off.fetched} == BEFORE_THE_DOOR[name]
    assert [s for s in off.fetched if "SELECT id, user_id, visibility FROM lists WHERE id = $1" == s
            or s.startswith("SELECT l.id, l.user_id")] == []   # the door's own read never runs with the flag off
    assert name == "create_list_record" or door_lookups         # ... and it did run with the flag on (the control)


# ---- allowed ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(DOORS))
@pytest.mark.asyncio
async def test_an_allowed_change_is_made_asked_by_ids_only_and_reported_done(door, caplog, name):
    caplog.set_level("INFO", logger="os_shadow")
    db = _db()
    await DOORS[name][1](db, OWNER)
    asks = door.ops("list_ask")
    assert db.writes and {a["verb"] for a in asks} == {DOORS[name][0]} and len(asks) == (2 if name == "add_items_to_list" else 1)
    assert all(a["person"] == OWNER and a["owner"] == OWNER and "via" not in a for a in asks)
    assert asks[0]["visibility"] == ("private" if name == "create_list_record" else "household")
    assert all(set(a) <= {"verb", "person", "list", "items", "owner", "visibility", "turn", "stamps"} for a in asks)
    sent = json.dumps(door.calls)
    assert WORD not in sent and NAME not in sent and "quince" not in sent and "Shopping" not in sent      # ids only
    assert [o for _, o in door.reports(len(asks))] == ["done"] * len(asks)
    lines = [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]
    assert len(lines) == len(asks) and all(f"area=lists verb={DOORS[name][0]} decision=allow reason=- ask_ms=" in line for line in lines)
    assert not any(w in " ".join(lines) for w in (WORD, NAME, OWNER, "L1"))


@pytest.mark.asyncio
async def test_the_panel_is_asked_as_the_panel_and_its_device_id_never_crosses(door):
    for who in ("guest-kiosk-7", "__household__", ""):
        await DOORS["create_item_record"][1](DB(), who)
    asks = door.ops("list_ask")
    assert [a.get("via") for a in asks] == ["panel"] * 3 and "kiosk" not in json.dumps(door.calls)


@pytest.mark.asyncio
async def test_a_spoken_add_is_reported_only_after_its_transaction_committed_and_failed_when_the_commit_fails(door, monkeypatch):
    seen = []
    real = os_shadow.report_list
    db = _db()
    monkeypatch.setattr(os_shadow, "report_list", lambda ask, outcome, stamps=None: (seen.append((db.depth, db.committed, outcome)), real(ask, outcome, stamps)))
    await DOORS["add_items_to_list"][1](db, OWNER)
    assert seen == [(0, 1, "done")] * 2                         # outside the transaction, after the one commit
    del seen[:]
    db = _db()
    db.fail_commit = True
    with pytest.raises(RuntimeError):
        await DOORS["add_items_to_list"][1](db, OWNER)
    assert seen == [(0, 0, "failed")] * 2


@pytest.mark.asyncio
async def test_a_write_that_raises_is_reported_failed(door):
    class Boom(DB):
        async def execute(self, sql, *params):
            if sql.lstrip().upper().startswith("UPDATE"):
                raise RuntimeError("write failed")
            return await super().execute(sql, *params)

    with pytest.raises(RuntimeError):
        await DOORS["delete_list_record"][1](Boom(), OWNER)
    assert [o for _, o in door.reports(1)] == ["failed"]


# ---- refused: final ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(DOORS))
@pytest.mark.asyncio
async def test_a_refused_change_is_not_made_and_raises_list_refused(door, caplog, name):
    caplog.set_level("INFO", logger="os_shadow")
    door.door = "refuse:adults_only"
    db = _db()
    with pytest.raises(list_service.ListRefused) as exc:
        await DOORS[name][1](db, KID)
    assert exc.value.reason == "adults_only" and db.writes == [] and db.committed == 0
    time.sleep(0.05)
    assert door.ops("list_report") == []                        # nothing to report: nothing was allowed
    assert [r.getMessage().split(" ask_ms")[0] for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()] == \
        [f"ZOE_OS_ACT area=lists verb={DOORS[name][0]} decision=refuse reason=adults_only"]


@pytest.mark.asyncio
async def test_the_refuse_test_has_teeth_a_door_whose_ask_always_allows_writes_the_refused_change(door, monkeypatch):
    """The control for the test above: with the ask stubbed to allow, the same refused call DOES write."""
    door.door = "refuse:not_owner"

    async def always(*a, **k):
        return list_service.Decision("allow", "", None)

    monkeypatch.setattr(list_service, "ask_kernel", always)
    db = _db()
    await DOORS["delete_list_record"][1](db, KID)
    assert db.writes


@pytest.mark.parametrize("reason,code", [("carries forgotten content", "forgotten"), ("some new kernel words", "other"),
                                         ("unknown_person", "unknown_person")])
@pytest.mark.asyncio
async def test_every_kernel_refusal_is_final_whatever_its_words_and_reaches_the_log_as_a_closed_code(door, caplog, reason, code):
    caplog.set_level("INFO", logger="os_shadow")
    door.door = f"refuse:{reason}"
    db = DB()
    with pytest.raises(list_service.ListRefused) as exc:
        await DOORS["remove_item_record"][1](db, OWNER)
    assert exc.value.reason == code and db.writes == []
    assert f"reason={code} " in caplog.text and "new kernel words" not in caplog.text


# ---- unreachable or slow: the change proceeds ---------------------------------------------------------------------------------------
async def _proceeds(door, caplog, name, reason, limit_s=1.0):
    caplog.set_level("INFO", logger="os_shadow")
    db = _db()
    started = time.perf_counter()
    await DOORS[name][1](db, OWNER)                             # never an exception
    took = time.perf_counter() - started
    lines = [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]
    assert db.writes and took < limit_s, took
    assert lines and all("decision=not_checked" in line for line in lines) and f"reason={reason} " in lines[0], lines
    return lines


@pytest.mark.parametrize("name", ["remove_item_record", "add_items_to_list", "rename_item_on_list"])
@pytest.mark.asyncio
async def test_a_kernel_that_is_down_lets_the_change_proceed_with_one_not_checked_line(door, caplog, name):
    door.srv.close()
    Path(door.path).unlink()
    lines = await _proceeds(door, caplog, name, "unavailable")
    assert len(lines) == (2 if name == "add_items_to_list" else 1)
    assert "reason=circuit " in lines[-1] or len(lines) == 1    # the second ask of the same add met the open circuit: no reconnect


@pytest.mark.asyncio
async def test_a_kernel_never_installed_and_one_that_drops_or_predates_the_door_are_not_checked(door, caplog):
    door.door = "close"
    await _proceeds(door, caplog, "remove_item_record", "unavailable")
    caplog.clear()
    os_shadow._reset_for_tests()
    door.door = "old_kernel"                                    # "unknown op" is a bad request, never a refusal
    await _proceeds(door, caplog, "remove_item_record", "protocol")
    caplog.clear()
    os_shadow._reset_for_tests()
    (door.state / "creds.json").unlink()
    await _proceeds(door, caplog, "remove_item_record", "not_provisioned")


@pytest.mark.asyncio
async def test_a_slow_kernel_is_cut_off_at_the_deadline_and_the_change_proceeds(door, caplog, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LISTS_ASK_MS", "60")
    door.delay = 0.6
    await _proceeds(door, caplog, "remove_item_record", "timeout", limit_s=0.4)
    assert os_shadow.ASK_DEADLINE_S == 0.150 and os_shadow.ask_deadline_s() == 0.060


@pytest.mark.asyncio
async def test_the_ask_runs_off_the_event_loop(door, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LISTS_ASK_MS", "2000")
    door.delay = 0.1
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(tick())
    await DOORS["remove_item_record"][1](DB(), OWNER)
    task.cancel()
    assert ticks >= 5 and len(door.ops("list_ask")) == 1          # the loop kept turning while the kernel took its time


@pytest.mark.asyncio
async def test_a_member_being_erased_is_not_put_to_the_kernel_and_the_change_proceeds(door, caplog):
    (door.state / "pending-forgets.json").write_text(json.dumps([OWNER]))
    await _proceeds(door, caplog, "remove_item_record", "erasing")
    assert door.ops("list_ask") == []


# ---- ordered against a forget by the kernel: stamps on every ask, report and whole-member forget -----------------------------------
def _is_stamp(value) -> bool:
    return isinstance(value, list) and len(value) == 2 and all(type(n) is int and n >= 0 for n in value)


@pytest.mark.asyncio
async def test_every_ask_and_its_report_carry_an_erase_stamp_for_the_actor_and_the_lists_owner(door):
    """The kernel can only order what it is told: a stamp for EVERY member the ask names, and the report carries the ASK's stamps
    (read at the ask, never again). Controls run: with the owner left out of ``ask_list``'s stamps the first assert fails; with
    ``report_list`` dropping its stamps the report assert fails; with the report's stamps read again at the report (after the
    erase below) the equality with the ask's fails."""
    await DOORS["remove_item_record"][1](_db(), KID)             # L1 is OWNER's household list: two members named
    (asked,) = door.ops("list_ask")
    assert set(asked["stamps"]) == {KID, OWNER} and all(_is_stamp(v) for v in asked["stamps"].values())
    assert asked["stamps"][KID][0] == asked["stamps"][OWNER][0] == os_shadow._BOOT      # one boot stamp per process
    door.reports(1)
    assert door.ops("list_report")[0]["stamps"] == asked["stamps"]
    held = _db()
    async with list_service.kernel_reports_after():                # the report waits for the commit; a forget lands meanwhile
        await DOORS["remove_item_record"][1](held, KID)
        assert await os_shadow.forget_for_erase(OWNER) == 3        # a confirmed kernel forget: the only thing that moves a stamp
    door.reports(2)
    assert door.ops("list_report")[1]["stamps"] == door.ops("list_ask")[1]["stamps"]
    assert door.ops("list_report")[1]["stamps"][OWNER] < os_shadow.erase_stamp(OWNER)      # the ask's, not one read at the report
    await DOORS["create_item_record"][1](DB(), "guest-kiosk-7")   # the panel: nobody identified, only the owner is named
    assert set(door.ops("list_ask")[-1]["stamps"]) == {OWNER}


@pytest.mark.parametrize("erase", ["member", "rows"])
@pytest.mark.asyncio
async def test_a_forget_reads_its_stamp_then_moves_on_so_every_earlier_ask_is_at_or_before_it(door, erase, monkeypatch):
    """What makes the kernel's ordering sound (it refuses a stamp that is not STRICTLY newer than the forget's): every ask stamped
    before the forget was SENT, including one made while a row- or turn-scoped forget was outstanding (no erase span holds the
    door then), is at or before the forget's stamp; every ask stamped after the send is strictly newer. Controls run: with the
    move-on removed from ``_forget_call`` the "rows" case fails (the member acting again would read as erased); with
    ``stamp`` left off the forget call both cases fail on the missing key."""
    await DOORS["remove_item_record"][1](_db(), OWNER)
    before = door.ops("list_ask")[0]["stamps"][OWNER]
    outstanding = []
    call = os_shadow._forget_call

    def spying(kernel, member, scoped):
        outstanding.append(os_shadow.erase_stamp(member))          # an ask made now: the forget is asked for, not yet sent
        return call(kernel, member, scoped)

    monkeypatch.setattr(os_shadow, "_forget_call", spying)
    if erase == "member":
        with user_erase_gate.erase_span(OWNER):
            assert await os_shadow.forget_user(OWNER) == 3
    else:
        assert await os_shadow.forget_for_erase(OWNER) == 3
    (forget,) = door.ops("forget")
    assert forget["subject"] == OWNER and _is_stamp(forget["stamp"])
    assert before <= forget["stamp"] and outstanding == [forget["stamp"]]
    assert os_shadow.erase_stamp(OWNER) > forget["stamp"]                          # moved on by the send
    await DOORS["remove_item_record"][1](_db(), OWNER)
    assert door.ops("list_ask")[1]["stamps"][OWNER] > forget["stamp"]              # the member acting again: strictly newer


@pytest.mark.asyncio
async def test_an_ask_stamped_while_the_forget_call_is_outstanding_is_not_newer_than_the_forget(door, monkeypatch):
    """The interval the kernel cannot see: the forget has been SENT and not yet confirmed (a row- or turn-scoped forget holds no
    erase span, so the door can ask meanwhile). A stamp read then must be at most the forget's own, because the kernel refuses
    only what is not strictly newer than its mark; delivered after the forget commits, such an ask writes nothing (the kernel's
    half: ``labs/zoe-os/tests/test_lists_erase_order.py``, the equal-stamp test). The count moves on only at the kernel's
    confirmation. Control run: with the count moved BEFORE the kernel call the stamp read during it is newer and this fails."""
    answer = DoorKernel._answer
    during = []

    def held(self, conn, skill, req):
        if req["op"] == "forget":
            during.append((os_shadow.erase_stamp(OWNER), req["args"]["stamp"]))     # the call is outstanding right now
        return answer(self, conn, skill, req)

    monkeypatch.setattr(DoorKernel, "_answer", held)
    assert await os_shadow.forget_for_erase(OWNER) == 3
    (read, sent), = during
    assert read == sent
    assert os_shadow.erase_stamp(OWNER) > sent                    # confirmed: the member acting again is strictly newer


@pytest.mark.asyncio
async def test_a_forget_the_kernel_did_not_confirm_moves_nothing(door, monkeypatch):
    """A forget whose reply never arrives leaves the count where it was: later asks keep the stamp that (possibly committed)
    forget carried, so they read as erased until a replay is confirmed, never as newer. Control run: with the count moved in a
    ``finally`` this fails."""
    answer = DoorKernel._answer
    sent = []

    def lost(self, conn, skill, req):
        if req["op"] == "forget":
            sent.append(req["args"].get("stamp"))
            raise ConnectionError
        return answer(self, conn, skill, req)

    monkeypatch.setattr(DoorKernel, "_answer", lost)
    before = os_shadow.erase_stamp(OWNER)
    with pytest.raises(Exception):
        await os_shadow.forget_for_erase(OWNER)
    assert sent and sent[0] == before and os_shadow.erase_stamp(OWNER) == before


@pytest.mark.asyncio
async def test_nothing_but_a_confirmed_kernel_forget_moves_the_stamp(door, monkeypatch):
    """Other erasers advance the erase gate's generation at their own times. If that moved the stamp, an ask made while a forget
    call was held open could look strictly newer than that forget and be recorded after it. So: the gate's generation is not in
    the stamp. Control run: with the generation added back into ``erase_stamp`` the stamp read during the call is newer than
    the forget's and this fails."""
    answer = DoorKernel._answer
    during = []

    def held(self, conn, skill, req):
        if req["op"] == "forget":
            user_erase_gate._generation[OWNER] = user_erase_gate.generation(OWNER) + 1     # another eraser, mid-call
            during.append((os_shadow.erase_stamp(OWNER), req["args"]["stamp"]))
        return answer(self, conn, skill, req)

    monkeypatch.setattr(DoorKernel, "_answer", held)
    assert await os_shadow.forget_for_erase(OWNER) == 3
    (read, sent), = during
    assert read == sent                                           # an ask made then is refused wherever it lands (equal)
    assert os_shadow.erase_stamp(OWNER) == [sent[0], sent[1] + 1]


@pytest.mark.parametrize("value", [None, "off", "shadow"])
@pytest.mark.asyncio
async def test_with_lists_off_a_forget_carries_no_stamp_and_moves_nothing(door, monkeypatch, value):
    """With lists off no ask exists to order: the forget goes exactly as before (no ``stamp``, so the kernel writes no erase
    mark; the kernel's half: a stampless forget sets no mark, ``labs/zoe-os/tests/test_lists_erase_order.py``). Control run:
    with the stamp sent unconditionally this fails."""
    if value is None:
        monkeypatch.delenv("ZOE_OS_LISTS")
    else:
        monkeypatch.setenv("ZOE_OS_LISTS", value)
    before = os_shadow.erase_stamp(OWNER)
    assert await os_shadow.forget_for_erase(OWNER) == 3
    (forget,) = door.ops("forget")
    assert set(forget) == {"subject", "actor"} and os_shadow.erase_stamp(OWNER) == before


@pytest.mark.asyncio
async def test_an_owner_forgotten_while_the_door_looks_the_list_up_keeps_their_old_stamp(door):
    """The door reads the list's owner from the database before it asks. If the owner's forget is confirmed while that read is
    awaited, the ask must still carry the stamp from BEFORE the read (the kernel then refuses it: not strictly newer than the
    mark). Control run: with the stamp read after the lookup the ask carries the moved-on stamp and this fails."""
    class SlowLookup(TxDB):
        async def fetch(self, sql, *args):
            rows = await super().fetch(sql, *args)
            assert await os_shadow.forget_for_erase(OWNER) == 3   # the owner is forgotten, and confirmed, mid-lookup
            return rows

    before = os_shadow.erase_stamp(OWNER)
    await DOORS["remove_item_record"][1](SlowLookup(items=[{"id": "I1", "text": "wombel", "completed": 0}]), KID)
    (forget,) = door.ops("forget")
    asked = door.ops("list_ask")[-1]["stamps"]
    assert asked[OWNER] == before == forget["stamp"] and os_shadow.erase_stamp(OWNER) > before


@pytest.mark.asyncio
async def test_a_malformed_forget_reply_is_not_a_confirmation_and_moves_nothing(door, monkeypatch):
    """The count moves on only once the reply is the documented one (``forget_ack``): a "success" that is not is unconfirmed to
    the caller, so it must be unconfirmed to the stamp too. Control run: with the count moved when the call returns this fails."""
    from test_os_shadow import _send

    answer = DoorKernel._answer

    def malformed(self, conn, skill, req):
        if req["op"] == "forget":
            self.calls.append((skill, req["op"], req["args"]))
            return _send(conn, {"v": 1, "id": req["id"], "ok": True, "result": {"count": "three"}})
        return answer(self, conn, skill, req)

    monkeypatch.setattr(DoorKernel, "_answer", malformed)
    before = os_shadow.erase_stamp(OWNER)
    with pytest.raises(Exception):
        await os_shadow.forget_for_erase(OWNER)
    assert door.ops("forget") and os_shadow.erase_stamp(OWNER) == before


@pytest.mark.asyncio
async def test_an_ask_made_while_a_member_erase_runs_is_never_queued(door, caplog):
    """Read with the stamps, on the loop: the door thread's own look could come after the erase has ended. Control run: without
    the loop-side check the ask is queued (it reaches the door thread, which may or may not still see the erase)."""
    def never(*a, **k):
        raise AssertionError("queued while the member was being erased")

    with user_erase_gate.erase_span(OWNER):
        real = os_shadow._door_q.put_nowait
        os_shadow._door_q.put_nowait = never
        try:
            await _proceeds(door, caplog, "remove_item_record", "erasing")
        finally:
            os_shadow._door_q.put_nowait = real
    assert door.ops("list_ask") == []


@pytest.mark.asyncio
async def test_an_entity_or_topic_forget_carries_no_stamp_and_its_whole_member_fallback_does(door):
    """A scoped forget sets no erase mark in the kernel, so it sends none; the over-forget it falls back to is whole-member and is
    stamped. Control run: with ``_forget_call`` stamping scoped forgets too the first assert fails."""
    assert await os_shadow.erase_entity(OWNER, NAME) == 3 and await os_shadow.erase_topic(OWNER, WORD) == 3
    assert [set(a) for a in door.ops("forget")] == [{"subject", "actor", "entity"}, {"subject", "actor", "topic"}]
    door.forget_mode = "refuse_scoped"
    assert await os_shadow.erase_entity(OWNER, NAME) == 3
    assert _is_stamp(door.ops("forget")[-1]["stamp"]) and "entity" not in door.ops("forget")[-1]


@pytest.mark.asyncio
async def test_a_kernel_from_before_the_stamp_still_forgets(door, monkeypatch):
    """A forget never waits for a kernel restart: a kernel that answers ``bad_request`` to the stamped forget (it does not know
    the argument) is sent the forget as before. Control run: without the fallback in ``_forget_call`` the forget raises
    ``ShadowForgetUnavailable`` and is parked."""
    answer = DoorKernel._answer

    def old_kernel(self, conn, skill, req):
        if req["op"] == "forget" and "stamp" in req["args"]:
            from test_os_shadow import _send
            return _send(conn, {"v": 1, "id": req["id"], "ok": False, "error": {"code": "bad_request", "reason": "unknown argument"}})
        return answer(self, conn, skill, req)

    monkeypatch.setattr(DoorKernel, "_answer", old_kernel)
    assert await os_shadow.forget_user(OWNER) == 3
    assert ["stamp" in a for a in door.ops("forget")] == [True, False]
    assert not (door.state / "pending-forgets.json").exists()


@pytest.mark.parametrize("name", ["remove_item_record", "add_items_to_list", "delete_list_record"])
@pytest.mark.asyncio
async def test_the_kernels_erased_answer_is_not_a_refusal_the_change_proceeds_not_checked(door, caplog, name):
    """The kernel found the ask older than a forget of a member it names and recorded nothing. That is not "no": the change is
    made, one ``not_checked reason=erased`` line, nothing is reported, and NOTHING compensates on this side (no parked forget: the
    kernel's ordering is the one mechanism). Control run: with the ``erased`` branch removed from ``_door_call`` this raises
    ``ListRefused`` and writes nothing."""
    door.door = "refuse:erased"
    lines = await _proceeds(door, caplog, name, "erased")
    time.sleep(0.05)
    assert door.ops("list_report") == [] and door.ops("forget") == []
    assert not (door.state / "pending-forgets.json").exists() and os_shadow._q.empty()
    assert not any(w in " ".join(lines) for w in (OWNER, "L1", WORD))


@pytest.mark.asyncio
async def test_a_stale_report_is_dropped_quietly_and_the_door_keeps_working(door, caplog, monkeypatch):
    """The kernel refuses a report whose stamps are older than a forget; the door thread swallows it (no line names anyone) and
    serves the next ask on the same connection. Control run: with the door loop's ``except`` narrowed to ``OSError`` the door
    thread dies on the refusal and the second ask is ``not_checked``."""
    answer = DoorKernel._answer

    def stale_reports(self, conn, skill, req):
        if req["op"] == "list_report":
            return self._refuse(conn, req, "erased")
        return answer(self, conn, skill, req)

    monkeypatch.setattr(DoorKernel, "_answer", stale_reports)
    caplog.set_level("DEBUG", logger="os_shadow")
    await DOORS["remove_item_record"][1](_db(), OWNER)
    door.reports(1)
    await DOORS["remove_item_record"][1](_db(), OWNER)
    assert len(door.ops("list_ask")) == 2 and door.connections == 1
    assert caplog.text.count("decision=allow") == 2 and OWNER not in caplog.text


@pytest.mark.asyncio
async def test_a_list_lookup_that_fails_raises_and_never_skips_the_kernel(door):
    """Only the kernel being down or slow lets a change proceed unasked; a database fault at the door's own read is the caller's
    error (the write shares that connection), never a quiet skip of a refusal."""
    class Broken(DB):
        async def fetch(self, sql, *args):
            raise RuntimeError("database fault")

    db = Broken()
    with pytest.raises(RuntimeError):
        await DOORS["remove_item_record"][1](db, OWNER)
    assert not db.writes and door.ops("list_ask") == []


@pytest.mark.asyncio
async def test_a_list_that_is_not_there_or_a_caller_who_did_not_say_who_is_not_checked(door, caplog):
    class NoList(DB):
        async def fetch(self, sql, *args):
            return []

    caplog.set_level("INFO", logger="os_shadow")
    await DOORS["remove_item_record"][1](NoList(), OWNER)
    await DOORS["create_item_record"][1](DB(), None)             # a caller that did not say who
    assert door.ops("list_ask") == []
    assert [m.split("reason=")[1].split()[0] for m in (r.getMessage() for r in caplog.records) if "ZOE_OS_ACT" in m] == ["no_list", "no_actor"]


# ---- every door asks ------------------------------------------------------------------------------------------------------------------
def unasked_writers(source: str) -> list:
    """Functions that hold a write to ``lists`` / ``list_items`` and never call ``_gate``."""
    from test_lists_one_door import WRITE

    out = []
    for fn in (n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        own = [n for n in ast.walk(fn)]
        strings = [n.value for n in own if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        doc = ast.get_docstring(fn) or ""
        if any(WRITE.search(s) for s in strings if s.strip() != doc.strip()) and not any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_gate" for n in own):
            out.append(fn.name)
    return out


def test_every_function_that_writes_a_list_table_asks_the_kernel():
    source = (SVC / "list_service.py").read_text("utf-8")
    assert unasked_writers(source) == []
    writers = {fn.name for fn in ast.walk(ast.parse(source)) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
               and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_gate" for n in ast.walk(fn))}
    assert writers == set(DOORS) - {"add_items_to_list"}        # and each of them is exercised above (add goes through create_item_record)


def test_the_scan_catches_a_planted_writer_that_does_not_ask():
    assert unasked_writers("async def quiet(db):\n    await db.execute('UPDATE list_items SET text = ? WHERE id = ?')\n") == ["quiet"]
    assert unasked_writers("async def asks(db):\n    await _gate(db, 'item.edit', user_id='u')\n"
                           "    await db.execute('UPDATE list_items SET text = ? WHERE id = ?')\n") == []


def test_every_item_add_outside_the_door_says_who_is_adding():
    offenders = []
    for path in SVC.rglob("*.py"):
        rel = path.relative_to(SVC)
        if {"tests", "alembic"} & set(rel.parts) or ".venv" in rel.parts:
            continue
        for call in (n for n in ast.walk(ast.parse(path.read_text("utf-8"))) if isinstance(n, ast.Call)):
            if getattr(call.func, "id", getattr(call.func, "attr", "")) == "create_item_record" and \
                    not any(k.arg == "user_id" for k in call.keywords):
                offenders.append(f"{rel}:{call.lineno}")
    assert offenders == []


# ---- the watch-only hooks are silent while the door is on ------------------------------------------------------------------------------
def test_the_list_shadow_hooks_do_nothing_while_the_door_is_on_and_other_verbs_still_shadow(door, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "list_add,list_remove,reminder_create")
    assert os_shadow.revive()
    os_shadow.note_add(OWNER, source="rest", list_type="shopping", list_name="Shopping", item=WORD)
    os_shadow.note_list_remove(OWNER, source="rest", list_type="shopping", list_name="Shopping", item=WORD)
    assert not os_shadow.wants("list_add") and not os_shadow.wants("list_remove") and os_shadow.wants("reminder_create")
    time.sleep(0.1)
    assert os_shadow.counters() == {} and door.calls == []      # no line, no count, nothing sent: no double recording
    monkeypatch.setenv("ZOE_OS_LISTS", "off")                   # the control: the same hook does record with the door off
    os_shadow.note_add(OWNER, source="rest", list_type="shopping", list_name="Shopping", item=WORD)
    end = time.monotonic() + 3
    while time.monotonic() < end and not os_shadow.counters():
        time.sleep(0.01)
    assert sum(os_shadow.counters().values()) == 1


# ---- each caller's refusal ----------------------------------------------------------------------------------------------------------------
LINE = list_service.refusal_line("not_owner")


async def _refuse(*a, **k):
    raise list_service.ListRefused("not_owner")


async def _noop(*a, **k):
    return None


def test_the_refusal_sentence_is_plain_and_carries_no_code():
    for reason in (*os_shadow.DOOR_REFUSALS, "other", "anything else"):
        line = list_service.refusal_line(reason)
        assert line and "_" not in line and reason not in line.split()


def test_rest_answers_a_refusal_with_403_and_the_plain_sentence(door, monkeypatch):
    """Through the real route and the real app machinery: the handler ``main.py`` registers turns the door's refusal into a 403."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import lists

    async def actor(*a, **k):
        return False, KID

    class Row(DB):
        async def execute(self, sql, *params):
            await super().execute(sql, *params)
            return _Cur([{"id": "I1", "user_id": KID}])

    door.door = "refuse:not_owner"
    monkeypatch.setattr(lists, "_item_actor", actor)
    for name in ("_require_item_feature", "_require_list_item_mutation_access", "require_feature_access"):
        monkeypatch.setattr(lists, name, _noop)
    monkeypatch.setattr(lists.broadcaster, "broadcast", _noop)
    db = Row()
    app = FastAPI()
    app.include_router(lists.router)
    app.add_exception_handler(lists.ListRefused, lists.list_refused_response)
    app.dependency_overrides[lists.get_current_user] = lambda: {"user_id": KID, "role": "member"}
    app.dependency_overrides[lists.get_db] = lambda: db
    with TestClient(app) as client:
        for path in ("/api/lists/shopping/L1/items/I1", "/api/lists/shopping/L1"):
            out = client.delete(path)
            assert out.status_code == 403 and out.json() == {"detail": LINE}, path
    assert db.writes == []


def test_main_registers_the_refusal_handler_for_the_whole_app():
    tree = ast.parse((SVC / "main.py").read_text("utf-8"))
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "add_exception_handler"]
    assert [[getattr(a, "id", "") for a in c.args] for c in calls] == [["_ListRefused", "_list_refused_response"]]
    imported = {a.asname: a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "routers.lists" for a in n.names}
    assert imported == {"_ListRefused": "ListRefused", "_list_refused_response": "list_refused_response"}


@pytest.mark.asyncio
async def test_the_spoken_add_remove_and_rename_say_the_refusal_instead_of_falling_through(door, monkeypatch):
    import intent_router
    from test_list_direct_execution import _FakeListDB, _fake_db_ctx

    class Spoken(_FakeListDB):
        async def fetch(self, sql, *args):
            if "FROM list_items" in sql and "list_id = $1" in sql:
                return [{"id": "I1", "text": "wombel", "completed": 0}]
            return [{"id": "L1", "user_id": OWNER, "visibility": "family"}]

    door.door = "refuse:not_owner"
    monkeypatch.setattr(intent_router, "_notify_lists_ui", _noop)
    for intent, rows in ((intent_router.Intent("list_add", {"item": WORD, "list_type": "shopping"}), [{"id": "L1"}, None]),
                         (intent_router.Intent("list_remove", {"item": WORD, "list_type": "shopping"}),
                          [{"id": "I1", "list_id": "L1", "text": WORD}]),
                         (intent_router.Intent("list_rename", {"item": "wombel", "new_item": WORD, "list_type": "shopping"}),
                          [{"id": "L1"}])):
        db = Spoken(rows)
        monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
        run = {"list_add": intent_router._execute_list_add_direct, "list_remove": intent_router._execute_list_remove_direct,
               "list_rename": intent_router._execute_list_rename_direct}[intent.name]
        assert await run(intent, KID) == LINE, intent.name
        assert not [s for s, _ in db.calls if s.split()[0].upper() in ("INSERT", "UPDATE")], intent.name


@pytest.mark.asyncio
async def test_the_brain_tool_returns_an_error_result_for_a_refusal(monkeypatch):
    import mcp_server

    async def actor(*a, **k):
        return {"user_id": KID}

    class Found(DB):
        async def execute(self, sql, *params):
            await super().execute(sql, *params)
            return _Cur([{"id": "I1", "list_id": "L1"}])

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    monkeypatch.setattr(mcp_server, "_notify_ui", _noop)
    monkeypatch.setattr(mcp_server, "set_item_checked", _refuse)
    out = await mcp_server._execute_tool(Found(), "list_remove_item", {"item_text": WORD, "list_type": "shopping"})
    assert out == {"error": LINE + " Nothing was removed.", "status": "refused"}
    monkeypatch.setattr(list_service, "add_items_to_list", _refuse)
    out = await mcp_server._execute_tool(Found(), "list_add_item", {"text": WORD, "list_type": "shopping"})
    assert out == {"error": LINE + " Nothing was added.", "status": "refused"}


@pytest.mark.asyncio
async def test_skybridge_answers_a_refusal_in_its_own_shape_and_a_refused_seed_never_breaks_a_read(monkeypatch):
    import skybridge_service as sky

    class Card:
        async def fetch(self, sql, *args):
            if "FROM lists" in sql and "WHERE id = $1" in sql:
                return [{"id": "L9", "name": NAME, "list_type": "personal"}]
            if "FROM list_items" in sql:
                return [{"id": "I1", "list_id": "L9", "text": WORD, "completed": 0}]
            return []

        async def execute(self, sql, *args):
            return "UPDATE 1"

    monkeypatch.setattr(sky, "_maybe_commit", _noop)
    monkeypatch.setattr(sky, "remove_item_record", _refuse)
    context = {"cards": [{"content": {"source": "list_show", "list_id": "L9", "list_type": "personal"}}]}
    intent = sky.SkybridgeIntent(domain="lists", action="remove_item", item_text=WORD)
    out = await sky._resolve_with_db(intent, KID, Card(), context=context)
    assert out["handled"] is True and out["spoken_summary"] == LINE and out["actions"] == []
    assert out["intent"] == {"domain": "lists", "action": "remove_item", "list_type": intent.list_type, "status": "refused"}
    assert out["cards"][0]["component"] == "status" and out["cards"][0]["props"]["body"] == LINE
    monkeypatch.setattr(sky, "create_list_record", _refuse)
    await sky._ensure_default_lists(KID, Card())                 # seeding is refused: the read path carries on


@pytest.mark.asyncio
async def test_a_slow_door_start_is_inside_the_ask_deadline(door, monkeypatch):
    """The first ask (or one after the worker died) waits for the door to start; that wait draws on the same budget."""
    monkeypatch.setenv("ZOE_OS_LISTS_ASK_MS", "100")
    monkeypatch.setattr(os_shadow, "_door_worker", None)
    monkeypatch.setattr(os_shadow, "_ensure_door", lambda: time.sleep(0.6))
    started = time.monotonic()
    out = await os_shadow.ask_list("item.add", OWNER, list_id="L1", household=True)
    assert out[:2] == (os_shadow.NOT_CHECKED, "timeout") and time.monotonic() - started < 0.4


@pytest.mark.asyncio
async def test_an_accepted_suggestion_reports_only_after_its_own_transaction_ends(door, monkeypatch):
    """``pending_suggestions.execute_suggestion`` holds its own transaction around the list write: the kernel hears ``done`` after
    that commit, and ``failed`` (never ``done``) when the commit fails. Control: without ``kernel_reports_after`` around that
    transaction the failed commit is reported ``done``."""
    import pending_suggestions
    from test_os_shadow_hook import MEMBER, _Pool
    from test_os_shadow_hook import _Conn as _Base

    class _Conn(_Base):
        async def fetch(self, sql, *args):                         # the door's own read of the list row
            return [{"id": "L1", "user_id": MEMBER, "visibility": "family"}]

    monkeypatch.setenv("ZOE_ANTICIPATION", "off")
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: _Pool(_Conn(fail_commit=True)))
    assert not (await pending_suggestions.execute_suggestion("s2", MEMBER))["ok"]
    door.reports(1)
    assert [r["outcome"] for r in door.ops("list_report")] == ["failed"] * len(door.ops("list_report")) and door.ops("list_report")
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: _Pool(_Conn()))
    assert (await pending_suggestions.execute_suggestion("s1", MEMBER))["ok"]
    door.reports(len(door.ops("list_ask")))
    assert [r["outcome"] for r in door.ops("list_report")].count("done") >= 1


@pytest.mark.parametrize("value", [None, "off", "shadow"])
@pytest.mark.asyncio
async def test_with_lists_off_an_accepted_suggestion_enters_nothing_of_the_door(monkeypatch, value):
    """Flag off: the suggestion path reads the flag once and never enters ``kernel_reports_after`` or asks. Control: with the
    hold entered unconditionally this fails."""
    import pending_suggestions
    from test_os_shadow_hook import MEMBER, _Conn, _Pool

    def never(*a, **k):
        raise AssertionError("the door was entered with the flag off")

    if value is None:
        monkeypatch.delenv("ZOE_OS_LISTS", raising=False)
    else:
        monkeypatch.setenv("ZOE_OS_LISTS", value)
    monkeypatch.setenv("ZOE_ANTICIPATION", "off")
    monkeypatch.setattr(list_service, "kernel_reports_after", never)
    monkeypatch.setattr(os_shadow, "ask_list", never)
    conn = _Conn()
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: _Pool(conn))
    assert (await pending_suggestions.execute_suggestion("s1", MEMBER))["ok"] and "INSERT INTO list_items" in conn.writes
