"""The panel's Transcript check screen (flag-dark ``ZOE_TRANSCRIPT_CHECK``; mounted via ``register(app)``, off/shadow = routes ABSENT).

  GET  /api/transcript-check                  the member's open items (best first) + progress ``{done, total}``
  GET  /api/transcript-check/{id}/clip        the item's audio snippet (<= 8 s of WAV)
  POST /api/transcript-check/{id}/answer      ``{"alt": n}`` (a model's word) or ``{"word": "..."}`` (typed on the phone)
  POST /api/transcript-check/{id}/skip
  POST /api/transcript-check/{id}/listen      "say it": Zoe takes the owner's next verified-voice utterance as the word
  GET  /api/transcript-check/{id}/state       status of one item (the screen polls it while Zoe listens)
  GET  /api/transcript-check/phone-qr         an SVG QR of the Pendant page - "fix on phone" (no secret in it; no keyboard on the panel)

WHO: the X-Panel-Card token (``panel_review_cards.authorize``: honoured only while that member's voice session on that panel is open, or when
the request also carries the member's own session) OR the member's own signed-in session (a PIN sign-in on the panel, or the phone). A guest,
an anonymous caller and a device token are refused; every query filters on the member, so another member's id is simply 404.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import transcript_check as tc
from auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/transcript-check", tags=["transcript-check"])


async def _member(request: Request, caller: Optional[dict] = Depends(get_current_user)) -> dict:
    """{"user_id", "panel_id"?}: the card token's member, else the signed-in human member. Anything else is refused."""
    token = request.headers.get("X-Panel-Card", "")
    if token:
        import panel_review_cards as prc
        try:
            card = prc.authorize(token, caller)
        except prc.CardError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        if card.kind != "transcript_check":
            raise HTTPException(status_code=403, detail="not_your_card")
        return {"user_id": card.member_id, "panel_id": card.panel_id}
    from auth import require_signed_in
    from routers.omi_recordings import _require_owner
    if not caller or caller.get("auth_degraded"):
        raise HTTPException(status_code=401, detail="sign_in")
    user = await _require_owner(await require_signed_in(caller))
    return {"user_id": user["user_id"], "panel_id": ""}


class _Answer(BaseModel):
    alt: Optional[int] = Field(default=None, ge=0, le=20)
    word: Optional[str] = Field(default=None, max_length=80)


def _raise(exc: tc.CheckError):
    raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@router.get("")
async def items(who: dict = Depends(_member)):
    return await tc.list_open(who["user_id"])


@router.get("/summary")
async def summary(who: dict = Depends(_member)):
    """Just the count: the home screen's notification card asks this (no sentence leaves the server for it)."""
    return {"open": await tc.open_count(who["user_id"])}


@router.get("/phone-qr")
async def phone_qr(request: Request, who: dict = Depends(_member)):
    import auth_handoff
    import setup_qr
    svg = setup_qr.render_svg(f"{auth_handoff.phone_base_url(request)}/pendant.html")
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/{item_id}/clip")
async def clip(item_id: str, who: dict = Depends(_member)):
    wav = await tc.clip(who["user_id"], item_id)
    if wav is None:
        raise HTTPException(status_code=404, detail="no audio is kept for that one")
    return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/{item_id}/answer")
async def answer(item_id: str, body: _Answer, who: dict = Depends(_member)):
    if (body.alt is None) == (body.word is None):
        raise HTTPException(status_code=400, detail="give alt or word")
    try:
        r = await tc.answer(who["user_id"], item_id, alt=body.alt, word=body.word, via="phone" if body.word is not None else "panel")
    except tc.CheckError as exc:
        _raise(exc)
    return {"ok": True, "applied": r["applied"]}


@router.post("/{item_id}/skip")
async def skip(item_id: str, who: dict = Depends(_member)):
    try:
        if not await tc.skip(who["user_id"], item_id):
            raise HTTPException(status_code=409, detail="already_decided")
    except tc.CheckError as exc:
        _raise(exc)
    return {"ok": True}


@router.post("/{item_id}/listen")
async def listen(item_id: str, who: dict = Depends(_member)):
    if not who.get("panel_id"):
        raise HTTPException(status_code=400, detail="say it needs the panel")      # a phone has a keyboard: it types instead
    async with tc._db() as db:
        row = await tc._item(db, who["user_id"], item_id)
    if row is None or row["status"] != "open":
        raise HTTPException(status_code=404, detail="not_found")
    tc.listen_for(who["user_id"], item_id, who["panel_id"])
    return {"ok": True, "seconds": int(tc.LISTEN_TTL_S)}


@router.get("/{item_id}/state")
async def state(item_id: str, who: dict = Depends(_member)):
    async with tc._db() as db:
        row = await tc._item(db, who["user_id"], item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return {"status": row["status"], "answer": row["answer"] if row["status"] == "answered" else None}


def register(app: FastAPI) -> bool:
    """Mount iff ``ZOE_TRANSCRIPT_CHECK`` is ``on``. Off / shadow = the routes are ABSENT (404): shadow shows nothing."""
    if not tc.live():
        return False
    app.include_router(router)
    logger.info("Transcript check router registered (ZOE_TRANSCRIPT_CHECK=on)")
    return True
