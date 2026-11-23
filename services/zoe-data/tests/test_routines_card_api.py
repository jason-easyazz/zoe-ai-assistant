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

    async def is_panel(request, user, db=None, *, mode=None):      # the panel's own test: only ever asked about an anonymous device
        assert panel_principal.is_guest_id(user["user_id"]) and mode == "registered"
        return bool(caller["panel"])

    monkeypatch.setattr(panel_principal, "is_panel_guest", is_panel)
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
    """The gate runs FIRST: with the flag off neither route identifies anybody (the caller dependency, the real
    ``get_current_user`` and the panel test are each recorded if called) and both answer 404. Controls run: with the gate
    dependency dropped from the router the member's ``/today`` is 200; with the gate moved after the caller dependency
    (``dependencies=[Depends(_caller), Depends(_on)]``) the caller is recorded and this fails."""
    called: list = []

    def caller():
        called.append("_caller")
        return api.caller["user"]

    async def authenticate(request):
        called.append("get_current_user")
        return api.caller["user"]

    async def panel(request, user, db=None, *, mode=None):
        called.append("is_panel_guest")
        return False

    api.c.app.dependency_overrides[rr._caller] = caller
    monkeypatch.setattr(rr, "get_current_user", authenticate)
    monkeypatch.setattr(panel_principal, "is_panel_guest", panel)
    monkeypatch.setenv("ZOE_ROUTINES", "off")
    _member(api, SAM)
    assert api.c.get("/api/routines/today").status_code == 404
    assert _act(api, "anything").status_code == 404
    assert called == []
    monkeypatch.setenv("ZOE_ROUTINES", "on")                       # the recorder records: the same requests, flag on
    assert api.c.get("/api/routines/today").status_code == 200 and called == ["_caller"]


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


# ---- WHO, on the real path: nothing below is stubbed above the registry read and the authenticator ---------------------------------
@pytest.fixture
def real(store, monkeypatch):  # noqa: F811
    """The router with its REAL ``_caller``, ``_who`` and ``panel_principal`` tests. Only the two ends are set: what
    ``get_current_user`` answers (or raises), and whether the request's address is a registered panel's."""
    monkeypatch.setenv("ZOE_ROUTINES", "on")
    monkeypatch.delenv("ZOE_PANEL_TOKEN_VIA_CF", raising=False)
    state = {"auth": None, "registered": False}

    async def authenticate(request):
        if isinstance(state["auth"], Exception):
            raise state["auth"]
        return state["auth"]

    async def registry(db, check, value):
        return bool(state["registered"])

    monkeypatch.setattr(rr, "get_current_user", authenticate)
    monkeypatch.setattr(panel_principal, "_with_registry", registry)
    app = FastAPI()
    app.include_router(rr.router)
    run(rs.create_routine(SAM, "Morning", "waking", steps=(("tablets", "medicine"), "stretch")))
    run(rs.create_routine(ALEX, "Feed the dog", "dinner", visibility="family", steps=("feed the dog",)))
    return type("Env", (), {"c": TestClient(app), "state": state})


TUNNEL = {"cf-ray": "synthetic-ray"}
BOUND_TOKEN = {"user_id": SAM, "role": "user", "device_token_role": "panel", "panel_id": "panel-qov"}


def _seen(env, headers=None):
    out = env.c.get("/api/routines/today", headers=headers or {})
    return out.status_code, ({r["routine"]["name"] for r in out.json()["routines"]} if out.status_code == 200 else None)


def test_a_device_token_bound_to_a_member_is_never_the_member_and_is_the_panel_only_where_a_device_token_is(real):
    """``ZOE_DEVICE_TOKEN_BINDS_MEMBER=1`` gives a device token a member principal (``device_token_role`` + ``panel_id``). It is
    not the member's own session, and being a member's must not make it the panel: it is judged as the device it is. Over the
    Cloudflare tunnel it is 403 for the read and the tap (it used to read and change family routines as the household); where
    a device token is the panel it sees what the bare panel sees, never the member's own routine. A member's own session is
    untouched by any of it.

    Controls run: ``_who`` calling ``panel_principal.member_or_panel(request, caller)`` again (the old line) -> the tunnel
    request is 200; ``_at_the_panel`` judging the principal as it came (``device = dict(u)``) -> the bound token is refused
    everywhere (not a guest id), failing the panel line."""
    real.state["auth"] = dict(BOUND_TOKEN)
    assert _seen(real, TUNNEL) == (403, None)
    assert real.c.post("/api/routines/steps/act", json={"step": "any", "action": "done"}, headers=TUNNEL).status_code == 403
    assert _seen(real) == (200, {"Feed the dog"})                                        # the panel: family only, not SAM's own
    real.state["auth"] = {"user_id": "guest", "role": "guest", "panel_id": "panel-qov"}  # the same token, bound to nobody
    assert _seen(real, TUNNEL) == (403, None) and _seen(real) == (200, {"Feed the dog"})
    real.state["auth"] = {"user_id": SAM, "role": "user"}                                # SAM's own session, wherever it is
    assert _seen(real, TUNNEL) == (200, {"Morning", "Feed the dog"})


@pytest.mark.parametrize("registered", [True, False])
def test_an_auth_outage_is_nobodys_evidence_even_at_a_registered_panel_address(real, registered):
    """Authentication failing closed (503) is the route's answer. It used to be turned into "nobody identified", which a
    request from a registered panel address then passed as the bare panel, reading and changing family routines during the
    outage. A degraded principal (the outage with fail-closed off) is 403. Only "no valid session" (401) reads as nobody
    identified: at a registered panel address that is the bare panel, anywhere else 403.

    Controls run: ``_caller`` swallowing every ``HTTPException`` again (the old ``except HTTPException: return None``) -> the
    503 case is 200 at the registered address and 403 elsewhere; the ``auth_degraded`` line dropped from ``_at_the_panel``
    -> a degraded principal at a registered address is 200."""
    from fastapi import HTTPException

    real.state["registered"] = registered
    real.state["auth"] = HTTPException(status_code=503, detail="auth unavailable")
    assert _seen(real) == (503, None)
    assert real.c.post("/api/routines/steps/act", json={"step": "any", "action": "done"}).status_code == 503
    real.state["auth"] = {"user_id": "guest", "role": "guest", "auth_degraded": True}
    assert _seen(real) == (403, None)
    real.state["auth"] = {"user_id": SAM, "role": "user", "auth_degraded": True}
    assert _seen(real) == (403, None)
    real.state["auth"] = HTTPException(status_code=401, detail="Invalid or expired session")
    assert _seen(real) == ((200, {"Feed the dog"}) if registered else (403, None))
    real.state["auth"] = {"user_id": "guest", "role": "guest"}                           # no credentials at all
    assert _seen(real) == ((200, {"Feed the dog"}) if registered else (403, None))
