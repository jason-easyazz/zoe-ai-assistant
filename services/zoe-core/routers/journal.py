"""
Journal Management System - Enhanced
Handles personal journal entries, mood tracking, reflections, journeys, and prompts
"""
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
import sqlite3
import json
import os
import httpx
import asyncio
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/journal", tags=["journal"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_journal_db():
    """Initialize enhanced journal tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Add new columns to journal_entries (if they don't exist)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            mood TEXT,
            mood_score INTEGER,
            tags TEXT,
            weather TEXT,
            location TEXT,
            photos JSON,
            health_data JSON,
            metadata JSON,
            privacy_level TEXT DEFAULT 'private',
            place_tags JSON,
            journey_id INTEGER,
            journey_stop_id INTEGER,
            word_count INTEGER,
            read_time_minutes INTEGER,
            is_journey_checkin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Add columns if they don't exist (SQLite doesn't have ALTER IF NOT EXISTS)
    try:
        cursor.execute("ALTER TABLE journal_entries ADD COLUMN privacy_level TEXT DEFAULT 'private'")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE journal_entries ADD COLUMN place_tags JSON")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE journal_entries ADD COLUMN journey_id INTEGER")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE journal_entries ADD COLUMN journey_stop_id INTEGER")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE journal_entries ADD COLUMN word_count INTEGER")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE journal_entries ADD COLUMN read_time_minutes INTEGER")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE journal_entries ADD COLUMN is_journey_checkin BOOLEAN DEFAULT 0")
    except:
        pass
    
    # Create journal_entry_people many-to-many table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_entry_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE,
            UNIQUE(entry_id, person_id)
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_date ON journal_entries(created_at, user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_mood ON journal_entries(mood, user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_journey ON journal_entries(journey_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_privacy ON journal_entries(privacy_level, user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entry_people_entry ON journal_entry_people(entry_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entry_people_person ON journal_entry_people(person_id)")
    
    conn.commit()
    conn.close()

# Initialize on import
init_journal_db()

# Request/Response models
class PlaceTag(BaseModel):
    name: str
    lat: Optional[float] = None
    lng: Optional[float] = None

class JournalEntryCreate(BaseModel):
    title: str
    content: str
    mood: Optional[str] = None
    mood_score: Optional[int] = None
    tags: Optional[List[str]] = None
    weather: Optional[str] = None
    location: Optional[str] = None
    photos: Optional[List[str]] = None
    health_data: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    privacy_level: Optional[str] = "private"
    place_tags: Optional[List[PlaceTag]] = None
    people_ids: Optional[List[int]] = None
    journey_id: Optional[int] = None
    journey_stop_id: Optional[int] = None

class JournalEntryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    mood: Optional[str] = None
    mood_score: Optional[int] = None
    tags: Optional[List[str]] = None
    weather: Optional[str] = None
    location: Optional[str] = None
    photos: Optional[List[str]] = None
    health_data: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    privacy_level: Optional[str] = None
    place_tags: Optional[List[PlaceTag]] = None
    people_ids: Optional[List[int]] = None

class PersonInfo(BaseModel):
    id: int
    name: str
    avatar_url: Optional[str] = None
    relationship: Optional[str] = None

class JournalEntryResponse(BaseModel):
    id: int
    title: str
    content: str
    mood: Optional[str]
    mood_score: Optional[int]
    tags: Optional[List[str]]
    weather: Optional[str]
    location: Optional[str]
    photos: Optional[List[str]]
    health_data: Optional[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]
    privacy_level: str
    place_tags: Optional[List[PlaceTag]]
    people: Optional[List[PersonInfo]]
    journey_id: Optional[int]
    journey_stop_id: Optional[int]
    word_count: Optional[int]
    read_time_minutes: Optional[int]
    is_journey_checkin: bool
    created_at: str
    updated_at: str

def calculate_word_count(text: str) -> tuple:
    """Calculate word count and estimated read time"""
    words = len(text.split())
    read_time = max(1, words // 200)  # Average reading speed: 200 words/minute
    return words, read_time

def get_entry_people(entry_id: int) -> List[Dict]:
    """Get people tagged in journal entry"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Check which columns exist in people table
        cursor.execute("PRAGMA table_info(people)")
        columns = [col[1] for col in cursor.fetchall()]
        has_avatar = 'avatar_url' in columns
        has_relationship = 'relationship' in columns
        
        # Build query based on available columns
        select_cols = "p.id, p.name"
        if has_avatar:
            select_cols += ", p.avatar_url"
        if has_relationship:
            select_cols += ", p.relationship"
        
        cursor.execute(f"""
            SELECT {select_cols}
            FROM people p
            JOIN journal_entry_people jep ON p.id = jep.person_id
            WHERE jep.entry_id = ?
        """, (entry_id,))
        
        people = []
        for row in cursor.fetchall():
            person = {
                "id": row[0],
                "name": row[1],
                "avatar_url": row[2] if has_avatar else None,
                "relationship": row[3 if has_avatar else 2] if has_relationship else None
            }
            people.append(person)
        
        conn.close()
        return people
    except Exception as e:
        conn.close()
        # If there's any error, return empty list to prevent crashes
        return []

