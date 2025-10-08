from auth_integration import validate_session
"""
Journal Management System
Handles personal journal entries, mood tracking, and reflections
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date
import sqlite3
import json
import os

router = APIRouter(prefix="/api/journal", tags=["journal"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_journal_db():
    """Initialize journal tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_journal_date 
        ON journal_entries(created_at, user_id)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_journal_mood 
        ON journal_entries(mood, user_id)
    """)
    
    conn.commit()
    conn.close()

# Initialize on import
init_journal_db()

# Request/Response models
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
    created_at: str
    updated_at: str

@router.get("/")
async def get_journal_entries(
    limit: int = Query(50, description="Number of entries to return"),
    offset: int = Query(0, description="Number of entries to skip"),
    mood: Optional[str] = Query(None, description="Filter by mood"),
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Search in title and content"),
    user_id: str = Query("default", description="User ID")
):
    """Get journal entries with optional filtering"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        SELECT id, title, content, mood, mood_score, tags, weather, location,
               photos, health_data, metadata, created_at, updated_at
        FROM journal_entries 
        WHERE user_id = ?
    """
    params = [user_id]
    
    if mood:
        query += " AND mood = ?"
        params.append(mood)
    
    if start_date:
        query += " AND DATE(created_at) >= ?"
        params.append(start_date)
    
    if end_date:
        query += " AND DATE(created_at) <= ?"
        params.append(end_date)
    
    if search:
        query += " AND (title LIKE ? OR content LIKE ?)"
        search_term = f"%{search}%"
        params.extend([search_term, search_term])
    
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    entries = []
    for row in rows:
        entries.append({
            "id": row[0],
            "title": row[1],
            "content": row[2],
            "mood": row[3],
            "mood_score": row[4],
            "tags": json.loads(row[5]) if row[5] else None,
            "weather": row[6],
            "location": row[7],
            "photos": json.loads(row[8]) if row[8] else None,
            "health_data": json.loads(row[9]) if row[9] else None,
            "metadata": json.loads(row[10]) if row[10] else None,
            "created_at": row[11],
            "updated_at": row[12]
        })
    
    return {"entries": entries}

@router.post("/")
async def create_journal_entry(entry: JournalEntryCreate, user_id: str = Query("default")):
    """Create a new journal entry"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO journal_entries (user_id, title, content, mood, mood_score, tags,
                                   weather, location, photos, health_data, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, entry.title, entry.content, entry.mood, entry.mood_score,
        json.dumps(entry.tags) if entry.tags else None,
        entry.weather, entry.location,
        json.dumps(entry.photos) if entry.photos else None,
        json.dumps(entry.health_data) if entry.health_data else None,
        json.dumps(entry.metadata) if entry.metadata else None
    ))
    
    entry_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"entry": {"id": entry_id, **entry.dict()}}

@router.get("/{entry_id}")
async def get_journal_entry(entry_id: int, user_id: str = Query("default")):
    """Get a specific journal entry"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, title, content, mood, mood_score, tags, weather, location,
               photos, health_data, metadata, created_at, updated_at
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
        "tags": json.loads(row[5]) if row[5] else None,
        "weather": row[6],
        "location": row[7],
        "photos": json.loads(row[8]) if row[8] else None,
        "health_data": json.loads(row[9]) if row[9] else None,
        "metadata": json.loads(row[10]) if row[10] else None,
        "created_at": row[11],
        "updated_at": row[12]
    }

@router.put("/{entry_id}")
async def update_journal_entry(entry_id: int, entry_update: JournalEntryUpdate, user_id: str = Query("default")):
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
    
    for field, value in entry_update.dict(exclude_unset=True).items():
        if field in ["tags", "photos", "health_data", "metadata"] and value is not None:
            update_fields.append(f"{field} = ?")
            params.append(json.dumps(value))
        else:
            update_fields.append(f"{field} = ?")
            params.append(value)
    
    if update_fields:
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([entry_id, user_id])
        
        query = f"UPDATE journal_entries SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
        cursor.execute(query, params)
        conn.commit()
    
    conn.close()
    
    return {"message": "Journal entry updated successfully"}

@router.delete("/{entry_id}")
async def delete_journal_entry(entry_id: int, user_id: str = Query("default")):
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
async def get_mood_stats(user_id: str = Query("default")):
    """Get mood statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT mood, COUNT(*) as count, AVG(mood_score) as avg_score
        FROM journal_entries 
        WHERE user_id = ? AND mood IS NOT NULL
        GROUP BY mood
        ORDER BY count DESC
    """, (user_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    stats = []
    for row in rows:
        stats.append({
            "mood": row[0],
            "count": row[1],
            "avg_score": float(row[2]) if row[2] else None
        })
    
    return {"mood_stats": stats}

@router.get("/stats/monthly")
async def get_monthly_stats(
    year: int = Query(None, description="Year filter"),
    month: int = Query(None, description="Month filter (1-12)"),
    user_id: str = Query("default", description="User ID")
):
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
