"""
Calendar Management System
Handles events, scheduling, and calendar operations
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date
import sqlite3
import json
import os

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_calendar_db():
    """Initialize calendar tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            title TEXT NOT NULL,
            description TEXT,
            start_date DATE NOT NULL,
            start_time TIME,
            end_date DATE,
            end_time TIME,
            category TEXT DEFAULT 'personal',
            location TEXT,
            all_day BOOLEAN DEFAULT FALSE,
            recurring TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_date 
        ON events(start_date, user_id)
    """)
    
    conn.commit()
    conn.close()

# Initialize on import
init_calendar_db()

# Request/Response models
class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    start_date: str
    start_time: Optional[str] = None
    end_date: Optional[str] = None
    end_time: Optional[str] = None
    category: Optional[str] = "personal"
    location: Optional[str] = None
    all_day: Optional[bool] = False
    recurring: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    start_time: Optional[str] = None
    end_date: Optional[str] = None
    end_time: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    all_day: Optional[bool] = None
    recurring: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class EventResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    start_date: str
    start_time: Optional[str]
    end_date: Optional[str]
    end_time: Optional[str]
    category: str
    location: Optional[str]
    all_day: bool
    recurring: Optional[str]
    metadata: Optional[Dict[str, Any]]
    created_at: str
    updated_at: str

@router.get("/events")
async def get_events(
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    category: Optional[str] = Query(None, description="Category filter"),
    user_id: str = Query("default", description="User ID")
):
    """Get events with optional filtering"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        SELECT id, title, description, start_date, start_time, end_date, end_time,
               category, location, all_day, recurring, metadata, created_at, updated_at
        FROM events 
        WHERE user_id = ?
    """
    params = [user_id]
    
    if start_date:
        query += " AND start_date >= ?"
        params.append(start_date)
    
    if end_date:
        query += " AND start_date <= ?"
        params.append(end_date)
    
    if category:
        query += " AND category = ?"
        params.append(category)
    
    query += " ORDER BY start_date, start_time"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        events.append({
            "id": row[0],
            "title": row[1],
            "description": row[2],
            "start_date": row[3],
            "start_time": row[4],
            "end_date": row[5],
            "end_time": row[6],
            "category": row[7],
            "location": row[8],
            "all_day": bool(row[9]),
            "recurring": row[10],
            "metadata": json.loads(row[11]) if row[11] else None,
            "created_at": row[12],
            "updated_at": row[13]
        })
    
    return {"events": events}

@router.post("/events")
async def create_event(event: EventCreate, user_id: str = Query("default")):
    """Create a new event"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO events (user_id, title, description, start_date, start_time, 
                          end_date, end_time, category, location, all_day, recurring, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, event.title, event.description, event.start_date, event.start_time,
        event.end_date, event.end_time, event.category, event.location, 
        event.all_day, event.recurring, json.dumps(event.metadata) if event.metadata else None
    ))
    
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"event": {"id": event_id, **event.dict()}}

@router.get("/events/{event_id}")
async def get_event(event_id: int, user_id: str = Query("default")):
    """Get a specific event"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, title, description, start_date, start_time, end_date, end_time,
               category, location, all_day, recurring, metadata, created_at, updated_at
        FROM events 
        WHERE id = ? AND user_id = ?
    """, (event_id, user_id))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    
    return {
        "id": row[0],
        "title": row[1],
        "description": row[2],
        "start_date": row[3],
        "start_time": row[4],
        "end_date": row[5],
        "end_time": row[6],
        "category": row[7],
        "location": row[8],
        "all_day": bool(row[9]),
        "recurring": row[10],
        "metadata": json.loads(row[11]) if row[11] else None,
        "created_at": row[12],
        "updated_at": row[13]
    }

@router.put("/events/{event_id}")
async def update_event(event_id: int, event_update: EventUpdate, user_id: str = Query("default")):
    """Update an event"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if event exists
    cursor.execute("SELECT id FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Build update query dynamically
    update_fields = []
    params = []
    
    for field, value in event_update.dict(exclude_unset=True).items():
        if field == "metadata" and value is not None:
            update_fields.append(f"{field} = ?")
            params.append(json.dumps(value))
        else:
            update_fields.append(f"{field} = ?")
            params.append(value)
    
    if update_fields:
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([event_id, user_id])
        
        query = f"UPDATE events SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
        cursor.execute(query, params)
        conn.commit()
    
    conn.close()
    
    return {"message": "Event updated successfully"}

@router.delete("/events/{event_id}")
async def delete_event(event_id: int, user_id: str = Query("default")):
    """Delete an event"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Event deleted successfully"}

@router.get("/events/today")
async def get_todays_events(user_id: str = Query("default")):
    """Get today's events"""
    today = date.today().isoformat()
    return await get_events(start_date=today, end_date=today, user_id=user_id)