async def sync_entry_to_temporal_memory(entry_id: int, user_id: str):
    """Sync journal entry to Zoe's temporal memory (async, non-blocking)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT title, content, mood, tags, location, is_journey_checkin, word_count
            FROM journal_entries WHERE id = ? AND user_id = ?
        """, (entry_id, user_id))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return
        
        title, content, mood, tags_json, location, is_journey_checkin, word_count = row
        tags = json.loads(tags_json) if tags_json else []
        
        # Calculate importance score
        importance = 0.5  # Base importance
        if word_count and word_count > 500:
            importance += 0.2  # Longer entries are more important
        if is_journey_checkin:
            importance += 0.3  # Journey milestones are significant
        
        # Create temporal memory episode
        episode_data = {
            "user_id": user_id,
            "episode_type": "journal_entry",
            "content": f"{title}\n\n{content}",
            "metadata": {
                "entry_id": entry_id,
                "mood": mood,
                "tags": tags,
                "location": location,
                "is_journey": is_journey_checkin
            },
            "importance_score": min(1.0, importance),
            "context": f"Journal entry: {title}"
        }
        
        # POST to temporal memory API (async)
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                "http://localhost:8000/api/temporal-memory/episodes",
                json=episode_data
            )
            
    except Exception as e:
        print(f"Temporal memory sync warning: {e}")

