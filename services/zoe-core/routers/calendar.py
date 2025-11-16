from auth_integration import validate_session, AuthenticatedSession
"""
Calendar Management System
Handles events, scheduling, and calendar operations with smart scheduling integration
"""
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Set
from datetime import datetime, date, timedelta
import sqlite3
import json
import os
import sys
import asyncio
from dateutil.rrule import rrule, DAILY, WEEKLY, MONTHLY, YEARLY
from dateutil.relativedelta import relativedelta
sys.path.append('/app')

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# WebSocket Connection Manager for real-time calendar updates
class CalendarWebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        async with self.lock:
            if user_id not in self.active_connections:
                self.active_connections[user_id] = set()
            self.active_connections[user_id].add(websocket)
        print(f"✅ Calendar WebSocket connected for user {user_id} (total: {len(self.active_connections[user_id])})")
    
    async def disconnect(self, websocket: WebSocket, user_id: str):
        async with self.lock:
            if user_id in self.active_connections and websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
        print(f"❌ Calendar WebSocket disconnected for user {user_id}")
    
    async def broadcast_to_user(self, user_id: str, message: Dict[str, Any]):
        """Broadcast message to all connections for a specific user"""
        async with self.lock:
            if user_id not in self.active_connections:
                return
            connections = list(self.active_connections[user_id])
        
        dead_connections = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"⚠️ Failed to send to connection: {e}")
                dead_connections.append(ws)
        
        # Clean up dead connections
        for ws in dead_connections:
            await self.disconnect(ws, user_id)

calendar_ws_manager = CalendarWebSocketManager()

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def get_connection(row_factory=None):
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    if row_factory is not None:
        conn.row_factory = row_factory
    # Ensure busy timeout on each connection
    try:
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    return conn

def generate_recurring_events(event, start_date, end_date):
    """Generate recurring event instances based on pattern"""
    if not event.get('recurring'):
        return []
    
    try:
        recurring_data = json.loads(event['recurring']) if isinstance(event['recurring'], str) else event['recurring']
    except (json.JSONDecodeError, TypeError):
        # If recurring data is invalid, return empty list
        return []
    pattern = recurring_data.get('pattern', 'daily')
    interval = recurring_data.get('interval', 1)
    end_recurring = recurring_data.get('end_date')
    
    # Convert start_date to datetime for rrule
    event_start_date = datetime.fromisoformat(event['start_date']).date() if isinstance(event['start_date'], str) else event['start_date']
    if isinstance(event['start_time'], str):
        # Handle both HH:MM and HH:MM:SS formats
        if len(event['start_time']) == 5:  # HH:MM format
            event_start_time = datetime.strptime(event['start_time'], '%H:%M').time()
        else:  # HH:MM:SS format
            event_start_time = datetime.strptime(event['start_time'], '%H:%M:%S').time()
    else:
        event_start_time = event['start_time'] or datetime.min.time()
    start_datetime = datetime.combine(event_start_date, event_start_time)
    
    # Create rrule based on pattern
    rule_kwargs = {
        'interval': interval,
        'dtstart': start_datetime
    }
    
    # Set end bound for recurring series
    # If the event has no explicit end, bound it by the request's end_date to avoid unbounded iteration
    if end_recurring:
        end_recurring_date = datetime.fromisoformat(end_recurring).date() if isinstance(end_recurring, str) else end_recurring
        rule_kwargs['until'] = datetime.combine(end_recurring_date, datetime.max.time())
    else:
        rule_kwargs['until'] = datetime.combine(end_date, datetime.max.time())
    
    if pattern == 'daily':
        rule = rrule(DAILY, **rule_kwargs)
    elif pattern == 'weekly':
        rule = rrule(WEEKLY, **rule_kwargs)
    elif pattern == 'monthly':
        rule = rrule(MONTHLY, **rule_kwargs)
    elif pattern == 'yearly':
        rule = rrule(YEARLY, **rule_kwargs)
    else:
        return []
    
    # Prepare exclusions and overrides from master event (optional columns)
    exdates_raw = event.get('exdates')
    overrides_raw = event.get('overrides')
    try:
        exdates = set(json.loads(exdates_raw)) if exdates_raw else set()
    except Exception:
        exdates = set()
    try:
        overrides = json.loads(overrides_raw) if overrides_raw else {}
    except Exception:
        overrides = {}

    # Generate instances within the requested date range
    instances = []
    MAX_INSTANCES = 1000
    for idx, dt in enumerate(rule):
        if idx >= MAX_INSTANCES:
            break
        if start_date <= dt.date() <= end_date:
            # Skip excluded dates
            if dt.date().isoformat() in exdates:
                continue
            instance = event.copy()
            instance['parent_event_id'] = event['id']
            instance['occurrence_id'] = f"{event['id']}:{dt.date().isoformat()}"
            instance['start_date'] = dt.date()
            instance['start_time'] = dt.time()
            if event.get('end_time'):
                # Calculate end time maintaining duration
                if isinstance(event['end_time'], str):
                    # Handle both HH:MM and HH:MM:SS formats
                    if len(event['end_time']) == 5:  # HH:MM format
                        event_end_time = datetime.strptime(event['end_time'], '%H:%M').time()
                    else:  # HH:MM:SS format
                        event_end_time = datetime.strptime(event['end_time'], '%H:%M:%S').time()
                else:
                    event_end_time = event['end_time']
                duration = datetime.combine(date.today(), event_end_time) - datetime.combine(date.today(), event_start_time)
                end_dt = dt + duration
                instance['end_date'] = end_dt.date()
                instance['end_time'] = end_dt.time()

            # Apply per-occurrence overrides if present
            ov = overrides.get(dt.date().isoformat())
            if ov:
                for key in ['title','description','location','category','all_day','start_time','end_time']:
                    if key in ov and ov[key] is not None:
                        instance[key] = ov[key]
            instances.append(instance)
    
    return instances

