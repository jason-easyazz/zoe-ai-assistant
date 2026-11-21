"""The routines card API (``routers/routines.py``), on main's store and on the Zoe OS door from its first line.

Flag off = 404 for everyone, before auth. A signed-in member sees their own and the family's routines and taps their own
lines, a medicine line included; the bare panel sees family routines only, taps them as the household, and cannot reach a
personal or a medicine line; nobody reaches another member's routine; anyone else is 403. A tap is the store's
``act_tapped``: with ``ZOE_OS_ROUTINES=kernel`` it is made first and the kernel is told afterwards, ids only. The module
holds no SQL. Each test names the control that was run against it. Synthetic members only.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import os_shadow
import panel_principal
import routines_store as rs
import sql_scan
import user_erase_gate
from routers import routines as rr
from routines_support import ALEX, SAM, run, store  # noqa: F401 - store is a fixture
from test_kernel_door import AreaKernel

pytestmark = pytest.mark.ci_safe
SVC = Path(__file__).resolve().parents[1]


@pytest.fixture
def api(store, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ZOE_ROUTINES", "on")
    caller = {"user": None, "panel": False}

    async def is_panel(request, user):
        return bool(caller["panel"])

    monkeypatch.setattr(panel_principal, "member_or_panel", is_panel)
    app = FastAPI()
    app.include_router(rr.router)
    app.dependency_overrides[rr._caller] = lambda: caller["user"]
    mine = run(rs.create_routine(SAM, "Morning", "waking", steps=(("tablets", "medicine"), "stretch")))
    fam = run(rs.create_routine(ALEX, "Feed the dog", "dinner", visibility="family", steps=("feed the dog",)))
    return type("Env", (), {"c": TestClient(app), "caller": caller, "db": store, "mine": mine, "fam": fam})


def _member(env, uid):
    env.caller.update(user={"user_id": uid, "role": "user"}, panel=False)


def _panel(env):
    env.caller.update(user=None, panel=True)


def _today(env) -> dict:
    body = env.c.get("/api/routines/today").json()
    return {d["routine"]["name"]: d for d in body["routines"]}


def _act(env, step, action="done"):
    return env.c.post("/api/routines/steps/act", json={"step": step, "action": action})


def test_flag_off_every_route_is_404_before_anyone_is_identified(api, monkeypatch):
    """Control run: with the gate dependency dropped from the router the member's ``/today`` is 200."""
    monkeypatch.setenv("ZOE_ROUTINES", "off")
    _member(api, SAM)
    assert api.c.get("/api/routines/today").status_code == 404
    assert _act(api, "anything").status_code == 404


def test_a_member_sees_their_own_and_the_familys_routines_and_ticks_their_own_medicine_line(api):
    """Control run: with the member session marked unverified in ``_who`` the medicine tick is 403."""
    _member(api, SAM)
    days = _today(api)
    assert set(days) == {"Morning", "Feed the dog"}
    assert [(s["text"], s["kind"], s["state"]) for s in days["Morning"]["steps"]] == [("tablets", "medicine", "pending"),
                                                                                    ("stretch", "check", "pending")]
    step = days["Morning"]["steps"][0]["id"]
    assert _act(api, step).json() == {"ok": True, "state": "done", "previous": "pending"}
    assert _act(api, step).status_code == 409                                          # already done: never a double act
    assert _act(api, step, "undo").json()["state"] == "pending"
    assert _act(api, step, "dance").status_code == 400 and _act(api, "").status_code == 400


def test_the_bare_panel_sees_family_routines_only_and_taps_them_as_the_household(api):
    """The fridge rule. Control run: with the panel given a member's id in ``_who`` it sees the personal routine."""
    _member(api, SAM)
    personal = _today(api)["Morning"]["steps"]
    _panel(api)
    days = _today(api)
    assert set(days) == {"Feed the dog"}
    assert _act(api, days["Feed the dog"]["steps"][0]["id"]).json()["state"] == "done"
    for line in personal:                                                               # a personal line, the medicine one included
        assert _act(api, line["id"]).status_code == 404
    assert api.db.q("SELECT actor_user_id, verified FROM routine_day_steps WHERE state = 'done'") == [(None, 0)]


def test_nobody_reaches_another_members_routine_and_a_stranger_is_403(api):
    """Control run: with ``_who`` answering the household for anyone, the signed-out caller gets 200."""
    _member(api, SAM)
    mine = _today(api)["Morning"]["steps"][1]["id"]
    _member(api, ALEX)
    assert set(_today(api)) == {"Feed the dog"} and _act(api, mine).status_code == 404
    api.caller.update(user=None, panel=False)
    assert api.c.get("/api/routines/today").status_code == 403 and _act(api, mine).status_code == 403
    api.caller.update(user={"user_id": SAM, "role": "user", "device_token_role": "panel"}, panel=False)
    assert api.c.get("/api/routines/today").status_code == 403                          # a device token is not the member's session


def test_a_tap_is_made_first_and_the_kernel_is_told_afterwards_with_ids_only(api, monkeypatch):
    """The API is on the door from its first line: a tap goes through the store, so with ``ZOE_OS_ROUTINES=kernel`` the
    kernel hears it after it happened, and a kernel "no" does not undo it. Control run: with the tell dropped from the
    store's ``_apply`` nothing is told."""
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ALEX, SAM]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_ROUTINES", "kernel")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    try:
        _member(api, SAM)
        step = _today(api)["Morning"]["steps"][1]["id"]
        k.door = "refuse:not_owner"
        assert _act(api, step).json()["state"] == "done"                                # made, whatever the kernel says
        end = time.monotonic() + 2
        while time.monotonic() < end and not k.ops("door_ask"):
            time.sleep(0.01)
        ask = k.ops("door_ask")[0]
        assert (ask["verb"], ask["person"], ask["owner"], ask["target"]) == ("routine.tick", SAM, SAM, api.mine["id"])
        assert "stretch" not in json.dumps(k.calls, default=str) and "Morning" not in json.dumps(k.calls, default=str)
    finally:
        os_shadow._reset_for_tests()
        user_erase_gate.reset()
        k.close()
        shutil.rmtree(state, ignore_errors=True)


def test_the_module_holds_no_sql_and_is_mounted_behind_its_flag():
    """The store is the one writer. Control run: an ``UPDATE routine_day_steps`` planted in the router fails this."""
    src = (SVC / "routers" / "routines.py").read_text("utf-8")
    assert not sql_scan.writes(src, sql_scan.write_pattern("routines", "routine_steps", "routine_days", "routine_day_steps"))
    assert "db.execute" not in src and "get_compat_db" not in src and "rs.act_tapped(" in src
    assert "dependencies=[Depends(_on)]" in src
    main = (SVC / "main.py").read_text("utf-8")
    assert "from routers.routines import router as routines_router" in main and "app.include_router(routines_router)" in main
