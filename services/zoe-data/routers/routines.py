"""The routines card API (flag-dark ``ZOE_ROUTINES``: off = every route 404, the gate runs before auth and the database).

  GET  /api/routines/today        today's copy of every routine the caller may see
  POST /api/routines/steps/act    ``{"step", "action"}``  action = done | skip | snooze | undo

WHO:
  * a signed-in member's own session (never a device token, a panel principal or an auth outage): acts as the member, verified.
  * the panel with nobody identified (``panel_principal.member_or_panel``): sees FAMILY routines only and taps as the household
    (the fridge rule), never verified.
  * anyone else: 403.

Every rule that decides a tick (who may see, the fridge rule, a medicine line only for its verified subject, no double act) is
in ``routines_store``; this module only says who is asking. A tap is ``routines_store.act_tapped``, so with
``ZOE_OS_ROUTINES=kernel`` it is made first and the Zoe OS kernel is told afterwards (``routine_door``); the store is the one
writer and this module holds no SQL. A kernel refusal of a change (none is made here today) is the app's 403 for
``kernel_door.Refused``. The voice tier's private card (an ``X-Panel-Card`` token) is not built on main and is not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import panel_principal
import routines_store as rs
from auth import get_current_user

ACTIONS = ("done", "skip", "snooze", "undo")


def _on() -> None:
    if not rs.enabled():
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(prefix="/api/routines", tags=["routines"], dependencies=[Depends(_on)])


async def _caller(request: Request) -> Optional[dict]:
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


@dataclass
class Who:
    actor: Optional[str]                 # None = the household at the panel
    verified: bool


def _own_session(u: dict) -> bool:
    return ("device_token_role" not in u and not u.get("panel_id") and not u.get("auth_degraded")
            and panel_principal.is_member(u) and not panel_principal.is_guest_id(u.get("user_id")))


async def _who(request: Request, caller: Optional[dict] = Depends(_caller)) -> Who:
    u = caller or {}
    if _own_session(u):
        return Who(str(u["user_id"]), True)
    if await panel_principal.member_or_panel(request, caller):
        return Who(None, False)
    raise HTTPException(status_code=403, detail="Sign in, or use the wall panel")


def _day_json(d: dict) -> dict:
    r = d["routine"]
    return {"routine": {"id": r["id"], "name": r["name"], "anchor": r["anchor"], "visibility": r["visibility"]},
            "day_id": d["day_id"],
            "steps": [{"id": s["id"], "text": s["text"], "kind": s["kind"], "state": s["state"]} for s in d["steps"]]}


@router.get("/today")
async def today(who: Who = Depends(_who)):
    out = []
    for r in await rs.list_routines(who.actor):
        if not r.get("enabled", 1):
            continue
        d = await rs.day_copy(who.actor, r["id"])            # None: it does not run today, or its name is shielded
        if d is not None:
            out.append(_day_json(d))
    return {"day": rs.household_today().isoformat(), "routines": out}


class _Act(BaseModel):
    action: str
    step: str = ""                       # a day-line id from ``/today``


@router.post("/steps/act")
async def act(body: _Act, who: Who = Depends(_who)):
    if body.action not in ACTIONS:
        raise HTTPException(status_code=400, detail="unknown action")
    if not body.step:
        raise HTTPException(status_code=400, detail="step required")
    try:
        out = await rs.act_tapped(who.actor, body.step, body.action, verified=who.verified)
    except rs.StoreError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    return {"ok": True, "state": out["state"], "previous": out["previous"]}
