"""Routines stage 1, the panel card API (``routers/routines.py``) and the card the voice tier pushes. Synthetic members only.

Negative controls beside each grant: flag off = 404 for everyone; a card for another kind or a closed voice session is refused; a
row not on the card is 404; the household panel sees family routines only and cannot tap a personal or a medicine line; a member
session cannot reach another member's routine; the medicine line refuses a household tap but takes the subject's own.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import panel_principal
import panel_review_cards as prc
import routines_store as rs
import routines_tier as rt
import voice_cards
import voice_session
from routers import routines as rr
from routines_support import ALEX, SAM, run, store  # noqa: F401 - store is a fixture

pytestmark = pytest.mark.ci_safe

PANEL = "panel-1"


@pytest.fixture
def api(store, monkeypatch):
    monkeypatch.setenv("ZOE_ROUTINES", "on")
    monkeypatch.setenv("ZOE_ROUTINE_VOICE", "on")
    prc.reset()
    sessions: dict = {}
    pushed: list = []

    async def push(frame):
        pushed.append(frame)
        return 1

    async def is_panel(request, user):
        return bool(caller["panel"])

    monkeypatch.setattr(voice_cards, "push", push)
    monkeypatch.setattr(voice_session, "active_member", lambda panel: sessions.get(panel))
    monkeypatch.setattr(panel_principal, "member_or_panel", is_panel)
    app = FastAPI()
    app.include_router(rr.router)
    caller = {"user": None, "panel": False}
    app.dependency_overrides[rr._caller] = lambda: caller["user"]
    mine = run(rs.create_routine(SAM, "Morning", "waking", steps=(("tablets", "medicine"), "stretch")))
    fam = run(rs.create_routine(ALEX, "Feed the dog", "dinner", visibility="family", steps=("feed the dog",)))
    yield type("Env", (), {"c": TestClient(app), "caller": caller, "sessions": sessions, "pushed": pushed, "db": store,
                           "mine": mine, "fam": fam})
    prc.reset()


def _member(env, uid):
    env.caller.update(user={"user_id": uid, "role": "user"}, panel=False)


def _steps(body):
    return {s["text"]: s for r in body["routines"] for s in r["steps"]}


def test_flag_off_is_404_for_everyone(api, monkeypatch):
    monkeypatch.setenv("ZOE_ROUTINES", "off")
    _member(api, SAM)
    assert api.c.get("/api/routines/today").status_code == 404
    assert api.c.post("/api/routines/steps/act", json={"step": "x", "action": "done"}).status_code == 404


def test_nobody_gets_403(api):
    assert api.c.get("/api/routines/today").status_code == 403


def test_a_member_session_sees_own_and_family_and_ticks_own(api):
    _member(api, SAM)
    body = api.c.get("/api/routines/today").json()
    assert {r["name"] for r in body["routines"]} == {"Morning", "Feed the dog"}
    s = _steps(body)
    assert s["tablets"]["state"] == "pending" and s["tablets"]["kind"] == "medicine"
    r = api.c.post("/api/routines/steps/act", json={"step": s["tablets"]["id"], "action": "done"})
    assert r.status_code == 200 and r.json()["state"] == "done"
    assert api.db.q("SELECT verified, known_by, actor_user_id FROM routine_day_steps WHERE text = 'tablets'") == [(1, "tapped", SAM)]
    assert api.c.post("/api/routines/steps/act", json={"step": s["tablets"]["id"], "action": "done"}).status_code == 409
    _member(api, ALEX)                                         # control: Alex never sees or reaches Sam's routine
    assert {r["name"] for r in api.c.get("/api/routines/today").json()["routines"]} == {"Feed the dog"}
    assert api.c.post("/api/routines/steps/act", json={"step": s["stretch"]["id"], "action": "done"}).status_code == 404


def test_the_household_panel_sees_family_only_and_taps_under_the_fridge_rule(api):
    _member(api, SAM)
    sam = _steps(api.c.get("/api/routines/today").json())
    api.caller.update(user={"user_id": "guest", "role": "guest"}, panel=True)
    body = api.c.get("/api/routines/today").json()
    assert [r["name"] for r in body["routines"]] == ["Feed the dog"]
    dog = _steps(body)["feed the dog"]["id"]
    assert api.c.post("/api/routines/steps/act", json={"step": dog, "action": "done"}).json()["state"] == "done"
    assert api.db.q("SELECT actor_user_id, verified FROM routine_day_steps WHERE text = 'feed the dog'") == [(None, 0)]
    for text in ("tablets", "stretch"):                        # personal lines, the medicine one included: invisible
        assert api.c.post("/api/routines/steps/act", json={"step": sam[text]["id"], "action": "done"}).status_code == 404


def test_the_voice_card_carries_opaque_rows_and_honours_only_its_member(api):
    api.sessions[PANEL] = SAM
    frame = run(rt.card_frame(PANEL, SAM))
    assert frame["private"] is True and frame["kind"] == rt.CARD_KIND and frame["panel_id"] == PANEL
    rows = {i["text"]: i for i in frame["items"]}
    assert set(rows) == {"tablets", "stretch", "feed the dog"} and all(i["id"].startswith("s") for i in frame["items"])
    h = {"X-Panel-Card": frame["card_token"]}
    got = api.c.get("/api/routines/today", headers=h).json()["card"]
    assert {i["id"] for i in got["items"]} == {i["id"] for i in frame["items"]}          # the same opaque ids on a re-read
    assert api.c.post("/api/routines/steps/act", headers=h, json={"item": rows["tablets"]["id"], "action": "done"}).json()["state"] == "done"
    assert api.c.post("/api/routines/steps/act", headers=h, json={"item": "s99", "action": "done"}).status_code == 404
    assert api.c.post("/api/routines/steps/act", headers=h, json={"step": rows["stretch"]["id"], "action": "done"}).status_code == 404
    api.sessions.pop(PANEL)                                    # the voice session closed: the card is dead for a non-member caller
    assert api.c.get("/api/routines/today", headers=h).status_code == 403
    other = prc.mint(PANEL, SAM, "open_loops", {})             # a card of another kind never reaches routines
    api.sessions[PANEL] = SAM
    assert api.c.get("/api/routines/today", headers={"X-Panel-Card": other}).status_code == 404


def test_a_bad_action_is_400(api):
    _member(api, SAM)
    assert api.c.post("/api/routines/steps/act", json={"step": "x", "action": "explode"}).status_code == 400


def test_naming_a_step_by_voice_pushes_the_private_card_to_that_panel_only(api):
    from routines_tier import tier

    res = run(tier("what's next", PANEL, "s1", channel="voice",
                   extra_ctx={"panel_id": PANEL, "scope_user": SAM, "speaker_verified": True}))
    assert res.reply == "Next: tablets."
    assert [f["panel_id"] for f in api.pushed] == [PANEL] and api.pushed[0]["member_id"] == SAM
    run(tier("what's next", "u", "s2", channel="chat", extra_ctx={}))           # chat has no panel: nothing pushed
    assert len(api.pushed) == 1
