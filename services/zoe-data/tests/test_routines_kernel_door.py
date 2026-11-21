"""``ZOE_OS_ROUTINES=kernel``: routines go to the Zoe OS kernel, inside their one writer (``routines_store.py``;
``routine_door.py``). Default off.

  * flag off (any value but ``kernel``) = ``main`` exactly: every scenario of ``tests/routines_scenarios.py`` gives the record
    of main and the kernel is never reached;
  * door on and the kernel allowing = the same record;
  * creating a routine, adding a step and adding a one-off ask FIRST; a refusal is final (nothing written); a kernel that is
    down, old or slow = the change proceeds, ``not_checked``;
  * a tick, skip, snooze or undo is MADE FIRST and told afterwards whatever the kernel does; the store's own rules (a
    medicine line needs its verified subject) still refuse before anything is told;
  * a routine someone else set up for its subject is asked about with the ``_given`` verb;
  * only ids and closed codes cross: never a routine's name or a step's words.

Each test names the control that was run against it. Invented people and words only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import pytest

import kernel_door
import os_shadow
import routine_door
import routines_scenarios as scenarios
import user_erase_gate
from routines_scenarios import ALEX, RILEY, SAM
from test_kernel_door import AreaKernel

pytestmark = pytest.mark.ci_safe
SVC = Path(__file__).resolve().parents[1]
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "routines_as_on_main.json").read_text("utf-8"))
WORDS = ("Morning", "brush", "teeth", "pack", "bag", "drill", "cat")


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ALEX, SAM, RILEY]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_ROUTINES", "kernel")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    yield k
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


@pytest.fixture
def world(tmp_path):
    w = scenarios.World(str(tmp_path))
    yield w
    w.close()


def _asks(door, n=0, timeout=2.0) -> list:
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(door.ops("door_ask")) < n:
        time.sleep(0.01)
    return door.ops("door_ask")


def _never(*a, **k):
    raise AssertionError("the kernel was reached")


def _tables(w) -> list:
    return [w.db.q(f"SELECT COUNT(*) FROM {t}")[0][0] for t in scenarios.TABLES]


# ---- flag off; door on and allowing ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "off", "shadow"])
def test_any_other_flag_value_does_exactly_what_main_did(door, monkeypatch, value):
    """Control run: with the flag check dropped from ``routine_door.told`` the stub raises on the first tick."""
    monkeypatch.delenv("ZOE_OS_ROUTINES") if value is None else monkeypatch.setenv("ZOE_OS_ROUTINES", value)
    monkeypatch.setattr(os_shadow, "ask_door", _never)
    monkeypatch.setattr(os_shadow, "record_door", _never)
    assert scenarios.run_all() == ON_MAIN
    assert door.ops("door_ask") == []


def test_with_the_door_on_and_allowing_every_entry_point_does_exactly_what_main_did(door):
    """Control run: with ``add_step`` writing its row twice when asked, the add-step scenarios gain a row."""
    assert scenarios.run_all() == ON_MAIN
    assert {a["verb"] for a in door.ops("door_ask")} == {"routine.create", "routine.edit", "routine.edit_given", "routine.tick",
                                                        "routine.skip", "routine.snooze", "routine.undo"}


# ---- asked first -----------------------------------------------------------------------------------------------------------------
async def test_creating_and_changing_a_routine_ask_first_with_ids_only_and_report_done(door, world, caplog):
    """Control run: with the ask dropped from ``create_routine`` no create is asked; with the routine's name sent as the
    target the words assertion fails."""
    rs = world.rs
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        mine = await rs.create_routine(ALEX, "Morning", "waking", steps=("brush teeth",))
        kids = await rs.create_routine(RILEY, "Morning", "waking", created_by=ALEX, visibility="personal", steps=("pack the bag",))
        house = await rs.create_routine(ALEX, "Morning", "waking", visibility="family")
        await rs.add_step(ALEX, mine["id"], "feed the cat")
        await rs.add_one_off(RILEY, kids["id"], world.today + timedelta(days=1), "take the drill")
        await rs.add_step(SAM, house["id"], "feed the cat")
    asks = _asks(door, 6)
    assert [(a["verb"], a["person"], a["owner"], a["target"], a.get("visibility")) for a in asks] == [
        ("routine.create", ALEX, ALEX, mine["id"], "private"), ("routine.create", ALEX, RILEY, kids["id"], "private"),
        ("routine.create", ALEX, ALEX, house["id"], "household"), ("routine.edit", ALEX, ALEX, mine["id"], "private"),
        ("routine.edit_given", RILEY, RILEY, kids["id"], "private"), ("routine.edit", SAM, ALEX, house["id"], "household")]
    assert all(set(a) <= {"area", "verb", "person", "target", "owner", "visibility", "stamps"} for a in asks)
    sent = json.dumps(door.calls, default=str) + " ".join(r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage())
    assert not [word for word in WORDS if word.lower() in sent.lower()]
    end = time.monotonic() + 2
    while time.monotonic() < end and len(door.ops("door_report")) < 6:
        await asyncio.sleep(0.02)
    assert [r["outcome"] for r in door.ops("door_report")] == ["done"] * 6


@pytest.mark.parametrize("reason", ["adults_only", "not_owner", "unknown_person"])
async def test_a_refused_change_writes_nothing_and_the_spoken_one_off_says_why(door, world, reason):
    """A kernel "no" is final. Control run: with the refusal not raised (``kernel_door.gate`` returning on refuse) the refused
    routine is created."""
    rs = world.rs
    mine = await rs.create_routine(RILEY, "Morning", "waking", created_by=ALEX, steps=("brush teeth",))
    before = _tables(world)
    door.door = f"refuse:{reason}"
    for change in (lambda: rs.create_routine(ALEX, "Morning", "waking"), lambda: rs.add_step(RILEY, mine["id"], "feed the cat"),
                   lambda: rs.add_one_off(RILEY, mine["id"], world.today + timedelta(days=1), "take the drill")):
        with pytest.raises(routine_door.RoutineRefused) as exc:
            await change()
        assert exc.value.reason == reason and isinstance(exc.value, kernel_door.Refused)
    assert _tables(world) == before
    await world.say("remind me to take the drill to work tomorrow", RILEY)
    assert world.log[-1][2] == routine_door.RoutineRefused(reason).line() and _tables(world) == before


@pytest.mark.parametrize("how", ["slow", "gone", "old_kernel"])
async def test_a_change_proceeds_not_checked_when_the_kernel_cannot_answer(door, world, caplog, how):
    """Control run: with ``not_checked`` treated as a refusal in ``kernel_door.gate`` nothing is created."""
    if how == "slow":
        door.delay = 0.8
    elif how == "gone":
        door.close()
    else:
        door.door = "old_kernel"
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        made = await world.rs.create_routine(ALEX, "Morning", "waking", steps=("brush teeth",))
        await asyncio.sleep(0.2)
    assert made is not None and _tables(world)[:2] == [1, 1]
    lines = [r.getMessage() for r in caplog.records if "ZOE_OS_ACT" in r.getMessage()]
    assert lines and "area=routines" in lines[0] and "verb=routine.create" in lines[0] and "decision=not_checked" in lines[0]


# ---- told afterwards -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kernel", ["allow", "refuse:not_owner", "slow", "gone"])
async def test_a_tick_is_made_at_once_whatever_the_kernel_does_and_told_afterwards(door, world, caplog, kernel):
    """Tick, skip, snooze and undo never wait and are never stopped: a kernel "no" only makes the record say objected.
    Control run: with the tell dropped from ``_apply`` nothing is told and this fails."""
    rs = world.rs
    r = await rs.create_routine(ALEX, "Morning", "waking", steps=("brush teeth", "pack the bag", "feed the cat"))
    d = await rs.day_copy(ALEX, r["id"])
    before = len(_asks(door, 1))
    if kernel == "slow":
        door.delay = 0.8
    elif kernel == "gone":
        door.close()
    else:
        door.door = kernel
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        started = time.monotonic()
        out = [(await rs.act_tapped(ALEX, d["steps"][i]["id"], action, verified=False))["state"]
               for i, action in ((0, "done"), (1, "skip"), (2, "snooze"), (0, "undo"))]
        assert time.monotonic() - started < 0.5 and out == ["done", "skipped", "snoozed", "pending"]
        if kernel in ("allow", "refuse:not_owner"):
            told = _asks(door, before + 4)[before:]
            assert [(a["verb"], a["person"], a["owner"], a["target"]) for a in told] == [
                (v, ALEX, ALEX, r["id"]) for v in ("routine.tick", "routine.skip", "routine.snooze", "routine.undo")]
            await asyncio.sleep(0.3)
            want = "recorded" if kernel == "allow" else "objected"
            lines = [x.getMessage() for x in caplog.records if "ZOE_OS_ACT" in x.getMessage() and "verb=routine.tick" in x.getMessage()]
            assert lines and f"decision={want}" in lines[0], lines


async def test_the_stores_own_rules_refuse_before_anything_is_told_and_the_panel_ticks_as_the_panel(door, world):
    """A medicine line unverified, someone else's personal line: refused by the store exactly as on main, and the kernel hears
    nothing. The bare panel's tick of a family line is told as the panel. Control run: with ``told`` called before the
    store's ``can_tick`` check the refused tick is told."""
    rs = world.rs
    med = await rs.create_routine(ALEX, "Morning", "waking", steps=(("take the tablets", "medicine"),))
    house = await rs.create_routine(ALEX, "Morning", "waking", visibility="family", steps=("feed the cat",))
    d, h = await rs.day_copy(ALEX, med["id"]), await rs.day_copy(None, house["id"])
    before = len(_asks(door, 2))
    for actor in (ALEX, SAM):
        with pytest.raises(rs.StoreError):
            await rs.act_tapped(actor, d["steps"][0]["id"], "done", verified=False)
    await asyncio.sleep(0.2)
    assert len(door.ops("door_ask")) == before
    assert (await rs.act_tapped(None, h["steps"][0]["id"], "done", verified=False))["state"] == "done"
    told = _asks(door, before + 1)[-1]
    assert told["verb"] == "routine.tick" and told.get("via") == "panel" and told["person"] == "panel" and told["owner"] == ALEX


def test_the_door_sits_in_the_one_writer_and_every_entry_point_takes_its_stamps_first():
    """Control run: with ``stamps_at = routine_door.stamps_now()`` moved after ``_load`` in ``add_step`` this fails."""
    import ast

    src = (SVC / "routines_store.py").read_text("utf-8")
    tree = ast.parse(src)
    for name in ("create_routine", "add_step", "add_one_off", "_apply"):
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant) else fn.body
        assert ast.unparse(body[0]) == "stamps_at = routine_door.stamps_now()", name
        text = ast.unparse(fn)
        assert ("routine_door.ask(" in text) != (name == "_apply") and ("routine_door.told(" in text) == (name == "_apply"), name
    for rel in ("routines_tier.py", "fast_tiers.py"):
        assert "routine_door" not in (SVC / rel).read_text("utf-8"), rel
    assert tuple(os_shadow.AREAS["routines"].verbs)[:3] == (routine_door.CREATE, routine_door.EDIT, routine_door.DELETE)
    assert set(routine_door.TOLD.values()) <= set(os_shadow.AREAS["routines"].verbs)
