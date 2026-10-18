"""Change notices (``change_notices``, ``ZOE_CHANGE_NOTICES``): the owner's own preferences, status and kill switch.

ADMIN PERSONS only (Phase 1 is owner-only; a panel device token authenticates a panel, not a person, so it is refused
even when bound to an admin):
  * ``GET  /api/change-notices/preferences``  the caller's validated ``communication`` preferences + whether Telegram is linked
  * ``PUT  /api/change-notices/preferences``  a partial update (``comm_prefs.merge``), validated; invalid fields fall back
  * ``GET  /api/change-notices/status``       mode, kill switch, delivery COUNTS by status (never notice text)
  * ``POST /api/change-notices/kill``         ``{"on": true|false}`` - stop / resume every send at the next tick

Registered unconditionally: with ``ZOE_CHANGE_NOTICES=off`` the routes still read and store preferences, and nothing sends.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException

from auth import require_admin

router = APIRouter(prefix="/api/change-notices", tags=["change-notices"])


async def _require_human_admin(user: dict = Depends(require_admin)) -> dict:
    if "device_token_role" in user or str(user.get("username", "")).startswith("panel:"):
        raise HTTPException(status_code=403, detail="Sign in as the household admin to manage change notices.")
    return user


@router.get("/preferences")
async def get_preferences(user: dict = Depends(_require_human_admin)):
    import change_notices
    import comm_prefs
    from user_prefs import read_prefs
    from database import get_db_ctx

    async with get_db_ctx() as db:
        prefs = await read_prefs(db, user["user_id"])
    doc = comm_prefs.normalise(prefs.get(comm_prefs.PREF_KEY), user.get("role", ""))
    tid = prefs.get("telegram_id")
    return {"mode": change_notices.mode(), "preferences": doc,
            "telegram_linked": isinstance(tid, str) and tid.isdigit()}


@router.put("/preferences")
async def put_preferences(payload: dict = Body(...), user: dict = Depends(_require_human_admin)):
    import comm_prefs
    from user_prefs import get_pref

    stored = await get_pref(user["user_id"], comm_prefs.PREF_KEY, None)
    doc = await comm_prefs.save(user["user_id"], user.get("role", ""), comm_prefs.merge(stored, payload))
    if doc is None:
        raise HTTPException(status_code=403, detail="Change notices are owner-only for now.")
    return {"preferences": doc}


@router.get("/status")
async def get_status(user: dict = Depends(_require_human_admin)):
    import change_notices

    return await asyncio.to_thread(change_notices.status)


@router.post("/kill")
async def set_kill(payload: dict = Body(...), user: dict = Depends(_require_human_admin)):
    import change_notices

    on = bool((payload or {}).get("on", True))
    await asyncio.to_thread(change_notices.set_killed, on)
    change_notices.audit("kill_switch", member=user["user_id"], on=on)
    return {"killed": on}
