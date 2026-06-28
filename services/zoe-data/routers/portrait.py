"""
Portrait API — exposes user portrait management endpoints.

GET  /api/portrait/me                  — current user's portrait
GET  /api/portrait/{user_id}           — specific user's portrait (admin only)
POST /api/portrait/me/regenerate       — regenerate current user's portrait now
POST /api/portrait/{user_id}/regenerate — regenerate specific user's portrait (admin)
GET  /api/portrait/me/emotional-moments — recent emotional memories for current user
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portrait", tags=["portrait"])


def _require_self_or_admin(user: dict, target_user_id: str) -> None:
    """Raise 403 if the requesting user is neither the target nor an admin."""
    if user["user_id"] != target_user_id and user.get("role") not in ("admin", "family-admin"):
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("/me")
async def get_my_portrait(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Return the current user's synthesized portrait text."""
    return await _get_portrait_for(user["user_id"], db)


@router.get("/{user_id}")
async def get_portrait(
    user_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return a specific user's portrait (admin or self only)."""
    _require_self_or_admin(user, user_id)
    return await _get_portrait_for(user_id, db)


async def _get_portrait_for(user_id: str, db) -> dict:
    row = await db.execute(
        "SELECT portrait_text, portrait_version, generated_from_memory_count, last_generated "
        "FROM user_portraits WHERE user_id = ?",
        (user_id,),
    )
    row = await row.fetchone()
    if not row:
        return {
            "user_id": user_id,
            "portrait": None,
            "version": 0,
            "memory_count": 0,
            "last_generated": None,
            "message": "No portrait yet — will be generated on next Sunday dreaming cycle, or use /regenerate.",
        }
    return {
        "user_id": user_id,
        "portrait": row[0],
        "version": row[1],
        "memory_count": row[2],
        "last_generated": row[3],
    }


@router.post("/me/regenerate")
async def regenerate_my_portrait(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Regenerate the current user's portrait immediately."""
    return await _regenerate_for(user["user_id"], db)


@router.post("/{user_id}/regenerate")
async def regenerate_portrait(
    user_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Regenerate a specific user's portrait (admin or self only)."""
    _require_self_or_admin(user, user_id)
    return await _regenerate_for(user_id, db)


async def _regenerate_for(user_id: str, db) -> dict:
    try:
        from user_portrait import run_portrait_synthesis  # type: ignore[import]
        result = await run_portrait_synthesis(user_id, db=db)
        return result
    except Exception:
        logger.exception("portrait regenerate failed user=%s", user_id)
        raise HTTPException(status_code=500, detail="Portrait generation failed")


@router.get("/me/emotional-moments")
async def get_my_emotional_moments(
    limit: int = Query(10, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """Return recent emotional memories for the current user."""
    return await _get_emotional_moments(user["user_id"], limit)


@router.get("/{user_id}/emotional-moments")
async def get_emotional_moments(
    user_id: str,
    limit: int = Query(10, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """Return recent emotional memories (admin or self only)."""
    _require_self_or_admin(user, user_id)
    return await _get_emotional_moments(user_id, limit)


async def _get_emotional_moments(user_id: str, limit: int) -> dict:
    try:
        from memory_service import get_memory_service  # type: ignore[import]
        svc = get_memory_service()
        refs = await svc.load_for_prompt(user_id, limit=200)
        emotional = [
            r for r in refs
            if (getattr(r, "memory_type", "") or "") == "emotional_moment"
            or "emotional" in ((getattr(r, "tags", "") or "")
                               if isinstance(getattr(r, "tags", ""), str)
                               else getattr(r, "tags", []))
        ]
        moments = [
            {
                "id": getattr(r, "id", ""),
                "text": r.text or "",
                "added_at": getattr(r, "added_at", ""),
                "tags": getattr(r, "tags", ""),
            }
            for r in emotional[:limit]
        ]
        return {"user_id": user_id, "emotional_moments": moments, "count": len(moments)}
    except Exception:
        logger.exception("emotional moments load failed user=%s", user_id)
        raise HTTPException(status_code=500, detail="Could not load emotional moments")
