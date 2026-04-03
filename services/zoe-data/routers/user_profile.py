import json
from collections import Counter

from fastapi import APIRouter, Depends

from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/api/user/profile", tags=["user-profile"])


@router.get("")
async def get_user_profile(user: dict = Depends(get_current_user), db=Depends(get_db)):
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
    cur = await db.execute(
        """SELECT id FROM memory_items
           WHERE user_id = ? AND source_type = 'profile-analysis' LIMIT 1""",
        (user_id,),
    )
    existing = await cur.fetchone()
    payload = json.dumps(insights)
    if existing:
        await db.execute(
            """UPDATE memory_items SET content = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (payload, existing["id"]),
        )
    else:
        await db.execute(
            """INSERT INTO memory_items
               (id, user_id, memory_type, title, content, confidence, source_type, status, visibility)
               VALUES (lower(hex(randomblob(16))), ?, 'profile', 'Profile Analysis', ?, 0.8, 'profile-analysis', 'approved', 'personal')""",
            (user_id, payload),
        )
    await db.commit()
    return {"analysis": insights, "status": "ok"}
