"""The door is ONE mechanism shared by every area (``os_shadow`` door section + ``kernel_door``); lists are its first area.

What this file pins is the area-neutral half, with a TOY area planted in ``os_shadow.AREAS`` (the lists' own behaviour is
``test_lists_kernel_door.py``, unchanged):

* a later area asks on the neutral wire ops (``door_ask`` / ``door_report``) with ``area`` and ``target``; ids, stamps and closed
  codes only cross the socket and reach the log; the lists keep ``list_ask`` / ``list_report``;
* one queue, one thread, one connection for every area;
* a report goes out only after the caller's own transaction ended (``kernel_door.reports_after``), whatever the area;
* a whole-member forget carries the erase stamp while ANY door is on, and none when every door is off;
* the log line's ``area`` and ``verb`` are clamped to their closed sets;
* one app-level 403 handler covers every area's refusal.

Invented ids and words only (``member-ova``, ``toy_bar_0001``, "womble").
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

import kernel_door
import os_shadow
import user_erase_gate
from test_lists_kernel_door import DoorKernel

pytestmark = pytest.mark.ci_safe

OWNER, KID = "member-ova", "member-kip"
TOY_FLAG = "ZOE_OS_LISTS"          # the toy area borrows a registered flag name: it is on exactly when that reads ``kernel``
TOY = os_shadow._Area(TOY_FLAG, None, ("toy.make", "toy.break"), frozenset(), "door_ask", "door_report", "target", None, True)


class AreaKernel(DoorKernel):
    """Answers the neutral ops exactly as ``DoorKernel`` answers the lists' spelling (``calls`` keeps the op as sent)."""

    def _answer(self, conn, skill, req):
        neutral = {"door_ask": "list_ask", "door_report": "list_report"}
        return super()._answer(conn, skill, {**req, "op": neutral.get(req["op"], req["op"])})


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [OWNER, KID]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LISTS", "kernel")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    monkeypatch.setitem(os_shadow.AREAS, "toys", TOY)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    yield k
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


@pytest.mark.asyncio
async def test_a_later_area_asks_on_the_neutral_ops_with_ids_and_stamps_only(door, caplog):
    """Control run: with ``args["area"] = area`` dropped from ``os_shadow._ask`` the ``area`` assertion fails; with the ask op
    hard-coded to ``list_ask`` in ``_door_call`` no ``door_ask`` is seen."""
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        out = await os_shadow.ask_door("toys", "toy.make", OWNER, target="toy_bar_0001", owner=KID, household=True)
    assert out[0] == os_shadow.ALLOW and set(out[3]) == {OWNER, KID}
    (ask,) = door.ops("door_ask")
    assert ask == {"area": "toys", "verb": "toy.make", "person": OWNER, "target": "toy_bar_0001", "owner": KID,
                   "visibility": "household", "stamps": {OWNER: os_shadow.erase_stamp(OWNER), KID: os_shadow.erase_stamp(KID)}}
    assert door.ops("list_ask") == []
    os_shadow.report_door("toys", out[2], "done", out[3])
    door.reports(0)
    for _ in range(300):
        if door.ops("door_report"):
            break
        threading.Event().wait(0.01)
    assert door.ops("door_report") == [{"ask": out[2], "outcome": "done", "stamps": out[3]}] and door.ops("list_report") == []
    lines = [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]
    assert len(lines) == 1 and lines[0].startswith("ZOE_OS_ACT area=toys verb=toy.make decision=allow reason=- ask_ms=")
    assert "toy_bar_0001" not in lines[0] and OWNER not in lines[0]


@pytest.mark.asyncio
async def test_every_area_shares_one_thread_and_one_connection(door):
    """Control run: with the door thread dropping its connection after each ask, ``connections`` is 2."""
    await os_shadow.ask_list("item.add", OWNER, list_id="L1", owner=OWNER, household=True)
    await os_shadow.ask_door("toys", "toy.make", OWNER, target="toy_bar_0001", owner=OWNER)
    assert [op for _, op, _ in door.calls] == ["list_ask", "door_ask"] and door.connections == 1
    assert [t.name for t in threading.enumerate() if t.name.startswith("os-door")] == ["os-door"]


