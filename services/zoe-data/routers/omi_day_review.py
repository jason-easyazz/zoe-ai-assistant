"""The pendant day review's surface (flag-dark ``ZOE_OMI_DAY_REVIEW``; mounted via ``register(app)``, off = routes ABSENT).

  GET  /api/omi/day-review/candidates              the caller's candidates (``?status=pending|approved|dismissed|all``, ``?day=``)
                                                   + ``pending`` counts by kind + ``voices_to_name`` (a FEW of the caller's own
                                                   unknown voices, ``omi_day_review.voices_to_name``; named through
                                                   ``/api/omi/voices/unknown/{id}/tag``). Facts about the caller's tagged people are
                                                   candidates like any other (``person_fact`` / ``person_relation``).
  GET  /api/omi/day-review/candidates/{id}         one candidate (payload, quoted evidence, recording ids, provenance)
  POST /api/omi/day-review/candidates/{id}/approve ``{"fields"?: {...}, "via"?: "desktop"|"phone"|"panel_touch"|"voice"}`` - writes
                                                   through the existing write path of its kind (``omi_review_apply``)
  POST /api/omi/day-review/candidates/{id}/dismiss

WHO. A signed-in, non-guest HUMAN member, and only their OWN candidates (every query filters ``user_id``): a candidate exists only
under the user its recordings belong to. A guest, an unauthenticated caller and a panel device token (even one bound to a member) are
refused; there is NO admin override - what a member's pendant heard is theirs. No UI and no spoken offer in this phase: the Memories
space and the first-turn mention read these routes / ``omi_day_review.pending_counts`` in the next phase.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

import omi_day_review as dr
import omi_review_apply as apply
from auth import require_signed_in

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/omi/day-review", tags=["omi"])


async def _require_member(user: dict = Depends(require_signed_in)) -> dict:
    if "device_token_role" in user or str(user.get("username", "")).startswith("panel:") or str(user.get("user_id", "")).startswith("panel"):
        raise HTTPException(status_code=403, detail="sign in as a household member to see what your pendant heard")
    try:
        from user_filters import is_unidentified
        if is_unidentified(str(user.get("user_id") or "")):
            raise HTTPException(status_code=403, detail="sign in as a household member")
    except ImportError:
        pass
    return user


@router.get("/candidates")
async def list_my_candidates(status: str = Query("pending", pattern="^(pending|approving|approved|dismissed|all)$"),
                             day: str = Query("", pattern=r"^(\d{4}-\d{2}-\d{2})?$"), limit: int = Query(100, ge=1, le=500),
                             user: dict = Depends(_require_member)):
    uid = user["user_id"]
    return {"candidates": await dr.list_candidates(uid, status=status, day=day, limit=limit), "pending": await dr.pending_counts(uid),
            "voices_to_name": await dr.voices_to_name(uid)}


@router.get("/candidates/{cid}")
async def get_my_candidate(cid: str, user: dict = Depends(_require_member)):
    c = await dr.get_candidate(user["user_id"], cid)
    if c is None:
        raise HTTPException(status_code=404, detail="no such candidate")
    return c


class _Approve(BaseModel):
    fields: Optional[dict] = None
    via: str = "desktop"


@router.post("/candidates/{cid}/approve")
async def approve_my_candidate(cid: str, body: Optional[_Approve] = None, user: dict = Depends(_require_member)):
    body = body or _Approve()
    try:
        return await apply.approve(user["user_id"], cid, fields=body.fields, via=body.via)
    except apply.ApplyError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code)


@router.post("/candidates/{cid}/dismiss")
async def dismiss_my_candidate(cid: str, user: dict = Depends(_require_member)):
    if not await dr.dismiss(user["user_id"], cid):
        if await dr.get_candidate(user["user_id"], cid) is None:
            raise HTTPException(status_code=404, detail="no such candidate")
        raise HTTPException(status_code=409, detail="already decided")
    return {"ok": True}


def register(app: FastAPI) -> bool:
    """Mount iff ``ZOE_OMI_DAY_REVIEW`` is not off. Off = the routes are ABSENT (404)."""
    if not dr.enabled():
        return False
    app.include_router(router)
    logger.info("Omi day review router registered (ZOE_OMI_DAY_REVIEW=%s, lane %s)", dr.mode(), dr.lane())
    return True
