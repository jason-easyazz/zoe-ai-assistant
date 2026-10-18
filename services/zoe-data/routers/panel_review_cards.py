"""Taps on a panel review card (flag-dark ``ZOE_PANEL_REVIEW_CARDS``; mounted via ``register(app)``, off = routes ABSENT).

  POST /api/panel/cards/act        ``{"item", "action", "choice"?}``  done / drop / remind (a loop), keep / skip (a day-review
                                   candidate), me / tag / media (a new voice)
  GET  /api/panel/cards/clip/{item}  the voice's sample line, <= 8 s of WAV (the owner-only clip route's own rule)
  GET  /api/panel/cards/phone-qr   an SVG QR of the Pendant page, for naming someone new on the phone (no secret in it)

WHO. The card token rides in the ``X-Panel-Card`` header (never a URL: nginx logs query strings). ``panel_review_cards.authorize``
honours it only while the member's voice session on that panel is still open, or when the request carries the member's own session
(a PIN sign-in). An item is an opaque id from that card; anything not on the card is 404. Every write goes through the existing
owner-checked path of its store (``omi_review_apply.approve``, ``omi_day_review.dismiss``, the omi voice routes).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import panel_review_cards as prc
from auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/panel/cards", tags=["panel"])


async def _caller(request: Request) -> Optional[dict]:
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


def _card(request: Request, caller: Optional[dict]) -> prc.Card:
    try:
        return prc.authorize(request.headers.get("X-Panel-Card", ""), caller)
    except prc.CardError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc


def _item(card: prc.Card, item: str) -> dict:
    it = card.items.get(str(item or ""))
    if it is None:
        raise HTTPException(status_code=404, detail="not on this card")
    return it


class _Act(BaseModel):
    item: str
    action: str
    choice: Optional[str] = None


async def _act_loop(card: prc.Card, it: dict, body: _Act) -> dict:
    if body.action in ("done", "drop"):
        if not await prc.close_loop(card.member_id, it["ref"], "done" if body.action == "done" else "dropped"):
            raise HTTPException(status_code=409, detail="already closed")
        return {"ok": True}
    if body.action == "remind":
        pick = card.choices.get(str(body.choice or ""))
        if not pick:
            raise HTTPException(status_code=400, detail="pick a time")
        try:
            made = await prc.loop_to_reminder(card.member_id, {"id": it["ref"], "text": it["text"]}, pick[0], pick[1])
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.info("panel_review: reminder failed: %s", type(exc).__name__)
            made = False
        if not made:
            raise HTTPException(status_code=502, detail="reminder not saved")
        return {"ok": True, "note": f"Reminder set: {pick[2]}"}
    raise HTTPException(status_code=400, detail="unknown action")


async def _act_candidate(card: prc.Card, it: dict, body: _Act) -> dict:
    import omi_day_review as dr
    import omi_review_apply as apply

    if body.action == "keep":
        try:
            await apply.approve(card.member_id, it["ref"], via="panel_touch")
        except apply.ApplyError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        return {"ok": True}
    if body.action == "skip":
        if not await dr.dismiss(card.member_id, it["ref"], via="panel_touch"):
            raise HTTPException(status_code=409, detail="already decided")
        return {"ok": True}
    raise HTTPException(status_code=400, detail="unknown action")


async def _act_voice(card: prc.Card, it: dict, body: _Act) -> dict:
    from routers import omi_voices as ov

    user = {"user_id": card.member_id, "role": "user", "username": card.member_id}
    await ov._require_owner(user)
    if body.action == "me":
        await ov.voice_is_me(it["ref"], user)
    elif body.action == "media":
        await ov.not_a_person_voice(it["ref"], ov._Kind(kind="media"), user)
    elif body.action == "tag":
        pid = card.choices.get(str(body.choice or ""))
        if not pid or pid not in card.people:
            raise HTTPException(status_code=400, detail="pick someone on the card")
        await ov.tag_voice(it["ref"], ov._Tag(person_id=pid), user)
    else:
        raise HTTPException(status_code=400, detail="unknown action")
    return {"ok": True}


@router.post("/act")
async def act(body: _Act, request: Request, caller: Optional[dict] = Depends(_caller)):
    card = _card(request, caller)
    it = _item(card, body.item)
    handler = {"loop": _act_loop, "candidate": _act_candidate, "voice": _act_voice}[it["type"]]
    out = await handler(card, it, body)
    card.items.pop(body.item, None)                  # one decision per row
    logger.info("PANEL_REVIEW act=%s type=%s via=%s", body.action, it["type"], "card")
    return out


@router.get("/clip/{item}")
async def clip(item: str, request: Request, caller: Optional[dict] = Depends(_caller)):
    from routers import omi_voices as ov

    card = _card(request, caller)
    it = _item(card, item)
    if it.get("type") != "voice" or not it.get("segment"):
        raise HTTPException(status_code=404, detail="no clip")
    user = {"user_id": card.member_id, "role": "user", "username": card.member_id}
    await ov._require_owner(user)
    return await ov.segment_clip(it["segment"], user)


@router.get("/phone-qr")
async def phone_qr(request: Request, caller: Optional[dict] = Depends(_caller)):
    import auth_handoff
    import setup_qr

    card = _card(request, caller)
    if card.kind != "day_review":
        raise HTTPException(status_code=404, detail="no phone hand-off on this card")
    svg = setup_qr.render_svg(f"{auth_handoff.phone_base_url(request)}/pendant.html")
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def register(app: FastAPI) -> bool:
    """Mount iff ``ZOE_PANEL_REVIEW_CARDS`` is on. Off = the routes are ABSENT (404)."""
    if not prc.enabled():
        return False
    app.include_router(router)
    logger.info("Panel review cards router registered (ZOE_PANEL_REVIEW_CARDS on)")
    return True
