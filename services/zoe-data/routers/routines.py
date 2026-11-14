"""The routines card API (flag-dark ``ZOE_ROUTINES``: off = every route 404, the gate runs before auth and the database).

  GET  /api/routines/today        today's copy of every routine the caller may see (the touch card / Day screen / "Your Day" line)
  POST /api/routines/steps/act    ``{"item"|"step", "action"}``  action = done | skip | snooze | undo

WHO (the private-panel-card pattern of ``routers/panel_review_cards.py``):
  * an ``X-Panel-Card`` token (header, never a URL) minted by the voice tier for this panel's member: honoured only while that
    member's voice session on that panel is open, or with the member's own session (``panel_review_cards.authorize``). Rows are
    named by the card's OPAQUE ids (``s1``, ``s2``); anything not on the card is 404. Acts as the member, verified.
  * a signed-in member's own session (never a device token, a panel principal or an auth outage): acts as the member, verified.
  * the panel with nobody identified (``panel_principal.member_or_panel``, strict ``registered`` form): sees FAMILY routines only and
    taps as the household (the lists fridge rule), never verified.
  * anyone else: 403.
Every rule that decides a tick (who may see, the fridge rule, a medicine line only for its verified subject, no double act) is in
``routines_store``; this module only says who is asking. Logs are the store's ``ROUTINE event=... via=tapped`` markers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import panel_principal
import panel_review_cards as prc
import routines_store as rs
import routines_tier as rt
from auth import get_current_user

logger = logging.getLogger(__name__)


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
    card: Optional[prc.Card] = None


def _own_session(u: dict) -> bool:
    return ("device_token_role" not in u and not u.get("panel_id") and not u.get("auth_degraded")
            and panel_principal.is_member(u) and not panel_principal.is_guest_id(u.get("user_id")))


async def _who(request: Request, caller: Optional[dict] = Depends(_caller)) -> Who:
    token = request.headers.get("X-Panel-Card", "")
    if token:
        try:
            card = prc.authorize(token, caller)
        except prc.CardError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        if card.kind != rt.CARD_KIND:
            raise HTTPException(status_code=404, detail="card_gone")
        return Who(card.member_id, True, card)
    u = caller or {}
    if _own_session(u):
        return Who(str(u["user_id"]), True)
    if await panel_principal.member_or_panel(request, caller):
        return Who(None, False)
    raise HTTPException(status_code=403, detail="Sign in, or use the wall panel")


@router.get("/today")
async def today(who: Who = Depends(_who)):
    if who.card is not None:                                   # the card's own rows, re-read now (same opaque ids per line)
        frame = await rt.card_frame(who.card.panel_id, who.actor, card=who.card)
        return {"day": rs.household_today().isoformat(), "card": frame}
    days = await rt.visible_days(who.actor)
    return {"day": rs.household_today().isoformat(), "routines": [rt.day_json(d) for d in days]}


class _Act(BaseModel):
    action: str
    item: Optional[str] = None           # an opaque id from the card (card callers)
    step: Optional[str] = None           # a day-line id (a member session or the household panel)


@router.post("/steps/act")
async def act(body: _Act, who: Who = Depends(_who)):
    if body.action not in ("done", "skip", "snooze", "undo"):
        raise HTTPException(status_code=400, detail="unknown action")
    if who.card is not None:
        it = who.card.items.get(str(body.item or ""))
        if not it or it.get("type") != "routine_step":
            raise HTTPException(status_code=404, detail="not on this card")
        step = it["ref"]
    else:
        step = str(body.step or "")
        if not step:
            raise HTTPException(status_code=400, detail="step required")
    try:
        out = await rs.act_tapped(who.actor, step, body.action, verified=who.verified)
    except rs.StoreError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    return {"ok": True, "state": out["state"], "previous": out["previous"]}