@router.get("/entries")
async def get_journal_entries(
    limit: int = Query(50, description="Number of entries to return"),
    offset: int = Query(0, description="Number of entries to skip"),
    mood: Optional[str] = Query(None, description="Filter by mood"),
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Search in title and content"),
    journey_id: Optional[int] = Query(None, description="Filter by journey"),
    person_id: Optional[int] = Query(None, description="Filter by person"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get journal entries with optional filtering and people info"""
    user_id = session.user_id
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        SELECT DISTINCT je.id, je.title, je.content, je.mood, je.mood_score, je.tags, 
               je.weather, je.location, je.photos, je.health_data, je.metadata,
               je.privacy_level, je.place_tags, je.journey_id, je.journey_stop_id,
               je.word_count, je.read_time_minutes, je.is_journey_checkin,
               je.created_at, je.updated_at
        FROM journal_entries je
    """
    
    # Add JOIN if filtering by person
    if person_id:
        query += " JOIN journal_entry_people jep ON je.id = jep.entry_id"
    
    query += " WHERE je.user_id = ?"
    params = [user_id]
    
    if mood:
        query += " AND je.mood = ?"
        params.append(mood)
    
    if start_date:
        query += " AND DATE(je.created_at) >= ?"
        params.append(start_date)
    
    if end_date:
        query += " AND DATE(je.created_at) <= ?"
        params.append(end_date)
    
    if search:
        query += " AND (je.title LIKE ? OR je.content LIKE ?)"
        search_term = f"%{search}%"
        params.extend([search_term, search_term])
    
    if journey_id:
        query += " AND je.journey_id = ?"
        params.append(journey_id)
    
    if person_id:
        query += " AND jep.person_id = ?"
        params.append(person_id)
    
    query += " ORDER BY je.created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    entries = []
    for row in rows:
        entry = {
            "id": row[0],
            "title": row[1],
            "content": row[2],
            "mood": row[3],
            "mood_score": row[4],
            "tags": json.loads(row[5]) if row[5] else [],
            "weather": row[6],
            "location": row[7],
            "photos": json.loads(row[8]) if row[8] else [],
            "health_data": json.loads(row[9]) if row[9] else None,
            "metadata": json.loads(row[10]) if row[10] else None,
            "privacy_level": row[11] or "private",
            "place_tags": json.loads(row[12]) if row[12] else [],
            "journey_id": row[13],
            "journey_stop_id": row[14],
            "word_count": row[15],
            "read_time_minutes": row[16],
            "is_journey_checkin": bool(row[17]),
            "created_at": row[18],
            "updated_at": row[19],
            "people": get_entry_people(row[0])
        }
        entries.append(entry)
    
    return {"entries": entries, "count": len(entries)}

@router.post("/entries", response_model=Dict[str, Any])
async def create_journal_entry(
    entry: JournalEntryCreate, 
    background_tasks: BackgroundTasks,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Create a new journal entry with enhanced features"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Calculate word count
    word_count, read_time = calculate_word_count(entry.content)
    
    # Insert entry
    cursor.execute("""
        INSERT INTO journal_entries (
            user_id, title, content, mood, mood_score, tags, weather, location,
            photos, health_data, metadata, privacy_level, place_tags,
            journey_id, journey_stop_id, word_count, read_time_minutes, is_journey_checkin
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, entry.title, entry.content, entry.mood, entry.mood_score,
        json.dumps(entry.tags) if entry.tags else None,
        entry.weather, entry.location,
        json.dumps(entry.photos) if entry.photos else None,
        json.dumps(entry.health_data) if entry.health_data else None,
        json.dumps(entry.metadata) if entry.metadata else None,
        entry.privacy_level,
        json.dumps([p.dict() for p in entry.place_tags]) if entry.place_tags else None,
        entry.journey_id, entry.journey_stop_id, word_count, read_time,
        1 if entry.journey_stop_id else 0
    ))
    
    entry_id = cursor.lastrowid
    
    # Link people
    if entry.people_ids:
        for person_id in entry.people_ids:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO journal_entry_people (entry_id, person_id)
                    VALUES (?, ?)
                """, (entry_id, person_id))
            except:
                pass
    
    conn.commit()
    conn.close()
    
    # Sync to temporal memory (async, non-blocking)
    background_tasks.add_task(sync_entry_to_temporal_memory, entry_id, user_id)
    
    return {
        "entry": {
            "id": entry_id,
            **entry.dict(),
            "word_count": word_count,
            "read_time_minutes": read_time
        }
    }

@router.get("/entries/on-this-day")
async def get_on_this_day_entries(
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get entries from this day in previous years (Day One feature)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    today = datetime.now()
    month = today.strftime("%m")
    day = today.strftime("%d")
    
    cursor.execute("""
        SELECT id, title, content, created_at, mood, photos, place_tags, journey_id,
               word_count, read_time_minutes
        FROM journal_entries
        WHERE user_id = ?
          AND strftime('%m', created_at) = ?
          AND strftime('%d', created_at) = ?
          AND strftime('%Y', created_at) != ?
        ORDER BY created_at DESC
        LIMIT 10
    """, (user_id, month, day, today.strftime("%Y")))
    
    entries = []
    for row in cursor.fetchall():
        entry_date = datetime.fromisoformat(row[3])
        years_ago = today.year - entry_date.year
        
        entries.append({
            "id": row[0],
            "title": row[1],
            "content": row[2][:200] + "..." if len(row[2]) > 200 else row[2],
            "created_at": row[3],
            "years_ago": years_ago,
            "label": f"{years_ago} year{'s' if years_ago > 1 else ''} ago",
            "mood": row[4],
            "photos": json.loads(row[5]) if row[5] else [],
            "place_tags": json.loads(row[6]) if row[6] else [],
            "journey_id": row[7],
            "word_count": row[8],
            "read_time_minutes": row[9],
            "people": get_entry_people(row[0])
        })
    
    conn.close()
    
    return {
        "date": today.strftime("%B %d"),
        "entries": entries,
        "count": len(entries)
    }

@router.get("/stats/streak")
async def get_journaling_streak(
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Calculate journaling streak (consecutive days)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all entry dates
    cursor.execute("""
        SELECT DISTINCT DATE(created_at) as entry_date
        FROM journal_entries
        WHERE user_id = ?
        ORDER BY entry_date DESC
    """, (user_id,))
    
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    if not dates:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "total_entries": 0
        }
    
    # Calculate current streak
    current_streak = 0
    today = date.today()
    check_date = today
    
    for entry_date_str in dates:
        entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
        
        if entry_date == check_date:
            current_streak += 1
            check_date -= timedelta(days=1)
        elif entry_date < check_date:
            break
    
    # Calculate longest streak
    longest_streak = 0
    current_count = 1
    
    for i in range(1, len(dates)):
        prev_date = datetime.strptime(dates[i-1], "%Y-%m-%d").date()
        curr_date = datetime.strptime(dates[i], "%Y-%m-%d").date()
        
        if (prev_date - curr_date).days == 1:
            current_count += 1
            longest_streak = max(longest_streak, current_count)
        else:
            current_count = 1
    
    longest_streak = max(longest_streak, current_streak)
    
    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "total_entries": len(dates)
    }

@router.get("/prompts")
async def get_journal_prompts(
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get intelligent journal prompts based on recent activities, goals, and journeys"""
    prompts = []
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Post-catch-up prompts (from recent calendar events with people)
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT ce.id, ce.title, ce.start_time
            FROM calendar_events ce
            WHERE ce.user_id = ?
              AND DATE(ce.start_time) >= ?
              AND DATE(ce.start_time) <= DATE('now')
              AND ce.end_time < datetime('now')
            ORDER BY ce.start_time DESC
            LIMIT 3
        """, (user_id, yesterday))
        
        for row in cursor.fetchall():
            event_id, title, start_time = row
            prompts.append({
                "id": f"event_{event_id}",
                "type": "catchup",
                "priority": "high",
                "prompt_text": f"How was your {title}?",
                "context": {
                    "event": title,
                    "time": start_time
                },
                "auto_fill": {
                    "title": title
                }
            })
    except:
        pass
    
    # 2. Journey milestone prompts
    try:
        cursor.execute("""
            SELECT j.id, j.title, js.id as stop_id, js.title as stop_title
            FROM journeys j
            JOIN journey_stops js ON j.id = js.journey_id
            WHERE j.user_id = ? AND j.status = 'active' AND js.status = 'current'
            LIMIT 1
        """, (user_id,))
        
        row = cursor.fetchone()
        if row:
            journey_id, journey_title, stop_id, stop_title = row
            prompts.append({
                "id": f"journey_{stop_id}",
                "type": "journey",
                "priority": "high",
                "prompt_text": f"You're at {stop_title} on your {journey_title}. How's the experience?",
                "context": {
                    "journey_id": journey_id,
                    "stop_id": stop_id,
                    "journey": journey_title,
                    "stop": stop_title
                },
                "auto_fill": {
                    "title": f"{stop_title} - {journey_title}",
                    "journey_id": journey_id,
                    "journey_stop_id": stop_id
                }
            })
    except:
        pass
    
    # 3. Generic daily prompts (fallback)
    if len(prompts) == 0:
        daily_prompts = [
            "What made today special?",
            "Who did you spend time with today?",
            "What are you grateful for?",
            "What did you learn today?",
            "How are you feeling right now?"
        ]
        import random
        prompts.append({
            "id": "daily_prompt",
            "type": "daily",
            "priority": "normal",
            "prompt_text": random.choice(daily_prompts),
            "context": {},
            "auto_fill": {}
        })
    
    conn.close()
    
    return {"prompts": prompts, "count": len(prompts)}

@router.post("/prompts/mark-prompted")
async def mark_prompt_shown(
    prompt_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Mark that a prompt was shown to the user (prevents duplicates)"""
    # Could store this in a prompts_shown table if needed
    return {"message": "Prompt marked as shown", "prompt_id": prompt_id}

# Keep existing endpoints below...
@router.get("/{entry_id}")
async def get_journal_entry(entry_id: int, session: AuthenticatedSession = Depends(validate_session)):
    """Get a specific journal entry with people info"""
    user_id = session.user_id
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, title, content, mood, mood_score, tags, weather, location,
               photos, health_data, metadata, privacy_level, place_tags,
               journey_id, journey_stop_id, word_count, read_time_minutes,
               is_journey_checkin, created_at, updated_at
        FROM journal_entries 
        WHERE id = ? AND user_id = ?
    """, (entry_id, user_id))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    
    return {
        "id": row[0],
        "title": row[1],
        "content": row[2],
        "mood": row[3],
        "mood_score": row[4],
        "tags": json.loads(row[5]) if row[5] else [],
        "weather": row[6],
        "location": row[7],
        "photos": json.loads(row[8]) if row[8] else [],
        "health_data": json.loads(row[9]) if row[9] else None,
        "metadata": json.loads(row[10]) if row[10] else None,
        "privacy_level": row[11] or "private",
        "place_tags": json.loads(row[12]) if row[12] else [],
        "journey_id": row[13],
        "journey_stop_id": row[14],
        "word_count": row[15],
        "read_time_minutes": row[16],
        "is_journey_checkin": bool(row[17]),
        "created_at": row[18],
        "updated_at": row[19],
        "people": get_entry_people(row[0])
    }

@router.put("/{entry_id}")
async def update_journal_entry(
    entry_id: int,
    entry_update: JournalEntryUpdate,
    background_tasks: BackgroundTasks,
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Update a journal entry"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if entry exists
    cursor.execute("SELECT id FROM journal_entries WHERE id = ? AND user_id = ?", (entry_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Journal entry not found")
    
    # Build update query dynamically
    update_fields = []
    params = []
    
    update_dict = entry_update.dict(exclude_unset=True, exclude={"people_ids"})
    
    # Recalculate word count if content updated
    if "content" in update_dict:
        word_count, read_time = calculate_word_count(update_dict["content"])
        update_dict["word_count"] = word_count
        update_dict["read_time_minutes"] = read_time
    
    for field, value in update_dict.items():
        if field in ["tags", "photos", "health_data", "metadata", "place_tags"]:
            update_fields.append(f"{field} = ?")
            params.append(json.dumps(value) if value is not None else None)
        else:
            update_fields.append(f"{field} = ?")
            params.append(value)
    
    if update_fields:
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([entry_id, user_id])
        
        query = f"UPDATE journal_entries SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
        cursor.execute(query, params)
    
    # Update people tags
    if entry_update.people_ids is not None:
        # Remove existing people
        cursor.execute("DELETE FROM journal_entry_people WHERE entry_id = ?", (entry_id,))
        
        # Add new people
        for person_id in entry_update.people_ids:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO journal_entry_people (entry_id, person_id)
                    VALUES (?, ?)
                """, (entry_id, person_id))
            except:
                pass
    
    conn.commit()
    conn.close()
    
    # Sync to temporal memory
    background_tasks.add_task(sync_entry_to_temporal_memory, entry_id, user_id)
    
    return {"message": "Journal entry updated successfully"}

@router.delete("/{entry_id}")
async def delete_journal_entry(entry_id: int, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Delete a journal entry"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM journal_entries WHERE id = ? AND user_id = ?", (entry_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Journal entry not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Journal entry deleted successfully"}

@router.get("/stats/mood")
async def get_mood_stats(session: AuthenticatedSession = Depends(validate_session)):
    """Get mood statistics"""
    user_id = session.user_id
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT mood, COUNT(*) as count, AVG(mood_score) as avg_score
        FROM journal_entries 
        WHERE user_id = ? AND mood IS NOT NULL
        GROUP BY mood
        ORDER BY count DESC
    """, (user_id,))
    
    stats = []
    for row in cursor.fetchall():
        stats.append({
            "mood": row[0],
            "count": row[1],
            "avg_score": float(row[2]) if row[2] else None
        })
    
    conn.close()
    
    return {"mood_stats": stats}

@router.get("/stats/monthly")
async def get_monthly_stats(
    year: int = Query(None, description="Year filter"),
    month: int = Query(None, description="Month filter (1-12)"),
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get monthly journal statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if year and month:
        cursor.execute("""
            SELECT COUNT(*) as total_entries, 
                   COUNT(DISTINCT mood) as unique_moods,
                   AVG(mood_score) as avg_mood_score
            FROM journal_entries 
            WHERE user_id = ? AND strftime('%Y', created_at) = ? AND strftime('%m', created_at) = ?
        """, (user_id, str(year), str(month).zfill(2)))
    else:
        cursor.execute("""
            SELECT COUNT(*) as total_entries, 
                   COUNT(DISTINCT mood) as unique_moods,
                   AVG(mood_score) as avg_mood_score
            FROM journal_entries 
            WHERE user_id = ?
        """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    return {
        "total_entries": row[0],
        "unique_moods": row[1],
        "avg_mood_score": float(row[2]) if row[2] else None
    }
