"""Taps on the panel's memories card (flag-dark ``ZOE_MEMORIES_VOICE``; mounted via ``register(app)``, off = routes ABSENT).

  POST /api/panel/memories/act      ``{"item", "action", "choice"?}``
        memory row:  wrong + voice   the next spoken turn on this panel (same member, 60 s) is the correction or "forget it"
                     forget + yes    the audited row forget (``memory_space.forget_row``)
                     source          where it came from (provenance + day); the member's EXACT words only behind a FRESH PIN (D13)
        person:      forget_person + yes   the existing ``memory_forget_entity`` intent - a FRESH PIN only (a voice session is
                                           not enough to forget a person: the voice path's step-up rule, kept for touch)
  GET  /api/panel/memories/phone-qr  an SVG QR of the phone's Memories page, to fix a note by typing (no secret in it)

WHO. The card token rides in ``X-Panel-Card`` (never a URL). ``panel_review_cards.authorize`` honours it only while the member's
voice session on that panel is open, or when the request carries the member's own session. FRESH PIN = the request carries the
member's own session (not a device token, not degraded) AND that member holds the panel's fresh PIN session
(``voice_tts._resolve_fresh_panel_member``: the panel idle-logout window, the one expiry authority). An item is a card-local id;
anything not on the card is 404. Logs carry counts and actions, never text.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import memories_voice as mv
import panel_review_cards as prc
from auth import get_current_user
from guest_policy import require_feature_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix=mv.ENDPOINT, tags=["panel"])

NOTE_FORGOTTEN = "Forgotten."
NOTE_SAY_IT = "Okay - tell me what's right, like “it's on Saturday”. Or say “forget it”."
PIN_DETAIL = "pin_needed"


async def _caller(request: Request) -> Optional[dict]:
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


async def _require_card(request: Request, caller: Optional[dict] = Depends(_caller)) -> prc.Card:
    try:
        card = prc.authorize(request.headers.get("X-Panel-Card", ""), caller)
    except prc.CardError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    if card.kind != "memories":
        raise HTTPException(status_code=404, detail="card_gone")
    return card


async def _fresh_panel_member(panel_id: str) -> Optional[str]:
    from routers import voice_tts

    return await voice_tts._resolve_fresh_panel_member(panel_id)


async def fresh_pin(card: prc.Card, caller: Optional[dict]) -> bool:
    """The member's own session on the request AND the panel's fresh PIN sign-in is theirs. Fail-closed."""
    c = caller or {}
    uid = str(c.get("user_id") or "")
    if not uid or uid != card.member_id or "device_token_role" in c or c.get("auth_degraded"):
        return False
    try:
        return (await _fresh_panel_member(card.panel_id)) == card.member_id
    except Exception:  # noqa: BLE001
        return False


async def _require_review(card: prc.Card) -> None:
    """Forget / correct are ``memories.review`` actions, the same capability the phone routes check. The card token proves the
    member, not their role: look the CARD OWNER's role up and refuse (403) when it may not review. Fail-closed."""
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            cur = await db.execute("SELECT role FROM users WHERE id = ?", (card.member_id,))
            row = await cur.fetchone()
            role = (row[0] if row else None) or ""
            if not role:
                raise HTTPException(status_code=403, detail="Your role does not have access to this action.")
            await require_feature_access(db, {"user_id": card.member_id, "role": role}, feature="memories", action="review")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("memories_card: role lookup failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=403, detail="Your role does not have access to this action.") from exc


class _Act(BaseModel):
    item: str
    action: str
    choice: Optional[str] = None


def _item(card: prc.Card, item: str) -> dict:
    it = card.items.get(str(item or ""))
    if it is None:
        raise HTTPException(status_code=404, detail="not on this card")
    return it


async def _source(card: prc.Card, it: dict, caller: Optional[dict]) -> dict:
    import memory_space
    import provenance_answers as pa

    ev = await pa.evidence_for_row(card.member_id, it["ref"])
    if ev.refusal:
        raise HTTPException(status_code=404, detail="gone")
    day = pa.spoken_day(ev.said_at) if ev.said_at else ""
    head = ("You told me" if ev.quote else "I noted it") + (f" {day}" if day else "") + "."
    if ev.pasted:
        return {"ok": True, "keep": True, "detail": head + " It came from something you pasted in."}
    if not ev.quote:
        return {"ok": True, "keep": True, "detail": head + " I don't have your exact words for it."}
    if ev.sensitive or not mv.wall_safe(ev.quote):
        return {"ok": True, "keep": True, "detail": head + " Your words are private, so they stay off this screen."}
    if not await fresh_pin(card, caller):
        return {"ok": True, "keep": True, "detail": head + " Unlock with your PIN to see your exact words."}
    quote = pa.trim(ev.quote, memory_space.TEXT_MAX_CHARS)
    return {"ok": True, "keep": True, "detail": f"{head} You said: “{quote}”"}


@router.post("/act")
async def act(body: _Act, request: Request, card: prc.Card = Depends(_require_card),
              caller: Optional[dict] = Depends(_caller)):
    import memory_space

    it = _item(card, body.item)
    if body.action in ("forget", "wrong", "forget_person"):
        await _require_review(card)
    out: dict
    if it["type"] == "memory" and body.action == "forget":
        if body.choice != "yes":
            raise HTTPException(status_code=400, detail="confirm first")
        try:
            outcome = await memory_space.forget_row(card.member_id, it["ref"])
        except Exception as exc:  # noqa: BLE001 - half done: the retry finishes it
            logger.warning("memories_card: forget failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="not_finished") from exc
        if outcome == "protected":
            raise HTTPException(status_code=409, detail="curated")
        if outcome != "forgotten":
            raise HTTPException(status_code=404, detail="gone")
        card.items.pop(body.item, None)
        out = {"ok": True, "note": NOTE_FORGOTTEN}
    elif it["type"] == "memory" and body.action == "wrong":
        if body.choice != "voice":
            raise HTTPException(status_code=400, detail="unknown choice")
        mv.tap(card.panel_id, card.member_id, it["ref"], it.get("text", ""))
        out = {"ok": True, "keep": True, "note": NOTE_SAY_IT}
    elif it["type"] == "memory" and body.action == "source":
        out = await _source(card, it, caller)
    elif it["type"] == "person" and body.action == "forget_person":
        if body.choice != "yes":
            raise HTTPException(status_code=400, detail="confirm first")
        if not await fresh_pin(card, caller):
            raise HTTPException(status_code=403, detail=PIN_DETAIL)
        reply = await mv.forget_person(card.member_id, it["ref"])
        card.items.pop(body.item, None)
        out = {"ok": True, "note": reply or NOTE_FORGOTTEN}
    else:
        raise HTTPException(status_code=400, detail="unknown action")
    logger.info("MEMORIES_CARD act=%s type=%s", body.action, it["type"])
    return out


@router.get("/phone-qr")
async def phone_qr(request: Request, card: prc.Card = Depends(_require_card)):
    import auth_handoff
    import setup_qr

    svg = setup_qr.render_svg(f"{auth_handoff.phone_base_url(request)}/memories.html#about-me")
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def register(app: FastAPI) -> bool:
    """Mount iff ``ZOE_MEMORIES_VOICE`` is on. Off = the routes are ABSENT (404)."""
    if not mv.enabled():
        return False
    app.include_router(router)
    logger.info("Memories card router registered (ZOE_MEMORIES_VOICE on)")
    return True
