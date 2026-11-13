"""The owner's decide page API (``evolution_inbox.py``; page ``zoe-ui/dist/evolution.html``). HOUSEHOLD ADMIN PERSONS only.

  * ``GET  /api/system/evolution/inbox?stale_days=30``  at most 20 grouped cards, counts and dates only (never a member's words)
  * ``POST /api/system/evolution/decide`` ``{ids, action: approve|reject|later, reason?}``  one card's verdict, idempotent
  * ``POST /api/system/evolution/dismiss-stale`` ``{stale_days}``  reject the hidden old proposals in one go

Refused (403): a non-admin (member, child, guest, no session), a panel device token (it authenticates a panel, not a person) and the
``ZOE_UNAUTHENTICATED_ROLE`` promotion (an admin role with no sign-in behind it). Approving applies nothing.
"""
from __future__ import annotations

from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth import require_admin

router = APIRouter(prefix="/api/system/evolution", tags=["evolution"])


async def _owner(user: dict = Depends(require_admin)) -> dict:
    if ("device_token_role" in user or str(user.get("username", "")).startswith("panel:") or user.get("unauthenticated_override")
            or not str(user.get("user_id") or "")):
        raise HTTPException(status_code=403, detail="Sign in as the household admin to decide on Zoe's proposals.")
    return user


class Verdict(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=500)
    action: Literal["approve", "reject", "later"]
    reason: Literal["", "not_a_problem", "wrong_label", "already_fixed", "not_now", "other"] = ""


class Stale(BaseModel):
    stale_days: float = Field(30.0, ge=1, le=365)


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=f"evolution queue unavailable ({type(exc).__name__})")


@router.get("/inbox")
async def evolution_inbox(stale_days: float = Query(30.0, ge=1, le=365), user: dict = Depends(_owner)):
    import evolution_inbox as ei

    try:
        return await ei.inbox(str(user["user_id"]), stale_days=stale_days)
    except Exception as exc:  # noqa: BLE001 - a missing table / DB blip is a 503, never a traceback
        raise _unavailable(exc) from exc


@router.post("/decide")
async def evolution_decide(body: Verdict, user: dict = Depends(_owner)):
    import evolution_inbox as ei

    try:
        out = await ei.decide(body.ids, body.action, str(user["user_id"]), body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
    if out["missing"] and not (out["changed"] or out["unchanged"]):
        raise HTTPException(status_code=404, detail="no such proposal")
    return out


@router.post("/dismiss-stale")
async def evolution_dismiss_stale(body: Stale, user: dict = Depends(_owner)):
    import evolution_inbox as ei

    try:
        return await ei.dismiss_stale(str(user["user_id"]), stale_days=body.stale_days)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
