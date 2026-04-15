"""
FastAPI router for journal entries.
Mounted at prefix="/api/journal" with tag "journal".
"""
import json
import random
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from models import JournalEntryCreate, JournalEntryUpdate
from push import broadcaster

router = APIRouter(prefix="/api/journal", tags=["journal"])

# Default journal prompts for GET /prompts
DEFAULT_PROMPTS = [
    "What was the highlight of your day?",
    "What are you grateful for today?",
    "What challenged you today, and how did you handle it?",
    "Describe one thing you learned today.",
    "How are you feeling right now, and why?",
    "What would you do differently if you could redo today?",
    "What made you smile today?",
    "What are you looking forward to tomorrow?",
    "Describe a moment of connection you had today.",
    "What did you do today that you're proud of?",
]


def _row_to_dict(row) -> dict:
    """Convert aiosqlite Row to dict, parsing tags and photos JSON."""
    if row is None:
        return None
    d = dict(row)
    for field in ("tags", "photos"):
        val = d.get(field)
        if val is not None and isinstance(val, str):
            try:
                d[field] = json.loads(val) if val else None
            except json.JSONDecodeError:
                d[field] = None
    if "deleted" in d and d["deleted"] is not None:
        d["deleted"] = bool(d["deleted"])
    return d


def _visibility_filter_sql() -> str:
    """Journal visibility is always personal - user_id must match."""
    return "user_id = ? AND deleted = 0"


async def _store_journal_memory(db, user_id: str, entry: dict, action: str):
    import asyncio
    content = (entry.get("content") or "")[:800]
    if not content:
        return
    await db.execute(
        """INSERT INTO memory_items
           (id, user_id, memory_type, title, content, entity_type, entity_id, confidence,
            source_type, source_id, source_excerpt, visibility, status)
           VALUES (?, ?, 'journal', ?, ?, 'journal', ?, 0.7, 'journal', ?, ?, 'personal', 'approved')""",
        (
            str(uuid.uuid4()),
            user_id,
            f"{action.title()} journal entry",
            content,
            entry.get("id"),
            entry.get("id"),
            content[:220],
        ),
    )
    # Mirror to MemPalace so agent memory stays current
    try:
        from pi_agent import _mempalace_add  # type: ignore[import]
        mood = entry.get("mood") or ""
        mood_str = f" [mood: {mood}]" if mood else ""
        fact = f"Journal entry{mood_str}: {content[:400]}"
        asyncio.ensure_future(_mempalace_add(fact, user_id=user_id, tags=["journal", action]))
    except Exception:
        pass


