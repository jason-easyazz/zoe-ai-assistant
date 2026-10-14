"""The struggles report (``struggle_digest`` over ``struggle_detector``): ADMIN PERSONS only.

``GET /api/system/struggles?days=7&scope=me|guest|all&top=20``
  * ``me`` (default) - the caller's own struggles, with example words from the caller's own rows;
  * ``guest``        - guest turns (categories only: guest rows hold no words);
  * ``all``          - every member + guests, COUNTS and clusters only; example words still only from the caller's own rows.
Plus the detector's counters (``stats``: queued / dropped / errors) so an operator can see it is alive and off the path.

A panel device token authenticates a panel, not a person, so it is refused even when bound to an admin. Registered
unconditionally (read-only); with ``ZOE_STRUGGLE_DETECT=off`` it reports what is stored and ``mode: off``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import require_admin

router = APIRouter(prefix="/api/system/struggles", tags=["struggles"])


async def _require_human_admin(user: dict = Depends(require_admin)) -> dict:
    if "device_token_role" in user or str(user.get("username", "")).startswith("panel:"):
        raise HTTPException(status_code=403, detail="Sign in as the household admin to see the struggles report.")
    return user


@router.get("")
async def struggles_report(
    days: float = Query(7.0, gt=0, le=90),
    scope: str = Query("me", pattern="^(me|guest|all)$"),
    top: int = Query(20, ge=1, le=100),
    user: dict = Depends(_require_human_admin),
):
    import struggle_detector
    import struggle_digest

    viewer = str(user.get("user_id") or "")
    if not viewer:
        raise HTTPException(status_code=403, detail="no user")
    user_ids = {"me": [viewer], "guest": ["guest"], "all": None}[scope]
    try:
        report = await struggle_digest.digest(viewer_id=viewer, user_ids=user_ids, days=days, top=top)
    except Exception as exc:  # noqa: BLE001 - a missing table / DB blip is a 503, never a traceback
        raise HTTPException(status_code=503, detail=f"struggles store unavailable ({type(exc).__name__})") from exc
    report["scope"] = scope
    report["stats"] = struggle_detector.stats()
    return report
