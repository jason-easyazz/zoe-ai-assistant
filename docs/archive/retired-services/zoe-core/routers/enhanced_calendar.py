"""
Enhanced Calendar Router with Family/Group Integration
Combines personal and shared family events with Skylight Calendar-inspired features
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
import sqlite3
import json
import os
import sys
from dateutil.rrule import rrule, DAILY, WEEKLY, MONTHLY, YEARLY
from dateutil.relativedelta import relativedelta
sys.path.append('/app')

from .family import family_manager
from .auth import get_current_user

router = APIRouter(prefix="/api/calendar", tags=["enhanced_calendar"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def get_connection(row_factory=None):
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    if row_factory is not None:
        conn.row_factory = row_factory
    try:
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    return conn

class UnifiedEventResponse(BaseModel):
    event_id: str
    title: str
    description: Optional[str]
    start_time: datetime
    end_time: Optional[datetime]
    event_type: str  # "personal", "family", "child_activity", "household"
    visibility: str  # "private", "family", "public"
    assigned_to: Optional[str]
    created_by: str
    family_id: Optional[str]
    family_name: Optional[str]
    category: str
    location: Optional[str]
    all_day: bool
    recurring: Optional[str]
    metadata: Optional[Dict[str, Any]]
    created_at: str

class EventCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    event_type: str = "personal"  # "personal", "family", "child_activity", "household"
    visibility: str = "private"  # "private", "family", "public"
    assigned_to: Optional[str] = None
    family_id: Optional[str] = None
    category: str = "personal"
    location: Optional[str] = None
    all_day: bool = False
    recurring: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

@router.get("/unified-events", response_model=List[UnifiedEventResponse])
async def get_unified_events(
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    event_type: Optional[str] = Query(None, description="Event type filter"),
    current_user = Depends(get_current_user)
):
    """Get unified view of personal and family events"""
    try:
        user_id = current_user["user_id"]
        
        # Set default date range if not provided
        if not start_date:
            start_date = (date.today() - timedelta(days=30)).isoformat()
        if not end_date:
            end_date = (date.today() + timedelta(days=365)).isoformat()
        
        start_date_obj = datetime.fromisoformat(start_date).date()
        end_date_obj = datetime.fromisoformat(end_date).date()
        
        all_events = []
        
        # Get personal events
        personal_events = await get_personal_events(user_id, start_date_obj, end_date_obj)
        for event in personal_events:
            all_events.append(UnifiedEventResponse(
                event_id=f"personal_{event['id']}",
                title=event['title'],
                description=event['description'],
                start_time=datetime.combine(
                    datetime.fromisoformat(event['start_date']).date(),
                    datetime.strptime(event['start_time'], '%H:%M').time() if event['start_time'] else datetime.min.time()
                ),
                end_time=datetime.combine(
                    datetime.fromisoformat(event['end_date']).date(),
                    datetime.strptime(event['end_time'], '%H:%M').time() if event['end_time'] else datetime.min.time()
                ) if event['end_date'] and event['end_time'] else None,
                event_type="personal",
                visibility="private",
                assigned_to=None,
                created_by=user_id,
                family_id=None,
                family_name=None,
                category=event['category'],
                location=event['location'],
                all_day=event['all_day'],
                recurring=event['recurring'],
                metadata=event['metadata'],
                created_at=event['created_at']
            ))
        
        # Get family events
        user_families = family_manager.get_user_families(user_id)
        for family in user_families:
            family_events = family_manager.get_family_events(
                family['family_id'], 
                user_id, 
                datetime.combine(start_date_obj, datetime.min.time()),
                datetime.combine(end_date_obj, datetime.max.time())
            )
            
            for event in family_events:
                all_events.append(UnifiedEventResponse(
                    event_id=f"family_{event['event_id']}",
                    title=event['title'],
                    description=event['description'],
                    start_time=datetime.fromisoformat(event['start_time']) if isinstance(event['start_time'], str) else event['start_time'],
                    end_time=datetime.fromisoformat(event['end_time']) if event['end_time'] and isinstance(event['end_time'], str) else event['end_time'],
                    event_type=event['event_type'],
                    visibility=event['visibility'],
                    assigned_to=event['assigned_to'],
                    created_by=event['created_by'],
                    family_id=family['family_id'],
                    family_name=family['name'],
                    category=event['event_type'],
                    location=None,
                    all_day=False,
                    recurring=None,
                    metadata=None,
                    created_at=event['created_at']
                ))
        
        # Filter by event type if specified
        if event_type:
            all_events = [e for e in all_events if e.event_type == event_type]
        
        # Sort by start time
        all_events.sort(key=lambda x: x.start_time)
        
        return all_events
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get unified events: {str(e)}")

async def get_personal_events(user_id: str, start_date: date, end_date: date) -> List[Dict[str, Any]]:
    """Get personal events for a user"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, title, description, start_date, start_time, end_date, end_time,
                   category, location, all_day, recurring, metadata, created_at
            FROM events 
            WHERE user_id = ? AND start_date BETWEEN ? AND ?
            ORDER BY start_date, start_time
        """, (user_id, start_date.isoformat(), end_date.isoformat()))
        
        events = []
        for row in cursor.fetchall():
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
                "created_at": row[12]
            })
        
        conn.close()
        return events
        
    except Exception as e:
        print(f"Error getting personal events: {e}")
        return []

@router.post("/create-event", response_model=Dict[str, Any])
async def create_unified_event(
    event_data: EventCreateRequest,
    current_user = Depends(get_current_user)
):
    """Create a personal or family event"""
    try:
        user_id = current_user["user_id"]
        
        if event_data.family_id:
            # Create family event
            result = family_manager.create_shared_event(
                event_data.family_id,
                event_data.dict(),
                user_id
            )
            
            if not result["success"]:
                raise HTTPException(status_code=400, detail=result["error"])
            
            return {
                "success": True,
                "event_id": result["event_id"],
                "event_type": "family",
                "message": "Family event created successfully"
            }
        else:
            # Create personal event
            conn = get_connection()
            cursor = conn.cursor()
            
            # Calculate end_time if not provided
            end_time = event_data.end_time
            if not end_time and event_data.start_time:
                end_time = event_data.start_time + timedelta(hours=1)  # Default 1 hour duration
            
            cursor.execute("""
                INSERT INTO events (user_id, title, description, start_date, start_time, 
                                  end_date, end_time, category, location, all_day, recurring, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, event_data.title, event_data.description,
                event_data.start_time.date(), event_data.start_time.time(),
                end_time.date() if end_time else None, end_time.time() if end_time else None,
                event_data.category, event_data.location, event_data.all_day,
                json.dumps(event_data.recurring) if event_data.recurring else None,
                json.dumps(event_data.metadata) if event_data.metadata else None
            ))
            
            event_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "event_id": event_id,
                "event_type": "personal",
                "message": "Personal event created successfully"
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create event: {str(e)}")