@pytest.mark.asyncio
async def test_a_verb_of_another_area_and_an_unknown_area_are_never_sent(door, caplog):
    """Control run: with the ``verb not in spec.verbs`` check removed from ``_ask`` the cross-area ask reaches the kernel."""
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        assert (await os_shadow.ask_door("toys", "item.add", OWNER, target="t"))[:2] == (os_shadow.NOT_CHECKED, "bad_verb")
        assert (await os_shadow.ask_door("womble", "toy.make", OWNER, target="t"))[:2] == (os_shadow.NOT_CHECKED, "bad_verb")
    assert door.calls == []
    lines = [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]
    assert lines[0].startswith("ZOE_OS_ACT area=toys verb=unknown ") and lines[1].startswith("ZOE_OS_ACT area=unknown verb=unknown ")
    assert "womble" not in "".join(lines)                          # a caller's word for an area never reaches the log


@pytest.mark.asyncio
async def test_a_report_waits_for_the_callers_transaction_whatever_the_area(door, monkeypatch):
    """Control run: with ``held.append`` replaced by an immediate report in ``kernel_door.wrote`` the first assertion fails."""
    seen = []
    monkeypatch.setattr(os_shadow, "report_door", lambda area, ask, outcome, stamps=None: seen.append((area, outcome)))
    allowed = kernel_door.Decision("allow", "", 7, area="toys")
    async with kernel_door.reports_after():
        kernel_door.wrote(allowed)
        assert seen == []                                           # still inside the transaction: nothing reported yet
    assert seen == [("toys", "done")]
    failed = kernel_door.Decision("allow", "", 8, area="toys")
    with pytest.raises(RuntimeError):
        async with kernel_door.reports_after():
            kernel_door.wrote(failed)
            raise RuntimeError("rolled back")
    assert seen == [("toys", "done"), ("toys", "failed")]
    kernel_door.report(failed, "done")                              # once per ask
    kernel_door.wrote(kernel_door.Decision("not_checked", "timeout", None, area="toys"))    # nothing to close
    assert len(seen) == 2


@pytest.mark.parametrize("lists_flag,stamped", [("kernel", True), ("off", False)])
def test_a_forget_carries_the_erase_stamp_while_any_door_is_on(door, monkeypatch, lists_flag, stamped):
    """The toy area's flag is the lists flag here, so "every door off" is ``off``. Control run: with ``doors_on()`` replaced by
    ``False`` in ``_forget_call`` the stamped case fails."""
    monkeypatch.setenv("ZOE_OS_LISTS", lists_flag)
    assert os_shadow.doors_on() is stamped
    sent = []

    class _K:
        def call(self, skill, op, *a, **kw):
            sent.append(kw)
            return {"count": 0, "upto": 0}

    os_shadow._forget_call(_K(), OWNER, {})
    assert ("stamp" in sent[0]) is stamped


def test_one_handler_turns_every_areas_refusal_into_a_403_with_its_own_sentence():
    """Control run: with ``ListRefused`` made a plain ``Exception`` again (not a ``kernel_door.Refused``), the one handler no
    longer covers it and this fails."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import list_service

    class ToyRefused(kernel_door.Refused):
        area, lines = "toys", {"not_owner": "That toy is someone else's."}

    app = FastAPI()
    app.add_exception_handler(kernel_door.Refused, kernel_door.refused_response)

    @app.get("/toy/{reason}")
    async def toy(reason: str):
        raise ToyRefused(reason)

    @app.get("/list")
    async def a_list():
        raise list_service.ListRefused("not_owner")

    with TestClient(app) as client:
        assert (client.get("/toy/not_owner").status_code, client.get("/toy/not_owner").json()) == (403, {"detail": "That toy is someone else's."})
        assert client.get("/toy/womble").json() == {"detail": "I'm not allowed to change that."}      # an unknown code: the fallback
        assert client.get("/list").json() == {"detail": list_service.refusal_line("not_owner")}
