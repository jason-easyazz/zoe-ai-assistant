"""
Journey Management Router
Handles bucket list journeys with stops/stages and check-ins
"""
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date
import sqlite3
import json
import os

router = APIRouter(prefix="/api/journeys", tags=["journeys"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_journey_db():
    """Initialize journey tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Journeys table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journeys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            bucket_list_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            start_date DATE,
            end_date DATE,
            status TEXT DEFAULT 'planning',
            cover_photo TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bucket_list_id) REFERENCES lists(id)
        )
    """)
    
    # Journey stops table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journey_stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journey_id INTEGER NOT NULL,
            stop_order INTEGER,
            title TEXT NOT NULL,
            location TEXT,
            location_coords JSON,
            planned_date DATE,
            actual_date DATE,
            status TEXT DEFAULT 'upcoming',
            checkin_entry_id INTEGER,
            emoji TEXT DEFAULT '📍',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (journey_id) REFERENCES journeys(id) ON DELETE CASCADE,
            FOREIGN KEY (checkin_entry_id) REFERENCES journal_entries(id)
        )
    """)
    
    # Indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journeys_user ON journeys(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journeys_status ON journeys(status, user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journey_stops_journey ON journey_stops(journey_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journey_stops_status ON journey_stops(status)")
    
    conn.commit()
    conn.close()

# Initialize on import
init_journey_db()

# Request/Response Models
class LocationCoords(BaseModel):
    lat: float
    lng: float

class JourneyCreate(BaseModel):
    title: str
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    cover_photo: Optional[str] = None
    bucket_list_id: Optional[int] = None

class JourneyStopCreate(BaseModel):
    title: str
    location: Optional[str] = None
    location_coords: Optional[LocationCoords] = None
    planned_date: Optional[str] = None
    emoji: Optional[str] = "📍"
    notes: Optional[str] = None

class JourneyStopUpdate(BaseModel):
    title: Optional[str] = None
    location: Optional[str] = None
    location_coords: Optional[LocationCoords] = None
    planned_date: Optional[str] = None
    actual_date: Optional[str] = None
    status: Optional[str] = None
    emoji: Optional[str] = None
    notes: Optional[str] = None

class CheckInCreate(BaseModel):
    title: str
    content: str
    photos: Optional[List[str]] = None
    mood: Optional[str] = None
    place_tags: Optional[List[Dict]] = None

@router.post("/from-bucket-item/{item_id}")
async def create_journey_from_bucket_item(
    item_id: int,
    session: AuthenticatedSession = Depends(validate_session)
    """Convert a bucket list item to a journey"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get bucket list item
    cursor.execute("""
        user_id = session.user_id
        SELECT li.task_text, l.id as list_id, l.name as list_name
        FROM list_items li
        JOIN lists l ON li.list_id = l.id
        WHERE li.id = ? AND l.user_id = ? AND l.list_type = 'bucket'
    """, (item_id, user_id))
    
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Bucket list item not found"
        )
    
    task_text, list_id, list_name = row
    
    # Create journey
    cursor.execute("""
        INSERT INTO journeys (user_id, bucket_list_id, title, description, status)
        VALUES (?, ?, ?, ?, 'planning')
    """, (user_id, list_id, task_text, f"Journey from bucket list: {list_name}"))
    
    journey_id = cursor.lastrowid
    
    # Mark bucket item as converted
    cursor.execute("""
        UPDATE list_items 
        SET metadata = json_set(
            COALESCE(metadata, '{}'),
            '$.converted_to_journey', ?
        )
        WHERE id = ?
    """, (journey_id, item_id))
    
    conn.commit()
    conn.close()
    
    return {
        "journey_id": journey_id,
        "title": task_text,
        "message": "Journey created from bucket list item",
        "redirect_to": f"/journeys/{journey_id}"
    }

@router.post("")
async def create_journey(
    journey: JourneyCreate,
    session: AuthenticatedSession = Depends(validate_session)
    """Create a new journey"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        user_id = session.user_id
        INSERT INTO journeys (
            user_id, title, description, start_date, end_date,
            cover_photo, bucket_list_id, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'planning')
    """, (
        user_id, journey.title, journey.description,
        journey.start_date, journey.end_date,
        journey.cover_photo, journey.bucket_list_id
    ))
    
    journey_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {
        "journey_id": journey_id,
        "message": "Journey created successfully",
        **journey.dict()
    }

