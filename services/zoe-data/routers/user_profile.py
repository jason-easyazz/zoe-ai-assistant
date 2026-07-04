import json
import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access

router = APIRouter(prefix="/api/user/profile", tags=["user-profile"])

# A Telegram numeric user id (from ctx.from.id). Telegram ids are positive
# integers; we store the canonical decimal string. Reject anything else so the
# resolver only ever matches a real, verified sender id.
_TELEGRAM_ID_RE = re.compile(r"^[1-9][0-9]{1,19}$")


async def _read_prefs(db, user_id: str) -> dict:
    """Return the user_preferences.prefs JSON dict for user_id (or {})."""
    cursor = await db.execute(
        "SELECT prefs FROM user_preferences WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return {}
    try:
        raw = row["prefs"]
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


async def _write_prefs(db, user_id: str, prefs: dict) -> None:
    await db.execute(
        """INSERT INTO user_preferences (user_id, prefs, updated_at)
           VALUES (?, ?, NOW())
           ON CONFLICT(user_id) DO UPDATE SET prefs = excluded.prefs, updated_at = NOW()""",
        (user_id, json.dumps(prefs)),
    )
    await db.commit()


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


# ─── Telegram account linking ────────────────────────────────────────────────
#
# A user links their Telegram account by storing their numeric telegram_id in
# their own profile (session-authed as themselves). The internal resolver
# GET /api/system/resolve-telegram/{telegram_id} maps that id back to this
# user so the Telegram bot can act as the real Zoe user (with their memory),
# instead of everyone landing as guest.


@router.get("/telegram")
async def get_telegram_link(
    user: dict = Depends(get_current_user), db=Depends(get_db)
):
    """Return the caller's linked telegram_id (or null)."""
    await require_feature_access(db, user, feature="user_profile", action="read")
    prefs = await _read_prefs(db, user["user_id"])
    tid = prefs.get("telegram_id")
    return {"telegram_id": tid if isinstance(tid, str) and tid else None}


@router.put("/telegram")
async def set_telegram_link(
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Link (or unlink) the caller's Telegram account.

    Body: {"telegram_id": "<numeric id>"} to link, or {"telegram_id": null} /
    {"telegram_id": ""} to unlink. Session-authed: the caller can only set the
    telegram_id on THEIR OWN profile — user_id comes from the session, never the
    body, so this can't link someone else's account.

    Uniqueness: a telegram_id maps to at most one Zoe user. Last-writer-wins —
    if another profile already claimed this id, that prior link is cleared so the
    new claimant owns it (a telegram account can only "be" one Zoe user at a time).
    """
    # Writing a link is a real-identity action; guests have no profile to link.
    await require_feature_access(db, user, feature="user_profile", action="analyze")

    body = await request.json()
    raw = body.get("telegram_id")
    user_id = user["user_id"]

    # Unlink path: null / empty clears the caller's own link.
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        prefs = await _read_prefs(db, user_id)
        if "telegram_id" in prefs:
            prefs.pop("telegram_id", None)
            await _write_prefs(db, user_id, prefs)
        return {"telegram_id": None, "linked": False}

    tid = str(raw).strip()
    if not _TELEGRAM_ID_RE.match(tid):
        raise HTTPException(
            status_code=400,
            detail="telegram_id must be a positive numeric Telegram user id",
        )

    # Last-writer-wins uniqueness: clear this telegram_id from any OTHER profile
    # that currently claims it, so at most one user maps to it.
    cursor = await db.execute(
        """SELECT user_id, prefs FROM user_preferences
           WHERE prefs::jsonb ->> 'telegram_id' = ? AND user_id != ?""",
        (tid, user_id),
    )
    for row in await cursor.fetchall():
        other_id = row["user_id"]
        other_prefs = await _read_prefs(db, other_id)
        if other_prefs.get("telegram_id") == tid:
            other_prefs.pop("telegram_id", None)
            await _write_prefs(db, other_id, other_prefs)

    prefs = await _read_prefs(db, user_id)
    prefs["telegram_id"] = tid
    await _write_prefs(db, user_id, prefs)
    return {"telegram_id": tid, "linked": True}