@router.get("/entries", response_model=dict)
async def list_entries(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    mood: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List journal entries with optional filters."""
    user_id = user["user_id"]
    conditions = [_visibility_filter_sql()]
    params: list = [user_id]

    if mood:
        conditions.append("mood = ?")
        params.append(mood)
    if start_date:
        conditions.append("date(created_at) >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("date(created_at) <= ?")
        params.append(end_date)
    if search:
        conditions.append("(title LIKE ? OR content LIKE ?)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern])

    where = " AND ".join(conditions)
    params.extend([limit, offset])
    sql = f"SELECT * FROM journal_entries WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    entries = [_row_to_dict(r) for r in rows]
    return {"entries": entries}


@router.post("/entries", response_model=dict)
async def create_entry(
    payload: JournalEntryCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new journal entry. visibility always 'personal'."""
    user_id = user["user_id"]
    entry_id = str(uuid.uuid4())
    tags_json = json.dumps(payload.tags) if payload.tags else None
    photos_json = json.dumps(payload.photos) if payload.photos else None

    await db.execute(
        """INSERT INTO journal_entries (
            id, user_id, title, content, mood, mood_score, tags, weather,
            location, photos, privacy_level, visibility, deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'personal', 0)""",
        (
            entry_id,
            user_id,
            payload.title,
            payload.content,
            payload.mood,
            payload.mood_score,
            tags_json,
            payload.weather,
            payload.location,
            photos_json,
            payload.privacy_level,
        ),
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM journal_entries WHERE id = ?", [entry_id])
    row = await cursor.fetchone()
    entry = _row_to_dict(row)
    await _store_journal_memory(db, user_id, entry, "created")
    await db.commit()

    await broadcaster.broadcast("journal", "entry_created", entry)
    return entry


@router.get("/entries/on-this-day", response_model=dict)
async def list_on_this_day(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Entries from this day in past years."""
    user_id = user["user_id"]
    today_md = date.today().strftime("%m-%d")
    sql = """
        SELECT * FROM journal_entries
        WHERE user_id = ? AND deleted = 0
          AND strftime('%m-%d', created_at) = ?
          AND date(created_at) < date('now')
        ORDER BY created_at DESC
    """
    cursor = await db.execute(sql, [user_id, today_md])
    rows = await cursor.fetchall()
    entries = [_row_to_dict(r) for r in rows]
    return {"entries": entries}


@router.get("/stats/streak", response_model=dict)
async def get_streak_stats(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return {current_streak, longest_streak, total_entries}."""
    from datetime import datetime, timedelta

    user_id = user["user_id"]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM journal_entries WHERE user_id = ? AND deleted = 0",
        [user_id],
    )
    row = await cursor.fetchone()
    total_entries = row[0] if row else 0

    cursor = await db.execute(
        """SELECT DISTINCT date(created_at) as d
         FROM journal_entries
         WHERE user_id = ? AND deleted = 0
         ORDER BY d DESC""",
        [user_id],
    )
    rows = await cursor.fetchall()
    dates_sorted = sorted([r[0] for r in rows if r[0]], reverse=True)

    current_streak = 0
    longest_streak = 0
    if dates_sorted:
        today = date.today().isoformat()
        # Current streak: consecutive days ending today
        for i, d in enumerate(dates_sorted):
            expected = (date.today() - timedelta(days=i)).isoformat()
            if d == expected:
                current_streak += 1
            else:
                break
        # Longest streak
        run = 1
        for i in range(1, len(dates_sorted)):
            curr_d = datetime.strptime(dates_sorted[i], "%Y-%m-%d").date()
            prev_d = datetime.strptime(dates_sorted[i - 1], "%Y-%m-%d").date()
            if (prev_d - curr_d).days == 1:
                run += 1
            else:
                longest_streak = max(longest_streak, run)
                run = 1
        longest_streak = max(longest_streak, run)

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "total_entries": total_entries,
    }


@router.get("/stats/mood", response_model=dict)
async def get_mood_stats(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return mood distribution."""
    user_id = user["user_id"]
    cursor = await db.execute(
        """SELECT mood, COUNT(*) as cnt
         FROM journal_entries
         WHERE user_id = ? AND deleted = 0 AND mood IS NOT NULL AND mood != ''
         GROUP BY mood""",
        [user_id],
    )
    rows = await cursor.fetchall()
    distribution = {r[0]: r[1] for r in rows}
    return {"distribution": distribution}


@router.get("/prompts", response_model=dict)
async def get_prompts(
    user: dict = Depends(get_current_user),
):
    """Return 5 random journal prompts."""
    prompts = random.sample(DEFAULT_PROMPTS, min(5, len(DEFAULT_PROMPTS)))
    return {"prompts": prompts}


@router.get("/{entry_id}", response_model=dict)
async def get_entry(
    entry_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get a single journal entry by ID."""
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT * FROM journal_entries WHERE id = ? AND " + _visibility_filter_sql(),
        [entry_id, user_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return _row_to_dict(row)


@router.put("/{entry_id}", response_model=dict)
async def update_entry(
    entry_id: str,
    payload: JournalEntryUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update an existing journal entry."""
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT * FROM journal_entries WHERE id = ? AND " + _visibility_filter_sql(),
        [entry_id, user_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    updates = []
    params = []
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key in ("tags", "photos"):
            updates.append(f"{key} = ?")
            params.append(json.dumps(value) if value else None)
        else:
            updates.append(f"{key} = ?")
            params.append(value)

    if not updates:
        return _row_to_dict(row)

    updates.append("updated_at = datetime('now')")
    params.append(entry_id)
    await db.execute(
        f"UPDATE journal_entries SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM journal_entries WHERE id = ?", [entry_id])
    row = await cursor.fetchone()
    entry = _row_to_dict(row)
    await _store_journal_memory(db, user_id, entry, "updated")
    await db.commit()

    await broadcaster.broadcast("journal", "entry_updated", entry)
    return entry


@router.delete("/{entry_id}", response_model=dict)
async def delete_entry(
    entry_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete a journal entry."""
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT * FROM journal_entries WHERE id = ? AND " + _visibility_filter_sql(),
        [entry_id, user_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    await db.execute(
        "UPDATE journal_entries SET deleted = 1, updated_at = datetime('now') WHERE id = ?",
        [entry_id],
    )
    await db.commit()

    await broadcaster.broadcast("journal", "entry_deleted", {"id": entry_id})
    return {"ok": True, "id": entry_id}