@router.get("/family-events/{family_id}", response_model=List[Dict[str, Any]])
async def get_family_events(
    family_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user = Depends(get_current_user)
):
    """Get events for a specific family"""
    try:
        user_id = current_user["user_id"]
        
        start_dt = None
        end_dt = None
        
        if start_date:
            start_dt = datetime.fromisoformat(start_date)
        if end_date:
            end_dt = datetime.fromisoformat(end_date)
        
        events = family_manager.get_family_events(family_id, user_id, start_dt, end_dt)
        return events
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get family events: {str(e)}")

@router.get("/event-types", response_model=Dict[str, List[str]])
async def get_event_types():
    """Get available event types and their descriptions"""
    return {
        "event_types": [
            {
                "id": "personal",
                "name": "Personal",
                "description": "Private personal events",
                "icon": "person",
                "color": "#3B82F6"
            },
            {
                "id": "family",
                "name": "Family",
                "description": "Family-wide events visible to all members",
                "icon": "family",
                "color": "#10B981"
            },
            {
                "id": "child_activity",
                "name": "Child Activity",
                "description": "Activities for children (sports, school, etc.)",
                "icon": "child_care",
                "color": "#F59E0B"
            },
            {
                "id": "household",
                "name": "Household",
                "description": "Household management events (cleaning, maintenance)",
                "icon": "home",
                "color": "#8B5CF6"
            }
        ],
        "visibility_levels": [
            {
                "id": "private",
                "name": "Private",
                "description": "Only visible to you"
            },
            {
                "id": "family",
                "name": "Family",
                "description": "Visible to all family members"
            },
            {
                "id": "public",
                "name": "Public",
                "description": "Visible to everyone"
            }
        ]
    }