def init_calendar_db():
    """Initialize calendar tables"""
    conn = get_connection()
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

    # Optional columns for recurring exceptions/overrides
    try:
        cursor.execute("ALTER TABLE events ADD COLUMN exdates TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE events ADD COLUMN overrides TEXT")
    except Exception:
        pass
    
    # Event attendees table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS event_attendees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            role TEXT DEFAULT 'participant',
            notes TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
            UNIQUE(event_id, person_id)
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_attendees 
        ON event_attendees(event_id)
    """)
    
    # Add transaction_id and sort_order columns for week planner integration
    try:
        cursor.execute("ALTER TABLE events ADD COLUMN transaction_id INTEGER REFERENCES transactions(id)")
    except Exception:
        pass  # Column already exists
    try:
        cursor.execute("ALTER TABLE events ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception:
        pass  # Column already exists
    
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
    duration: Optional[int] = 30
    category: Optional[str] = "personal"
    location: Optional[str] = None
    all_day: Optional[bool] = False
    recurring: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    get_ready_time: Optional[int] = 0
    travel_time: Optional[int] = 0
    prep_items: Optional[List[Dict[str, Any]]] = []
    attendee_ids: Optional[List[int]] = []

class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    start_time: Optional[str] = None
    end_date: Optional[str] = None
    end_time: Optional[str] = None
    duration: Optional[int] = None
    category: Optional[str] = None
    location: Optional[str] = None
    all_day: Optional[bool] = None
    recurring: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    get_ready_time: Optional[int] = None
    travel_time: Optional[int] = None
    prep_items: Optional[List[Dict[str, Any]]] = None

class ReminderCreate(BaseModel):
    title: str
    description: Optional[str] = None
    reminder_type: str = "once"  # once, daily, weekly, monthly
    category: str = "personal"  # medical, household, personal, work, family
    priority: str = "medium"  # low, medium, high, critical
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    requires_acknowledgment: bool = False
    snooze_minutes: int = 5
    linked_list_id: Optional[int] = None

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
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Get events with optional filtering, including recurring events"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Set default date range if not provided
    if not start_date:
        start_date = (date.today() - timedelta(days=30)).isoformat()
    if not end_date:
        end_date = (date.today() + timedelta(days=365)).isoformat()
    
    query = """
        SELECT id, title, description, start_date, start_time, end_date, end_time,
               category, location, all_day, recurring, metadata, created_at, updated_at,
               exdates, overrides
        FROM events 
        WHERE user_id = ?
    """
    params = [user_id]
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    events = []
    start_date_obj = datetime.fromisoformat(start_date).date()
    end_date_obj = datetime.fromisoformat(end_date).date()
    
    for row in rows:
        event = {
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
            "updated_at": row[13],
            "exdates": row[14] if len(row) > 14 else None,
            "overrides": row[15] if len(row) > 15 else None
        }
        
        # Add the original event if it's in the date range
        event_start_date = datetime.fromisoformat(event['start_date']).date() if isinstance(event['start_date'], str) else event['start_date']
        if start_date_obj <= event_start_date <= end_date_obj:
            events.append(event)
        
        # Generate recurring instances if the event has a recurring pattern
        if event['recurring']:
            recurring_instances = generate_recurring_events(event, start_date_obj, end_date_obj)
            events.extend(recurring_instances)
    
    # Filter by category if specified
    if category:
        events = [e for e in events if e['category'] == category]
    
    # Sort events by date and time
    def sort_key(event):
        start_date = datetime.fromisoformat(event['start_date']).date() if isinstance(event['start_date'], str) else event['start_date']
        if event['start_time']:
            if isinstance(event['start_time'], str):
                # Handle various time formats more robustly
                try:
                    # Try HH:MM format first
                    if ':' in event['start_time'] and len(event['start_time']) <= 8:
                        time_parts = event['start_time'].split(':')
                        if len(time_parts) >= 2:
                            # Extract just hour and minute, ignore seconds if present
                            hour = int(time_parts[0])
                            minute = int(time_parts[1])
                            start_time = datetime.strptime(f"{hour:02d}:{minute:02d}", '%H:%M').time()
                        else:
                            start_time = datetime.min.time()
                    else:
                        start_time = datetime.min.time()
                except (ValueError, IndexError):
                    start_time = datetime.min.time()
            else:
                start_time = event['start_time']
        else:
            start_time = datetime.min.time()
        return (start_date, start_time)
    
    events.sort(key=sort_key)
    
    return {"events": events}

@router.put("/events/{event_id}/occurrence")
async def update_single_occurrence(
    event_id: int,
    occurrence_date: str = Query(..., description="YYYY-MM-DD"),
    update: EventUpdate = None,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update a single occurrence by adding an override on the master event."""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT overrides FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    try:
        overrides = json.loads(row[0]) if row[0] else {}
    except Exception:
        overrides = {}
    ov = overrides.get(occurrence_date, {})
    for field, value in (update.dict(exclude_unset=True) if update else {}).items():
        ov[field] = value
    overrides[occurrence_date] = ov
    cursor.execute(
        "UPDATE events SET overrides = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (json.dumps(overrides), event_id, user_id),
    )
    conn.commit()
    conn.close()
    return {"message": "Occurrence updated", "event_id": event_id, "occurrence_date": occurrence_date}

