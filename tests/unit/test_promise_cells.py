"""Bar cells S32 / S33 (``scripts/perf/promise_cells.py``): the persisted provenance ledger after a restart, and Zoe keeping her own timed promises.

In-process cells: the REAL modules against a throwaway SQLite database built from the real migrations, a stubbed memory store and a fixed
clock. Each cell has a control that must go red; this file proves the instrument can (a scorer that cannot go red measures nothing).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "perf"))
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

import promise_cells as pc  # noqa: E402
import samantha_bar as sb  # noqa: E402

pytestmark = pytest.mark.ci_safe


def test_the_cells_pass_in_process_and_every_control_goes_the_declared_way():
    assert pc.run_controls() == []
    res = pc.run_inprocess()
    assert res["S32"][0] == "PASS" and res["S33"][0] == "PASS", res


def test_s32_goes_red_when_the_persisted_ledger_is_off():
    verdict, ev = pc.run_inprocess({"ZOE_PROVENANCE_PERSIST": "0"})["S32"]
    assert verdict == "FAIL" and "persisted ledger was not used" in ev["why"]


def test_s32_goes_red_when_another_conversation_is_shown_the_sources():
    verdict, ev = pc.score_s32("I said that because earlier today you told me, \"%s\"" % pc.SAY_SISTER.rstrip("."),
                               "I said that because you told me, \"%s\"" % pc.SAY_SISTER)
    assert verdict == "FAIL" and ev["other_session_leaks"]


@pytest.mark.parametrize("flip", [{"ZOE_COMMITMENTS": "off"}, {"ZOE_COMMITMENTS": "shadow"}])
def test_s33_goes_red_without_the_enforcing_tracker(flip):
    verdict, ev = pc.run_inprocess(flip)["S33"]
    assert verdict == "FAIL" and ev["why"]


def test_s33_goes_red_if_the_users_own_words_were_recorded():
    res = pc.run_inprocess()
    good = None
    import asyncio
    with pc.rig({"ZOE_COMMITMENTS": "enforce"}):
        good = asyncio.run(pc._flow_s33())
    assert pc.score_s33(good)[0] == "PASS"
    assert pc.score_s33({**good, "user_rows": 1})[0] == "FAIL" and res["S33"][0] == "PASS"


def test_the_rig_stubs_the_reminder_scheduler_and_restores_every_global_it_touched():
    """The real scheduler module binds ``get_compat_db`` when it is imported: left unstubbed, a warm import writes outside the rig and a
    cold one keeps the rig's database after it exits. The rig stubs the boundary and puts back everything it replaced."""
    import asyncio

    import commitments
    import db_compat
    import proactive.triggers.reminder_scan as scan
    import proactive.triggers.reminders as sched
    import reply_ledger

    touched = [(sched, "schedule_reminder"), (scan, "_ZOE_TZ"), (db_compat, "get_compat_db"), (commitments, "_pending"),
               (commitments, "_last_purge"), (reply_ledger, "_pending"), (reply_ledger, "_last_purge")]
    before = [getattr(m, n) for m, n in touched]
    real_scheduler = sched.schedule_reminder
    with pc.rig({"ZOE_COMMITMENTS": "enforce"}) as db:
        assert sched.schedule_reminder is not real_scheduler
        ev = asyncio.run(pc._flow_s33())
        assert ev["reminders_made"] == 1
        assert [(u, m) for u, m, _ in db.scheduled] == [(pc.USER, "The school pickup")]      # went to the stub, not the real scheduler
    assert all(getattr(m, n) is old for (m, n), old in zip(touched, before))


def test_the_bar_declares_the_cells_and_runs_them_without_the_live_service():
    assert {"S32", "S33"} <= set(sb.SCENARIO_IDS)
    assert {s["id"] for s in sb.SCENARIOS} == set(sb.SCENARIO_IDS)
    assert sb.parse_selection("S32,S33", None) == frozenset({"S32", "S33"})
    logs: list = []
    out = pc.run(None, "unused", lambda c: True, 1, logs.append)
    assert [(c, v) for c, v, _ in out] == [("S32", "PASS"), ("S33", "PASS")] and all(ev["mode"] == "in-process" for _, _, ev in out)