@router.get("")
async def get_journeys(
    status: Optional[str] = Query(None, description="Filter by status"),
    session: AuthenticatedSession = Depends(validate_session)
    """Get all journeys for a user"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        user_id = session.user_id
        SELECT j.id, j.title, j.description, j.start_date, j.end_date,
               j.status, j.cover_photo, j.bucket_list_id, j.created_at, j.updated_at,
               (SELECT COUNT(*) FROM journey_stops WHERE journey_id = j.id) as stop_count,
               (SELECT COUNT(*) FROM journey_stops WHERE journey_id = j.id AND status = 'completed') as completed_stops,
               (SELECT COUNT(*) FROM journal_entries WHERE journey_id = j.id) as entry_count
        FROM journeys j
        WHERE j.user_id = ?
    """
    params = [user_id]
    
    if status:
        query += " AND j.status = ?"
        params.append(status)
    
    query += " ORDER BY j.created_at DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    journeys = []
    for row in rows:
        journey = {
            "id": row[0],
            "title": row[1],
            "description": row[2],
            "start_date": row[3],
            "end_date": row[4],
            "status": row[5],
            "cover_photo": row[6],
            "bucket_list_id": row[7],
            "created_at": row[8],
            "updated_at": row[9],
            "stop_count": row[10],
            "completed_stops": row[11],
            "entry_count": row[12],
            "progress_percentage": int((row[11] / row[10] * 100) if row[10] > 0 else 0)
        }
        journeys.append(journey)
    
    return {"journeys": journeys, "count": len(journeys)}

@router.get("/{journey_id}")
async def get_journey(
    journey_id: int,
    session: AuthenticatedSession = Depends(validate_session)
    """Get journey details with all stops and entries"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get journey
    cursor.execute("""
        user_id = session.user_id
        SELECT id, title, description, start_date, end_date,
               status, cover_photo, bucket_list_id, created_at, updated_at
        FROM journeys
        WHERE id = ? AND user_id = ?
    """, (journey_id, user_id))
    
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Journey not found")
    
    journey = {
        "id": row[0],
        "title": row[1],
        "description": row[2],
        "start_date": row[3],
        "end_date": row[4],
        "status": row[5],
        "cover_photo": row[6],
        "bucket_list_id": row[7],
        "created_at": row[8],
        "updated_at": row[9]
    }
    
    # Get stops
    cursor.execute("""
        SELECT id, stop_order, title, location, location_coords,
               planned_date, actual_date, status, checkin_entry_id,
               emoji, notes, created_at
        FROM journey_stops
        WHERE journey_id = ?
        ORDER BY stop_order, created_at
    """, (journey_id,))
    
    stops = []
    for stop_row in cursor.fetchall():
        stops.append({
            "id": stop_row[0],
            "stop_order": stop_row[1],
            "title": stop_row[2],
            "location": stop_row[3],
            "location_coords": json.loads(stop_row[4]) if stop_row[4] else None,
            "planned_date": stop_row[5],
            "actual_date": stop_row[6],
            "status": stop_row[7],
            "checkin_entry_id": stop_row[8],
            "emoji": stop_row[9] or "📍",
            "notes": stop_row[10],
            "created_at": stop_row[11]
        })
    
    # Get journal entries
    cursor.execute("""
        SELECT id, title, content, created_at, photos, mood, journey_stop_id
        FROM journal_entries
        WHERE journey_id = ?
        ORDER BY created_at DESC
    """, (journey_id,))
    
    entries = []
    for entry_row in cursor.fetchall():
        entries.append({
            "id": entry_row[0],
            "title": entry_row[1],
            "content": entry_row[2][:200] + "..." if len(entry_row[2]) > 200 else entry_row[2],
            "created_at": entry_row[3],
            "photos": json.loads(entry_row[4]) if entry_row[4] else [],
            "mood": entry_row[5],
            "journey_stop_id": entry_row[6]
        })
    
    conn.close()
    
    # Calculate progress
    completed_stops = sum(1 for s in stops if s["status"] == "completed")
    journey["progress_percentage"] = int((completed_stops / len(stops) * 100) if stops else 0)
    journey["stops"] = stops
    journey["entries"] = entries
    
    return journey

@router.post("/{journey_id}/stops")
async def add_journey_stop(
    journey_id: int,
    stop: JourneyStopCreate,
    session: AuthenticatedSession = Depends(validate_session)
    """Add a stop to a journey"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Verify journey exists
    cursor.execute("SELECT id FROM journeys WHERE id = ? AND user_id = ?", (journey_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Journey not found")
    
    # Get next stop order
    cursor.execute("SELECT MAX(stop_order) FROM journey_stops WHERE journey_id = ?", (journey_id,))
    max_order = cursor.fetchone()[0]
    next_order = (max_order or 0) + 1
    
    # Insert stop
    cursor.execute("""
        user_id = session.user_id
        INSERT INTO journey_stops (
            journey_id, stop_order, title, location, location_coords,
            planned_date, emoji, notes, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'upcoming')
    """, (
        journey_id, next_order, stop.title, stop.location,
        json.dumps(stop.location_coords.dict()) if stop.location_coords else None,
        stop.planned_date, stop.emoji or "📍", stop.notes
    ))
    
    stop_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {
        "stop_id": stop_id,
        "stop_order": next_order,
        "message": "Journey stop added successfully"
    }

@router.put("/{journey_id}/stops/{stop_id}")
async def update_journey_stop(
    journey_id: int,
    stop_id: int,
    stop_update: JourneyStopUpdate,
    session: AuthenticatedSession = Depends(validate_session)
    """Update a journey stop"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Verify journey and stop exist
    cursor.execute("""
        user_id = session.user_id
        SELECT js.id FROM journey_stops js
        JOIN journeys j ON js.journey_id = j.id
        WHERE js.id = ? AND js.journey_id = ? AND j.user_id = ?
    """, (stop_id, journey_id, user_id))
    
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Journey stop not found")
    
    # Build update query
    update_fields = []
    params = []
    
    for field, value in stop_update.dict(exclude_unset=True).items():
        if field == "location_coords" and value:
            update_fields.append(f"{field} = ?")
            params.append(json.dumps(value))
        else:
            update_fields.append(f"{field} = ?")
            params.append(value)
    
    if update_fields:
        params.extend([stop_id, journey_id])
        query = f"UPDATE journey_stops SET {', '.join(update_fields)} WHERE id = ? AND journey_id = ?"
        cursor.execute(query, params)
    
    conn.commit()
    conn.close()
    
    return {"message": "Journey stop updated successfully"}

@router.post("/{journey_id}/checkin")
async def journey_checkin(
    journey_id: int,
    checkin: CheckInCreate,
    session: AuthenticatedSession = Depends(validate_session)
    """Create a journal entry for current journey stop and advance"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get current stop
    cursor.execute("""
        user_id = session.user_id
        SELECT id, title FROM journey_stops
        WHERE journey_id = ? AND status = 'current'
        ORDER BY stop_order
        LIMIT 1
    """, (journey_id,))
    
    current_stop = cursor.fetchone()
    
    if not current_stop:
        # If no current stop, get first upcoming
        cursor.execute("""
            SELECT id, title FROM journey_stops
            WHERE journey_id = ? AND status = 'upcoming'
            ORDER BY stop_order
            LIMIT 1
        """, (journey_id,))
        current_stop = cursor.fetchone()
        
        if current_stop:
            # Mark as current
            cursor.execute("""
                UPDATE journey_stops SET status = 'current'
                WHERE id = ?
            """, (current_stop[0],))
    
    if not current_stop:
        conn.close()
        raise HTTPException(status_code=400, detail="No active stop found for check-in")
    
    stop_id, stop_title = current_stop
    
    # Calculate word count
    word_count = len(checkin.content.split())
    read_time = max(1, word_count // 200)
    
    # Create journal entry
    cursor.execute("""
        INSERT INTO journal_entries (
            user_id, title, content, photos, mood, place_tags,
            journey_id, journey_stop_id, is_journey_checkin,
            word_count, read_time_minutes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, (
        user_id, checkin.title, checkin.content,
        json.dumps(checkin.photos) if checkin.photos else None,
        checkin.mood,
        json.dumps(checkin.place_tags) if checkin.place_tags else None,
        journey_id, stop_id, word_count, read_time
    ))
    
    entry_id = cursor.lastrowid
    
    # Update stop with check-in
    cursor.execute("""
        UPDATE journey_stops
        SET checkin_entry_id = ?, actual_date = DATE('now'), status = 'completed'
        WHERE id = ?
    """, (entry_id, stop_id))
    
    # Set next stop as current
    cursor.execute("""
        UPDATE journey_stops
        SET status = 'current'
        WHERE journey_id = ? AND status = 'upcoming'
        ORDER BY stop_order
        LIMIT 1
    """, (journey_id,))
    
    # Update journey status to active if it was planning
    cursor.execute("""
        UPDATE journeys SET status = 'active'
        WHERE id = ? AND status = 'planning'
    """, (journey_id,))
    
    conn.commit()
    conn.close()
    
    return {
        "entry_id": entry_id,
        "stop_id": stop_id,
        "message": "Check-in recorded successfully",
        "next_stop_activated": cursor.rowcount > 0
    }

@router.delete("/{journey_id}")
async def delete_journey(
    journey_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a journey and all its stops"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM journeys WHERE id = ? AND user_id = ?", (journey_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Journey not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Journey deleted successfully"}