@router.delete("/events/{event_id}/occurrence")
async def delete_single_occurrence(
    event_id: int,
    occurrence_date: str = Query(..., description="YYYY-MM-DD"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Exclude a single occurrence by adding the date to exdates."""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT exdates FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    try:
        exdates = set(json.loads(row[0])) if row[0] else set()
    except Exception:
        exdates = set()
    exdates.add(occurrence_date)
    cursor.execute(
        "UPDATE events SET exdates = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (json.dumps(sorted(exdates)), event_id, user_id),
    )
    conn.commit()
    conn.close()
    return {"message": "Occurrence excluded", "event_id": event_id, "occurrence_date": occurrence_date}

@router.post("/events")
async def create_event(event: EventCreate, session: AuthenticatedSession = Depends(validate_session)):
    """Create a new event"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Calculate end_time from start_time + duration if not provided
    end_date = event.end_date
    end_time = event.end_time
    
    if not end_time and event.start_time and event.duration:
        from datetime import datetime, timedelta
        start_dt = datetime.strptime(f"{event.start_date} {event.start_time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=event.duration)
        end_date = end_dt.date().isoformat()
        end_time = end_dt.time().strftime("%H:%M")
    
    # Build metadata with new fields
    metadata = event.metadata or {}
    if event.get_ready_time:
        metadata['get_ready_time'] = event.get_ready_time
    if event.travel_time:
        metadata['travel_time'] = event.travel_time
    if event.prep_items:
        metadata['prep_items'] = event.prep_items
    
    cursor.execute("""
        INSERT INTO events (user_id, title, description, start_date, start_time, 
                          end_date, end_time, duration, category, location, all_day, recurring, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, event.title, event.description, event.start_date, event.start_time,
        end_date, end_time, event.duration, event.category, event.location, 
        event.all_day, event.recurring, json.dumps(metadata) if metadata else None
    ))
    
    event_id = cursor.lastrowid
    
    # Add attendees if provided
    if event.attendee_ids:
        for person_id in event.attendee_ids:
            try:
                cursor.execute("""
                    INSERT INTO event_attendees (event_id, person_id, role)
                    VALUES (?, ?, 'participant')
                """, (event_id, person_id))
            except Exception:
                # Skip duplicate or invalid person_id
                pass
    
    conn.commit()
    conn.close()
    
    # Broadcast update to WebSocket clients
    await calendar_ws_manager.broadcast_to_user(user_id, {
        "type": "event_created",
        "event_id": event_id,
        "action": "created"
    })
    
    # Return the created event with calculated end_time
    return {"event": {
        "id": event_id,
        "title": event.title,
        "description": event.description,
        "start_date": event.start_date,
        "start_time": event.start_time,
        "end_date": end_date,
        "end_time": end_time,
        "duration": event.duration,
        "category": event.category,
        "location": event.location,
        "all_day": event.all_day,
        "recurring": event.recurring,
        "metadata": metadata,
        "get_ready_time": event.get_ready_time,
        "travel_time": event.travel_time,
        "prep_items": event.prep_items,
        "attendee_ids": event.attendee_ids
    }}

@router.get("/events/{event_id}")
async def get_event(event_id: int, session: AuthenticatedSession = Depends(validate_session)):
    """Get a specific event"""
    user_id = session.user_id
    conn = get_connection()
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
async def update_event(event_id: int, event_update: EventUpdate, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Update an event"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if event exists and get current metadata
    cursor.execute("SELECT id, metadata FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Build update query dynamically
    update_fields = []
    params = []
    
    # Check if we need to recalculate end_time
    update_data = event_update.dict(exclude_unset=True)
    if "duration" in update_data or "start_time" in update_data:
        # Get current event data to calculate new end_time
        cursor.execute("SELECT start_date, start_time, duration FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
        current = cursor.fetchone()
        if current:
            start_date = update_data.get("start_date", current[0])
            start_time = update_data.get("start_time", current[1])
            duration = update_data.get("duration", current[2])
            
            if start_time and duration:
                from datetime import datetime, timedelta
                start_dt = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
                end_dt = start_dt + timedelta(minutes=duration)
                update_data["end_date"] = end_dt.date().isoformat()
                update_data["end_time"] = end_dt.time().strftime("%H:%M")
    
    # Handle metadata updates for new fields
    if any(k in update_data for k in ['get_ready_time', 'travel_time', 'prep_items', 'metadata']):
        try:
            current_metadata = json.loads(existing[1]) if existing[1] else {}
        except Exception:
            current_metadata = {}
        
        if 'get_ready_time' in update_data:
            current_metadata['get_ready_time'] = update_data.pop('get_ready_time')
        if 'travel_time' in update_data:
            current_metadata['travel_time'] = update_data.pop('travel_time')
        if 'prep_items' in update_data:
            current_metadata['prep_items'] = update_data.pop('prep_items')
        if 'metadata' in update_data and update_data['metadata']:
            current_metadata.update(update_data.pop('metadata'))
        
        update_data['metadata'] = current_metadata
    
    for field, value in update_data.items():
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
        
        # Fetch and return the updated event
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
        event = cursor.fetchone()
        conn.close()
        
        # Broadcast update to WebSocket clients
        await calendar_ws_manager.broadcast_to_user(user_id, {
            "type": "event_updated",
            "event_id": event_id,
            "action": "updated"
        })
        
        if event:
            return {"event": dict(event)}
        else:
            raise HTTPException(status_code=404, detail="Event not found after update")
    
    conn.close()
    
    return {"message": "No fields to update"}

@router.delete("/events/{event_id}")
async def delete_event(event_id: int, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Delete an event"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Event deleted successfully"}

@router.put("/events/{event_id}/move")
async def move_event(event_id: int, new_date: str = Query(..., description="New date (YYYY-MM-DD)"), session: AuthenticatedSession = Depends(validate_session)):
    """Move an event to a different date"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify event exists and belongs to user
    cursor.execute("SELECT id FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Update the event date
    cursor.execute("""
        UPDATE events 
        SET start_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (new_date, event_id, user_id))
    
    conn.commit()
    conn.close()
    
    return {
        "message": "Event moved successfully",
        "id": event_id,
        "start_date": new_date
    }

# Event Attendees Endpoints

class AttendeeCreate(BaseModel):
    person_id: int
    role: Optional[str] = "participant"
    notes: Optional[str] = None

@router.post("/events/{event_id}/attendees")
async def add_attendee(event_id: int, attendee: AttendeeCreate, session: AuthenticatedSession = Depends(validate_session)):
    """Add an attendee to an event"""
    user_id = session.user_id
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify event exists and belongs to user
    cursor.execute("SELECT id FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Verify person exists (check in memories database)
    try:
        memories_conn = sqlite3.connect(DB_PATH)
        memories_cursor = memories_conn.cursor()
        memories_cursor.execute("SELECT id, name, email, phone FROM people WHERE id = ?", (attendee.person_id,))
        person = memories_cursor.fetchone()
        memories_conn.close()
        
        if not person:
            conn.close()
            raise HTTPException(status_code=404, detail="Person not found")
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Error checking person: {str(e)}")
    
    # Add attendee
    try:
        cursor.execute("""
            INSERT INTO event_attendees (event_id, person_id, role, notes)
            VALUES (?, ?, ?, ?)
        """, (event_id, attendee.person_id, attendee.role, attendee.notes))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Attendee already added to this event")
    
    conn.close()
    
    return {
        "message": "Attendee added successfully",
        "attendee": {
            "person_id": attendee.person_id,
            "name": person[1],
            "email": person[2],
            "phone": person[3],
            "role": attendee.role,
            "notes": attendee.notes
        }
    }

@router.get("/events/{event_id}/attendees")
async def get_attendees(event_id: int, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Get all attendees for an event"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify event exists and belongs to user
    cursor.execute("SELECT id FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Get attendees
    cursor.execute("""
        SELECT person_id, role, notes, added_at
        FROM event_attendees
        WHERE event_id = ?
    """, (event_id,))
    
    attendee_rows = cursor.fetchall()
    conn.close()
    
    # Get person details from memories database
    attendees = []
    memories_conn = sqlite3.connect(DB_PATH)
    memories_cursor = memories_conn.cursor()
    
    for row in attendee_rows:
        person_id, role, notes, added_at = row
        memories_cursor.execute("""
            SELECT id, name, email, phone, relationship
            FROM people WHERE id = ?
        """, (person_id,))
        person = memories_cursor.fetchone()
        
        if person:
            attendees.append({
                "person_id": person[0],
                "name": person[1],
                "email": person[2],
                "phone": person[3],
                "relationship": person[4],
                "role": role,
                "notes": notes,
                "added_at": added_at
            })
    
    memories_conn.close()
    
    return {
        "event_id": event_id,
        "attendees": attendees,
        "count": len(attendees)
    }

@router.delete("/events/{event_id}/attendees/{person_id}")
async def remove_attendee(event_id: int, person_id: int, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Remove an attendee from an event"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify event exists and belongs to user
    cursor.execute("SELECT id FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Remove attendee
    cursor.execute("""
        DELETE FROM event_attendees
        WHERE event_id = ? AND person_id = ?
    """, (event_id, person_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Attendee not found for this event")
    
    conn.commit()
    conn.close()
    
    return {"message": "Attendee removed successfully"}

# Prep Items Endpoints

class PrepItemCreate(BaseModel):
    text: str
    list_type: Optional[str] = "personal"
    deadline_offset: Optional[int] = 1
    deadline_unit: Optional[str] = "days"
    auto_add_to_list: Optional[bool] = False

@router.post("/events/{event_id}/prep-items")
async def add_prep_item(event_id: int, prep_item: PrepItemCreate, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Add a prep item to an event"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get event and its metadata
    cursor.execute("SELECT metadata FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    try:
        metadata = json.loads(row[0]) if row[0] else {}
    except Exception:
        metadata = {}
    
    # Get or create prep_items list
    prep_items = metadata.get('prep_items', [])
    
    # Create new prep item with UUID
    import uuid
    new_item = {
        "id": str(uuid.uuid4()),
        "text": prep_item.text,
        "list_type": prep_item.list_type,
        "deadline_offset": prep_item.deadline_offset,
        "deadline_unit": prep_item.deadline_unit,
        "auto_add_to_list": prep_item.auto_add_to_list,
        "completed": False,
        "project_compatible": True
    }
    
    prep_items.append(new_item)
    metadata['prep_items'] = prep_items
    
    # Update event metadata
    cursor.execute("""
        UPDATE events SET metadata = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (json.dumps(metadata), event_id, user_id))
    
    conn.commit()
    conn.close()
    
    return {
        "message": "Prep item added successfully",
        "prep_item": new_item
    }

@router.get("/events/{event_id}/prep-items")
async def get_prep_items(event_id: int, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Get all prep items for an event"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get event metadata
    cursor.execute("SELECT metadata FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    try:
        metadata = json.loads(row[0]) if row[0] else {}
    except Exception:
        metadata = {}
    
    conn.close()
    
    prep_items = metadata.get('prep_items', [])
    
    return {
        "event_id": event_id,
        "prep_items": prep_items,
        "count": len(prep_items)
    }

@router.put("/events/{event_id}/prep-items/{item_id}")
async def update_prep_item(event_id: int, item_id: str, prep_item: PrepItemCreate, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Update a prep item"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get event metadata
    cursor.execute("SELECT metadata FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    try:
        metadata = json.loads(row[0]) if row[0] else {}
    except Exception:
        metadata = {}
    
    prep_items = metadata.get('prep_items', [])
    
    # Find and update item
    item_found = False
    for item in prep_items:
        if item['id'] == item_id:
            item['text'] = prep_item.text
            item['list_type'] = prep_item.list_type
            item['deadline_offset'] = prep_item.deadline_offset
            item['deadline_unit'] = prep_item.deadline_unit
            item['auto_add_to_list'] = prep_item.auto_add_to_list
            item_found = True
            break
    
    if not item_found:
        conn.close()
        raise HTTPException(status_code=404, detail="Prep item not found")
    
    metadata['prep_items'] = prep_items
    
    # Update event metadata
    cursor.execute("""
        UPDATE events SET metadata = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (json.dumps(metadata), event_id, user_id))
    
    conn.commit()
    conn.close()
    
    return {"message": "Prep item updated successfully"}

@router.delete("/events/{event_id}/prep-items/{item_id}")
async def delete_prep_item(event_id: int, item_id: str, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Delete a prep item"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get event metadata
    cursor.execute("SELECT metadata FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    try:
        metadata = json.loads(row[0]) if row[0] else {}
    except Exception:
        metadata = {}
    
    prep_items = metadata.get('prep_items', [])
    
    # Find and remove item
    initial_length = len(prep_items)
    prep_items = [item for item in prep_items if item['id'] != item_id]
    
    if len(prep_items) == initial_length:
        conn.close()
        raise HTTPException(status_code=404, detail="Prep item not found")
    
    metadata['prep_items'] = prep_items
    
    # Update event metadata
    cursor.execute("""
        UPDATE events SET metadata = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (json.dumps(metadata), event_id, user_id))
    
    conn.commit()
    conn.close()
    
    return {"message": "Prep item deleted successfully"}

@router.post("/events/{event_id}/prep-items/{item_id}/complete")
async def complete_prep_item(event_id: int, item_id: str, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Mark a prep item as complete or incomplete"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get event metadata
    cursor.execute("SELECT metadata FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    try:
        metadata = json.loads(row[0]) if row[0] else {}
    except Exception:
        metadata = {}
    
    prep_items = metadata.get('prep_items', [])
    
    # Find and toggle completion
    item_found = False
    for item in prep_items:
        if item['id'] == item_id:
            item['completed'] = not item.get('completed', False)
            item_found = True
            break
    
    if not item_found:
        conn.close()
        raise HTTPException(status_code=404, detail="Prep item not found")
    
    metadata['prep_items'] = prep_items
    
    # Update event metadata
    cursor.execute("""
        UPDATE events SET metadata = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (json.dumps(metadata), event_id, user_id))
    
    conn.commit()
    conn.close()
    
    return {"message": "Prep item status updated successfully"}

# Event Reminders Endpoints

class EventReminderCreate(BaseModel):
    offset_minutes: int  # Minutes before event
    message: Optional[str] = None

@router.post("/events/{event_id}/reminders")
async def create_event_reminder(event_id: int, reminder: EventReminderCreate, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Create a reminder for an event"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get event
    cursor.execute("""
        SELECT start_date, start_time, title FROM events 
        WHERE id = ? AND user_id = ?
    """, (event_id, user_id))
    event = cursor.fetchone()
    
    if not event:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    start_date, start_time, title = event
    
    # Calculate reminder time
    try:
        from datetime import datetime, timedelta
        event_dt = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
        reminder_dt = event_dt - timedelta(minutes=reminder.offset_minutes)
        
        # Create reminder
        reminder_title = reminder.message or f"Reminder: {title}"
        cursor.execute("""
            INSERT INTO reminders (
                user_id, title, reminder_type, category, priority,
                due_date, due_time, requires_acknowledgment, linked_list_id
            ) VALUES (?, ?, 'once', 'personal', 'medium', ?, ?, FALSE, ?)
        """, (
            user_id, reminder_title, reminder_dt.date().isoformat(),
            reminder_dt.time().strftime("%H:%M"), event_id
        ))
        
        reminder_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "message": "Event reminder created successfully",
            "reminder_id": reminder_id,
            "reminder_time": reminder_dt.isoformat(),
            "offset_minutes": reminder.offset_minutes
        }
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Error creating reminder: {str(e)}")

@router.get("/events/{event_id}/reminders")
async def get_event_reminders(event_id: int, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Get all reminders for an event"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify event exists
    cursor.execute("SELECT id FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Get reminders linked to this event (using linked_list_id as event_id)
    cursor.execute("""
        SELECT id, title, due_date, due_time, priority, created_at
        FROM reminders
        WHERE user_id = ? AND linked_list_id = ? AND is_active = TRUE
    """, (user_id, event_id))
    
    reminders = []
    for row in cursor.fetchall():
        reminders.append({
            "id": row[0],
            "title": row[1],
            "due_date": row[2],
            "due_time": row[3],
            "priority": row[4],
            "created_at": row[5]
        })
    
    conn.close()
    
    return {
        "event_id": event_id,
        "reminders": reminders,
        "count": len(reminders)
    }

@router.delete("/reminders/{reminder_id}")
async def delete_reminder(reminder_id: int, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Delete a reminder"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE reminders SET is_active = FALSE
        WHERE id = ? AND user_id = ?
    """, (reminder_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Reminder not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Reminder deleted successfully"}

# Smart Scheduling Integration

@router.post("/suggest-time")
async def suggest_optimal_time(
    title: str = Query(..., description="Event title"),
    duration_minutes: int = Query(60, description="Duration in minutes"),
    task_type: str = Query("focus", description="Task type: focus, admin, creative, physical"),
    priority: int = Query(3, description="Priority level 1-5"),
    preferred_times: Optional[str] = Query(None, description="Preferred times: morning,afternoon,evening"),
    deadline: Optional[str] = Query(None, description="Deadline date (YYYY-MM-DD)"),
    energy_requirement: str = Query("medium", description="Energy requirement: high, medium, low")
):
    """Suggest optimal time for an event using smart scheduling"""
    try:
        # Simple energy-based scheduling logic
        now = datetime.now()
        today = now.date()
        
        # Get energy patterns (simplified)
        morning_energy = 0.8
        afternoon_energy = 0.6
        evening_energy = 0.4
        
        # Suggest times based on task type and energy
        suggestions = []
        
        if task_type == "focus" and priority >= 4:
            # High priority focus work - suggest morning
            suggestions.append({
                "start_time": f"{today}T09:00:00",
                "end_time": f"{today}T{9 + duration_minutes//60:02d}:{duration_minutes%60:02d}:00",
                "energy_level": "high",
                "score": 0.9,
                "reasoning": "Morning energy peak for focus work"
            })
        
        if task_type == "admin" or priority <= 2:
            # Admin work - suggest afternoon
            suggestions.append({
                "start_time": f"{today}T14:00:00",
                "end_time": f"{today}T{14 + duration_minutes//60:02d}:{duration_minutes%60:02d}:00",
                "energy_level": "medium",
                "score": 0.7,
                "reasoning": "Afternoon energy suitable for admin tasks"
            })
        
        # Add evening option for low priority tasks
        if priority <= 3:
            suggestions.append({
                "start_time": f"{today}T19:00:00",
                "end_time": f"{today}T{19 + duration_minutes//60:02d}:{duration_minutes%60:02d}:00",
                "energy_level": "low",
                "score": 0.5,
                "reasoning": "Evening time for low priority tasks"
            })
        
        return {
            "title": title,
            "suggestions": suggestions,
            "reasoning": f"Based on your energy patterns and {task_type} task requirements"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/energy-patterns")
async def get_energy_patterns(session: AuthenticatedSession = Depends(validate_session)):
    """Get user's energy patterns for smart scheduling"""
    user_id = session.user_id
    try:
        from routers.smart_scheduling import SmartScheduler
        scheduler = SmartScheduler()
        
        patterns = await scheduler.get_user_patterns(user_id)
        return patterns
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/schedule-event")
async def schedule_event_with_smart_scheduling(
    title: str = Query(..., description="Event title"),
    duration_minutes: int = Query(60, description="Duration in minutes"),
    task_type: str = Query("focus", description="Task type"),
    priority: int = Query(3, description="Priority level"),
    suggested_time: str = Query(..., description="Suggested time slot"),
    session: AuthenticatedSession = Depends(validate_session)
):
    user_id = session.user_id
    """Schedule an event using smart scheduling suggestions"""
    try:
        # Parse suggested time (format: "2025-09-14T10:00:00")
        start_datetime = datetime.fromisoformat(suggested_time.replace('Z', '+00:00'))
        end_datetime = start_datetime + timedelta(minutes=duration_minutes)
        
        # Create event
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO events (user_id, title, start_date, start_time, end_date, end_time, 
                              category, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, title, start_datetime.date(), start_datetime.time(),
            end_datetime.date(), end_datetime.time(), task_type,
            json.dumps({"smart_scheduled": True, "priority": priority})
        ))
        
        event_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "event_id": event_id,
            "title": title,
            "start_time": suggested_time,
            "duration_minutes": duration_minutes,
            "message": "Event scheduled using smart scheduling"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/events/today")
async def get_todays_events(session: AuthenticatedSession = Depends(validate_session)):
    """Get today's events"""
    user_id = session.user_id
    today = date.today().isoformat()
    return await get_events(start_date=today, end_date=today, user_id=user_id)

@router.get("/week")
async def get_week_view(
    start_date: str = Query(..., description="Monday of week (YYYY-MM-DD)"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get aggregated week view with calendar events, list items, and transactions"""
    user_id = session.user_id
    import sys
    print(f"[WEEK VIEW] user_id from session: {user_id}", file=sys.stderr)
    
    # Parse start date and calculate week range
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        # Ensure it's Monday
        days_since_monday = start.weekday()
        monday = start - timedelta(days=days_since_monday)
        sunday = monday + timedelta(days=6)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Day names for grouping
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    week_data = {day: [] for day in days}
    
    # Get calendar events for the week
    cursor.execute("""
        SELECT id, title, description, start_date, start_time, end_date, end_time,
               category, location, all_day, recurring, metadata, created_at, updated_at,
               transaction_id, sort_order
        FROM events 
        WHERE user_id = ? AND start_date >= ? AND start_date <= ?
        ORDER BY start_time ASC, sort_order ASC
    """, (user_id, monday.isoformat(), sunday.isoformat()))
    
    events = cursor.fetchall()
    
    # Debug logging
    import sys
    print(f"[WEEK DEBUG] user_id={user_id}, monday={monday}, sunday={sunday}", file=sys.stderr)
    print(f"[WEEK DEBUG] Found {len(events)} events", file=sys.stderr)
    for evt in events[:3]:
        print(f"[WEEK DEBUG] Event: {evt[:3]}", file=sys.stderr)
    
    # Get transactions for the week
    cursor.execute("""
        SELECT id, description, amount, type, transaction_date, status, payment_method,
               person_id, calendar_event_id, list_item_id, metadata
        FROM transactions
        WHERE user_id = ? AND transaction_date >= ? AND transaction_date <= ?
        ORDER BY transaction_date ASC, created_at ASC
    """, (user_id, monday.isoformat(), sunday.isoformat()))
    
    transactions = cursor.fetchall()
    
    # Get list items with due_date for the week
    cursor.execute("""
        SELECT li.id, li.task_text, li.priority, li.completed, li.due_date, li.transaction_id,
               l.name as list_name, l.list_type
        FROM list_items li
        JOIN lists l ON li.list_id = l.id
        WHERE l.user_id = ? AND l.user_id = ? AND li.due_date IS NOT NULL AND li.due_date >= ? AND li.due_date <= ?
        ORDER BY li.due_date ASC, li.sort_order ASC
    """, (user_id, user_id, monday.isoformat(), sunday.isoformat()))
    
    list_items = cursor.fetchall()
    
    # Create transaction lookup by ID
    transaction_map = {}
    for txn in transactions:
        txn_id, desc, amount, txn_type, txn_date, status, method, person_id, cal_id, list_id, metadata = txn
        try:
            metadata_json = json.loads(metadata) if metadata else {}
        except:
            metadata_json = {}
        
        # Get person name if exists
        person_name = None
        if person_id:
            cursor.execute("SELECT name FROM people WHERE id = ?", (person_id,))
            person_row = cursor.fetchone()
            if person_row:
                person_name = person_row[0]
        
        transaction_map[txn_id] = {
            "id": txn_id,
            "description": desc,
            "amount": amount,
            "type": txn_type,
            "transaction_date": txn_date,
            "status": status,
            "payment_method": method,
            "person": {"name": person_name} if person_name else None,
            "calendar_event_id": cal_id,
            "list_item_id": list_id
        }
    
    # Process events
    print(f"[WEEK] Processing {len(events)} events for week")
    print(f"[WEEK] User ID: {user_id}, Week: {monday} to {sunday}")
    for event in events:
        try:
            event_id, title, desc, start_date_str, start_time, end_date, end_time, category, location, all_day, recurring, metadata, created_at, updated_at, transaction_id, sort_order = event
            
            print(f"[WEEK] Processing: {title} on {start_date_str} at {start_time}")
            
            # Get transaction if linked
            linked_transaction = None
            if transaction_id and transaction_id in transaction_map:
                linked_transaction = {
                    "amount": transaction_map[transaction_id]["amount"],
                    "type": transaction_map[transaction_id]["type"],
                    "status": transaction_map[transaction_id]["status"]
                }
            
            # Get day name
            event_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            day_index = (event_date - monday).days
            print(f"[WEEK] Day index: {day_index}")
            
            if day_index < 0 or day_index >= 7:
                print(f"[WEEK] Skipping event outside week range")
                continue  # Skip if outside week range
            
            day_name = days[day_index]
            print(f"[WEEK] Day name: {day_name}")
            
            # Determine icon based on title/category
            icon = get_event_icon(title, category)
            
            # Format time - handle both string and time object types
            time_str = None
            if start_time:
                if isinstance(start_time, str):
                    # Already a string, use as-is or extract HH:MM
                    time_str = start_time[:5] if len(start_time) >= 5 else start_time
                else:
                    # It's a time object, format it
                    time_str = start_time.strftime("%H:%M")
            
            week_data[day_name].append({
                "type": "calendar_event",
                "id": event_id,
                "title": title,
                "time": time_str,
                "description": desc,
                "category": category,
                "icon": icon,
                "transaction": linked_transaction,
                "sort_order": sort_order
            })
            print(f"[WEEK] Added to {day_name}: {title}")
        except Exception as e:
            print(f"[WEEK] Error processing event: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # Process standalone transactions (not linked to events or lists)
    for txn in transactions:
        transaction_id = txn[0]
        txn_data = transaction_map[transaction_id]
        
        if txn_data["calendar_event_id"] or txn_data["list_item_id"]:
            continue  # Already linked to event or list
        
        txn_date = datetime.strptime(txn_data["transaction_date"], "%Y-%m-%d").date()
        day_index = (txn_date - monday).days
        day_name = days[day_index]
        
        week_data[day_name].append({
            "type": "transaction",
            "id": transaction_id,
            "description": txn_data["description"],
            "amount": txn_data["amount"],
            "type": txn_data["type"],
            "status": txn_data["status"],
            "payment_method": txn_data["payment_method"],
            "person": txn_data["person"]
        })
    
    # Process list items
    for item in list_items:
        try:
            item_id, task_text, priority, completed, due_date_str, transaction_id, list_name, list_type = item
            
            # Get transaction if linked
            linked_transaction = None
            if transaction_id and transaction_id in transaction_map:
                linked_transaction = {
                    "amount": transaction_map[transaction_id]["amount"],
                    "type": transaction_map[transaction_id]["type"],
                    "status": transaction_map[transaction_id]["status"]
                }
            
            if not due_date_str:
                continue
            
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            
            day_index = (due_date - monday).days
            if day_index < 0 or day_index >= 7:
                continue
            
            day_name = days[day_index]
            
            icon = get_list_item_icon(task_text)
            
            week_data[day_name].append({
                "type": "list_item",
                "id": item_id,
                "text": task_text,
                "priority": priority,
                "completed": completed,
                "list_name": list_name,
                "list_type": list_type,
                "icon": icon,
                "transaction": linked_transaction
            })
        except Exception as e:
            print(f"Error processing list item: {e}")
            continue
    
    # Calculate financial totals
    totals = {
        "income_expected": 0,
        "income_received": 0,
        "expense_due": 0,
        "expense_paid": 0
    }
    
    cursor.execute("""
        SELECT type, status, SUM(amount) as total
        FROM transactions
        WHERE user_id = ? AND transaction_date >= ? AND transaction_date <= ?
        GROUP BY type, status
    """, (user_id, monday.isoformat(), sunday.isoformat()))
    
    for row in cursor.fetchall():
        txn_type, txn_status, total = row
        if txn_type == "income":
            if txn_status == "pending":
                totals["income_expected"] = total
            elif txn_status == "completed":
                totals["income_received"] = total
        elif txn_type == "expense":
            if txn_status == "pending":
                totals["expense_due"] = total
            elif txn_status == "completed":
                totals["expense_paid"] = total
    
    conn.close()
    
    return {
        "days": week_data,
        "totals": totals,
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat()
    }

def get_event_icon(title: str, category: str) -> str:
    """Get icon for calendar event based on title and category"""
    title_lower = title.lower()
    if any(word in title_lower for word in ['clean', 'ash', 'amy', 'lynette', 'katrina', 'aimee', 'louise', 'anna']):
        return "🧹"
    elif category == "birthday":
        return "🎂"
    elif any(word in title_lower for word in ['call', 'phone']):
        return "📞"
    elif any(word in title_lower for word in ['meeting', 'appointment']):
        return "📅"
    elif any(word in title_lower for word in ['fuel', 'gas']):
        return "⛽"
    elif any(word in title_lower for word in ['basketball', 'sport', 'game']):
        return "🏀"
    elif "party" in title_lower or "birthday" in title_lower:
        return "🎉"
    else:
        return "📅"

def get_list_item_icon(text: str) -> str:
    """Get icon for list item based on text"""
    text_lower = text.lower()
    if any(word in text_lower for word in ['call', 'phone', 'contact']):
        return "📞"
    elif any(word in text_lower for word in ['buy', 'get', 'shopping']):
        return "🛒"
    else:
        return "✓"

# Reminder endpoints integrated with calendar

@router.post("/reminders/", response_model=Dict[str, Any])
async def create_reminder(reminder: ReminderCreate, session: AuthenticatedSession = Depends(validate_session)):
    user_id = session.user_id
    """Create a new reminder"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Insert reminder
        cursor.execute("""
            INSERT INTO reminders (
                user_id, title, description, reminder_type, category, priority,
                due_date, due_time, requires_acknowledgment, snooze_minutes, linked_list_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, reminder.title, reminder.description, reminder.reminder_type,
            reminder.category, reminder.priority, reminder.due_date, reminder.due_time,
            reminder.requires_acknowledgment, reminder.snooze_minutes, reminder.linked_list_id
        ))
        
        reminder_id = cursor.lastrowid
        
        # Create initial notification if due date/time is set
        if reminder.due_date and reminder.due_time:
            try:
                from datetime import datetime
                # Handle both HH:MM and HH:MM:SS formats for due_time
                if len(reminder.due_time) == 5:  # HH:MM format
                    due_time_obj = datetime.strptime(reminder.due_time, '%H:%M').time()
                else:  # HH:MM:SS format
                    due_time_obj = datetime.strptime(reminder.due_time, '%H:%M:%S').time()
                
                notification_time = datetime.combine(
                    datetime.strptime(reminder.due_date, '%Y-%m-%d').date(),
                    due_time_obj
                )
                cursor.execute("""
                    INSERT INTO notifications (user_id, reminder_id, notification_time, message, priority)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    user_id, reminder_id, notification_time.isoformat(),
                    f"Reminder: {reminder.title}", reminder.priority
                ))
            except Exception as e:
                print(f"Warning: Could not create notification: {e}")
                # Continue without notification
        
        conn.commit()
        conn.close()
        
        return {
            "reminder_id": reminder_id,
            "message": "Reminder created successfully",
            "reminder": {
                "id": reminder_id,
                "title": reminder.title,
                "category": reminder.category,
                "priority": reminder.priority,
                "due_date": reminder.due_date,
                "due_time": reminder.due_time
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/reminders/", response_model=Dict[str, Any])
async def get_reminders(
    session: AuthenticatedSession = Depends(validate_session),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50)
):
    """Get reminders with optional filtering"""
    user_id = session.user_id
    try:
        conn = get_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        
        query = "SELECT * FROM reminders WHERE user_id = ? AND is_active = TRUE"
        params = [user_id]
        
        if category:
            query += " AND category = ?"
            params.append(category)
        
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        
        query += " ORDER BY due_date ASC, due_time ASC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        reminders = []
        
        for row in cursor.fetchall():
            reminder = {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "reminder_type": row["reminder_type"],
                "category": row["category"],
                "priority": row["priority"],
                "due_date": row["due_date"],
                "due_time": row["due_time"],
                "requires_acknowledgment": bool(row["requires_acknowledgment"]),
                "snooze_minutes": row["snooze_minutes"],
                "linked_list_id": row["linked_list_id"],
                "created_at": row["created_at"]
            }
            reminders.append(reminder)
        
        conn.close()
        return {"reminders": reminders, "count": len(reminders)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/reminders/today", response_model=Dict[str, Any])
async def get_todays_reminders(session: AuthenticatedSession = Depends(validate_session)):
    """Get today's reminders"""
    user_id = session.user_id
    try:
        today = date.today().isoformat()
        conn = get_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM reminders 
            WHERE user_id = ? AND is_active = TRUE 
            AND (due_date = ? OR due_date IS NULL)
            ORDER BY due_time ASC
        """, (user_id, today))
        
        reminders = []
        for row in cursor.fetchall():
            reminder = {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "category": row["category"],
                "priority": row["priority"],
                "due_time": row["due_time"],
                "requires_acknowledgment": bool(row["requires_acknowledgment"]),
                "linked_list_id": row["linked_list_id"]
            }
            reminders.append(reminder)
        
        conn.close()
        return {"reminders": reminders, "date": today, "count": len(reminders)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# WebSocket endpoint for real-time calendar updates
@router.websocket("/ws/{user_id}")
async def calendar_websocket(websocket: WebSocket, user_id: str):
    """WebSocket endpoint for real-time calendar synchronization across devices"""
    await calendar_ws_manager.connect(websocket, user_id)
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "message": "Calendar WebSocket connected",
            "user_id": user_id
        })
        
        # Keep connection alive and handle pings
        while True:
            data = await websocket.receive_text()
            # Echo back as heartbeat
            await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
    except WebSocketDisconnect:
        await calendar_ws_manager.disconnect(websocket, user_id)
    except Exception as e:
        print(f"WebSocket error: {e}")
        await calendar_ws_manager.disconnect(websocket, user_id)