@router.get("/dashboard", response_model=Dict[str, Any])
async def get_calendar_dashboard(current_user = Depends(get_current_user)):
    """Get calendar dashboard with today's events and upcoming family events"""
    try:
        user_id = current_user["user_id"]
        today = date.today()
        
        # Get today's personal events
        today_events = await get_personal_events(user_id, today, today)
        
        # Get upcoming family events (next 7 days)
        upcoming_start = today
        upcoming_end = today + timedelta(days=7)
        
        user_families = family_manager.get_user_families(user_id)
        upcoming_family_events = []
        
        for family in user_families:
            family_events = family_manager.get_family_events(
                family['family_id'],
                user_id,
                datetime.combine(upcoming_start, datetime.min.time()),
                datetime.combine(upcoming_end, datetime.max.time())
            )
            upcoming_family_events.extend(family_events)
        
        # Get family summary
        family_summary = []
        for family in user_families:
            family_summary.append({
                "family_id": family['family_id'],
                "name": family['name'],
                "member_count": family['member_count'],
                "user_role": family['user_role']
            })
        
        return {
            "today_events": today_events,
            "upcoming_family_events": upcoming_family_events,
            "family_summary": family_summary,
            "date": today.isoformat(),
            "total_events_today": len(today_events),
            "total_family_events_upcoming": len(upcoming_family_events)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard: {str(e)}")

@router.get("/suggestions", response_model=Dict[str, Any])
async def get_event_suggestions(
    event_type: str = Query("family", description="Type of event to suggest"),
    current_user = Depends(get_current_user)
):
    """Get event suggestions based on family patterns and Skylight Calendar inspiration"""
    
    suggestions = {
        "family_events": [
            {
                "title": "Family Dinner",
                "description": "Weekly family dinner time",
                "suggested_time": "18:00",
                "recurring": "weekly",
                "category": "family"
            },
            {
                "title": "Household Cleaning",
                "description": "Weekly household cleaning rotation",
                "suggested_time": "10:00",
                "recurring": "weekly",
                "category": "household"
            },
            {
                "title": "Kids' Sports Practice",
                "description": "Regular sports practice schedule",
                "suggested_time": "16:00",
                "recurring": "weekly",
                "category": "child_activity"
            }
        ],
        "household_events": [
            {
                "title": "Kitchen Deep Clean",
                "description": "Monthly kitchen deep cleaning",
                "suggested_time": "14:00",
                "recurring": "monthly",
                "category": "household"
            },
            {
                "title": "Garden Maintenance",
                "description": "Weekly garden care",
                "suggested_time": "09:00",
                "recurring": "weekly",
                "category": "household"
            }
        ],
        "child_activities": [
            {
                "title": "Homework Time",
                "description": "Daily homework session",
                "suggested_time": "15:30",
                "recurring": "daily",
                "category": "child_activity"
            },
            {
                "title": "Music Lessons",
                "description": "Weekly music lesson",
                "suggested_time": "17:00",
                "recurring": "weekly",
                "category": "child_activity"
            }
        ]
    }
    
    return {
        "suggestions": suggestions.get(event_type, suggestions["family_events"]),
        "event_type": event_type,
        "inspired_by": "Skylight Calendar family organization features"
    }


