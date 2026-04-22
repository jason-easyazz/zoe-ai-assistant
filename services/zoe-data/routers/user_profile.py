import json
from collections import Counter

from fastapi import APIRouter, Depends

from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access

router = APIRouter(prefix="/api/user/profile", tags=["user-profile"])


@router.get("")
async def get_user_profile(user: dict = Depends(get_current_user), db=Depends(get_db)):
    await require_feature_access(db, user, feature="user_profile", action="read")
    user_id = user["user_id"]
    profile = {
        "user_id": user_id,
        "name": user.get("display_name", user_id),
        "role": user.get("role", "user"),
        "profile_completeness": 0.0,
        "confidence_score": 0.0,
        "ai_insights": {"observed_patterns": []},
    }
    cur = await db.execute("SELECT COUNT(*) FROM people WHERE deleted = 0 AND user_id = ?", (user_id,))
    people_count = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM notes WHERE deleted = 0 AND user_id = ?", (user_id,))
    notes_count = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM journal_entries WHERE deleted = 0 AND user_id = ?", (user_id,))
    journal_count = (await cur.fetchone())[0]
    completeness = min(1.0, (people_count * 0.2) + (notes_count * 0.05) + (journal_count * 0.1))
    confidence = min(1.0, (notes_count * 0.03) + (journal_count * 0.05))
    profile["profile_completeness"] = round(completeness, 2)
    profile["confidence_score"] = round(confidence, 2)
    return profile


@router.post("/analyze")
async def analyze_profile(user: dict = Depends(get_current_user), db=Depends(get_db)):
    await require_feature_access(db, user, feature="user_profile", action="analyze")
    user_id = user["user_id"]
    cur = await db.execute(
        """SELECT mood FROM journal_entries
           WHERE user_id = ? AND deleted = 0 AND mood IS NOT NULL AND mood != ''
           ORDER BY created_at DESC LIMIT 200""",
        (user_id,),
    )
    moods = [r["mood"] for r in await cur.fetchall()]
    mood_counts = Counter(moods)
    top_moods = [{"type": k, "count": v} for k, v in mood_counts.most_common(5)]

    cur = await db.execute(
        """SELECT category FROM notes
           WHERE user_id = ? AND deleted = 0 AND category IS NOT NULL
           ORDER BY updated_at DESC LIMIT 200""",
        (user_id,),
    )
    cats = [r["category"] for r in await cur.fetchall()]
    cat_counts = Counter(cats)
    top_topics = [{"type": k, "count": v} for k, v in cat_counts.most_common(5)]

    insights = {"observed_patterns": top_moods + top_topics}
    payload = json.dumps(insights)
    try:
        from memory_service import MemoryServiceError, get_memory_service
        try:
            # entity_id is stable per user so the idempotency key dedupes
            # repeated analysis runs on the same content into one row.
            await get_memory_service().ingest(
                payload,
                user_id=user_id,
                source="profile-analysis",
                memory_type="profile",
                confidence=0.8,
                status="approved",
                tags=["profile", "analysis"],
                entity_type="profile",
                entity_id=user_id,
                user_turn_id=f"profile-analysis-{user_id}",
            )
        except MemoryServiceError as exc:
            import logging as _lg
            _lg.getLogger(__name__).info(
                "user_profile: memory ingest skipped: %s", exc
            )
    except Exception:
        pass
    return {"analysis": insights, "status": "ok"}
