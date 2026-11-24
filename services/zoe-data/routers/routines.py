"""The routines card API (flag-dark ``ZOE_ROUTINES``: off = the routes do not exist, read per request (``_Gated``): the app's
own 404, as for a path nobody mounted, before the method, the body, auth or the database is looked at).

  GET  /api/routines/today        today's copy of every routine the caller may see
  POST /api/routines/steps/act    ``{"step", "action"}``  action = done | skip | snooze | undo

WHO:
  * a signed-in member's own session (never a device token, a panel principal or an auth outage): acts as the member, verified.
  * the panel with nobody identified: sees FAMILY routines only and taps as the household (the fridge rule), never verified.
    "The panel" is PROVED by the request, never by who a credential names (``_at_the_panel``): the strict ``registered`` test
    of ``panel_principal.is_panel_guest`` (a device token not presented over the Cloudflare tunnel, the kiosk credential, a
    registered panel address). A device token BOUND TO A MEMBER (``ZOE_DEVICE_TOKEN_BINDS_MEMBER=1``) is judged as the device it
    is, by that same test: it is never the member, and being a member's does not make it the panel.
  * anyone else: 403. An AUTH OUTAGE is nobody's evidence: a fail-closed 503 from authentication is the route's answer (it is
    never turned into "nobody identified", which a registered panel address would then pass), and a degraded principal is 403.
    Only "no valid session" (401) reads as nobody identified.

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
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.routing import Match

import panel_principal
import routines_store as rs
from auth import get_current_user

ACTIONS = ("done", "skip", "snooze", "undo")


class _Gated(APIRoute):
    """A route of this router MATCHES NOTHING while ``ZOE_ROUTINES`` is off (read per request), so the app answers exactly as it
    did before the router was mounted: its own 404 ``{"detail": "Not Found"}`` for every method, body and parameter. A gate in a
    dependency runs too late: FastAPI parses the body (a malformed one is 422) before it solves any dependency."""

    def matches(self, scope):
        match, child_scope = super().matches(scope)
        if match is not Match.NONE and not rs.enabled():
            return Match.NONE, {}
        return match, child_scope


router = APIRouter(prefix="/api/routines", tags=["routines"], route_class=_Gated)


async def _caller(request: Request) -> Optional[dict]:
    try:
        return await get_current_user(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise                        # an auth outage (503, fail closed), anything unexpected: the request fails as it is
        return None                      # no valid session: nobody is identified (the panel, if the request proves it is one)


@dataclass
class Who:
    actor: Optional[str]                 # None = the household at the panel
    verified: bool


def _own_session(u: dict) -> bool:
    return ("device_token_role" not in u and not u.get("panel_id") and not u.get("auth_degraded")
            and panel_principal.is_member(u) and not panel_principal.is_guest_id(u.get("user_id")))


async def _at_the_panel(request: Request, u: dict) -> bool:
    """Is this request, which is NOT a member's own session, demonstrably the wall panel? Whoever its credential names is set
    aside: it is judged as an anonymous device carrying only its panel id. Never an auth-outage principal. Fails closed."""
    try:
        if u.get("auth_degraded"):
            return False
        device = {"user_id": "guest", "role": "guest"}
        if u.get("panel_id"):
            device["panel_id"] = u["panel_id"]
        return bool(await panel_principal.is_panel_guest(request, device, None, mode="registered"))
    except Exception:  # noqa: BLE001 - fail closed
        return False


async def _who(request: Request, caller: Optional[dict] = Depends(_caller)) -> Who:
    u = caller or {}
    if _own_session(u):
        return Who(str(u["user_id"]), True)
    if await _at_the_panel(request, u):
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
