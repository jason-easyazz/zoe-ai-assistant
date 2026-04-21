"""
Redesigned Lists Management System
Inspired by Martin AI - Personal productivity and task management
Supports: Personal To-dos, Work To-dos, Wellness Tasks, Home Tasks, Notes, Reminders
With calendar integration and smart scheduling
"""
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta
from enum import Enum
import sqlite3
import json
import os
import asyncio
import httpx

router = APIRouter(prefix="/api/lists", tags=["lists-redesigned"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class ListType(str, Enum):
    PERSONAL_TODOS = "personal_todos"
    WORK_TODOS = "work_todos" 
    WELLNESS_TASKS = "wellness_tasks"
    HOME_TASKS = "home_tasks"
    NOTES = "notes"
    REMINDERS = "reminders"
    SHOPPING = "shopping"
    BUCKET_LIST = "bucket_list"

class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SCHEDULED = "scheduled"

class ListItem(BaseModel):
    id: Optional[str] = None
    text: str
    description: Optional[str] = None
    priority: Priority = Priority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    due_date: Optional[str] = None
    estimated_duration: Optional[int] = None  # minutes
    actual_duration: Optional[int] = None  # minutes
    tags: List[str] = []
    metadata: Dict[str, Any] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class List(BaseModel):
    id: Optional[str] = None
    name: str
    list_type: ListType
    description: Optional[str] = None
    items: List[ListItem] = []
    color: Optional[str] = None
    icon: Optional[str] = None
    shared_with: List[str] = []
    metadata: Dict[str, Any] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class Reminder(BaseModel):
    id: Optional[str] = None
    text: str
    due_date: str
    reminder_type: str = "notification"  # notification, call, email, slack
    priority: Priority = Priority.MEDIUM
    completed: bool = False
    metadata: Dict[str, Any] = {}

class Note(BaseModel):
    id: Optional[str] = None
    title: str
    content: str
    tags: List[str] = []
    category: str = "general"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

def init_lists_db():
    """Initialize redesigned lists database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create lists table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            list_type TEXT NOT NULL,
            description TEXT,
            color TEXT,
            icon TEXT,
            shared_with TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create list items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS list_items (
            id TEXT PRIMARY KEY,
            list_id TEXT NOT NULL,
            text TEXT NOT NULL,
            description TEXT,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'pending',
            due_date TEXT,
            estimated_duration INTEGER,
            actual_duration INTEGER,
            tags TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (list_id) REFERENCES lists (id)
        )
    """)
    
    # Create reminders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            due_date TEXT NOT NULL,
            reminder_type TEXT DEFAULT 'notification',
            priority TEXT DEFAULT 'medium',
            completed BOOLEAN DEFAULT FALSE,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create notes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            category TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lists_type ON lists(list_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_list_id ON list_items(list_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON list_items(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due_date ON reminders(due_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category)")
    
    conn.commit()
    conn.close()

# Initialize on import
init_lists_db()

def generate_id():
    """Generate a unique ID"""
    return f"{int(datetime.now().timestamp() * 1000)}"

# ============================================================================
# LISTS MANAGEMENT
# ============================================================================

@router.get("/")
async def get_lists(list_type: Optional[ListType] = None):
    """Get all lists, optionally filtered by type"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if list_type:
            cursor.execute("SELECT * FROM lists WHERE list_type = ? ORDER BY created_at DESC", (list_type.value,))
        else:
            cursor.execute("SELECT * FROM lists ORDER BY created_at DESC")
        
        lists = []
        for row in cursor.fetchall():
            # Get items for this list
            cursor.execute("SELECT * FROM list_items WHERE list_id = ? ORDER BY created_at", (row['id'],))
            items = []
            for item_row in cursor.fetchall():
                items.append(ListItem(
                    id=item_row['id'],
                    text=item_row['text'],
                    description=item_row['description'],
                    priority=Priority(item_row['priority']),
                    status=TaskStatus(item_row['status']),
                    due_date=item_row['due_date'],
                    estimated_duration=item_row['estimated_duration'],
                    actual_duration=item_row['actual_duration'],
                    tags=json.loads(item_row['tags']) if item_row['tags'] else [],
                    metadata=json.loads(item_row['metadata']) if item_row['metadata'] else {},
                    created_at=item_row['created_at'],
                    updated_at=item_row['updated_at']
                ))
            
            lists.append(List(
                id=row['id'],
                name=row['name'],
                list_type=ListType(row['list_type']),
                description=row['description'],
                items=items,
                color=row['color'],
                icon=row['icon'],
                shared_with=json.loads(row['shared_with']) if row['shared_with'] else [],
                metadata=json.loads(row['metadata']) if row['metadata'] else {},
                created_at=row['created_at'],
                updated_at=row['updated_at']
            ))
        
        conn.close()
        return {"lists": [list.dict() for list in lists]}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get lists: {str(e)}")

@router.post("/")
async def create_list(list_data: List):
    """Create a new list"""
    try:
        list_id = generate_id()
        timestamp = datetime.now().isoformat()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO lists (id, name, list_type, description, color, icon, shared_with, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            list_id,
            list_data.name,
            list_data.list_type.value,
            list_data.description,
            list_data.color,
            list_data.icon,
            json.dumps(list_data.shared_with),
            json.dumps(list_data.metadata),
            timestamp,
            timestamp
        ))
        
        conn.commit()
        conn.close()
        
        list_data.id = list_id
        list_data.created_at = timestamp
        list_data.updated_at = timestamp
        
        return {"message": "List created", "list": list_data.dict()}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create list: {str(e)}")

@router.get("/{list_id}")
async def get_list(list_id: str):
    """Get a specific list with its items"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get list
        cursor.execute("SELECT * FROM lists WHERE id = ?", (list_id,))
        list_row = cursor.fetchone()
        
        if not list_row:
            raise HTTPException(status_code=404, detail="List not found")
        
        # Get items
        cursor.execute("SELECT * FROM list_items WHERE list_id = ? ORDER BY created_at", (list_id,))
        items = []
        for item_row in cursor.fetchall():
            items.append(ListItem(
                id=item_row['id'],
                text=item_row['text'],
                description=item_row['description'],
                priority=Priority(item_row['priority']),
                status=TaskStatus(item_row['status']),
                due_date=item_row['due_date'],
                estimated_duration=item_row['estimated_duration'],
                actual_duration=item_row['actual_duration'],
                tags=json.loads(item_row['tags']) if item_row['tags'] else [],
                metadata=json.loads(item_row['metadata']) if item_row['metadata'] else {},
                created_at=item_row['created_at'],
                updated_at=item_row['updated_at']
            ))
        
        conn.close()
        
        return List(
            id=list_row['id'],
            name=list_row['name'],
            list_type=ListType(list_row['list_type']),
            description=list_row['description'],
            items=items,
            color=list_row['color'],
            icon=list_row['icon'],
            shared_with=json.loads(list_row['shared_with']) if list_row['shared_with'] else [],
            metadata=json.loads(list_row['metadata']) if list_row['metadata'] else {},
            created_at=list_row['created_at'],
            updated_at=list_row['updated_at']
        ).dict()
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get list: {str(e)}")

# ============================================================================
# TASK MANAGEMENT
# ============================================================================

@router.post("/{list_id}/items")
async def add_item(list_id: str, item: ListItem, background_tasks: BackgroundTasks):
    """Add an item to a list"""
    try:
        item_id = generate_id()
        timestamp = datetime.now().isoformat()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO list_items (id, list_id, text, description, priority, status, due_date, 
                                  estimated_duration, actual_duration, tags, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item_id,
            list_id,
            item.text,
            item.description,
            item.priority.value,
            item.status.value,
            item.due_date,
            item.estimated_duration,
            item.actual_duration,
            json.dumps(item.tags),
            json.dumps(item.metadata),
            timestamp,
            timestamp
        ))
        
        # Update list updated_at
        cursor.execute("UPDATE lists SET updated_at = ? WHERE id = ?", (timestamp, list_id))
        
        conn.commit()
        conn.close()
        
        # If item has due_date, schedule it in calendar
        if item.due_date and item.status == TaskStatus.SCHEDULED:
            background_tasks.add_task(schedule_task_in_calendar, item_id, item.text, item.due_date, item.estimated_duration)
        
        item.id = item_id
        item.created_at = timestamp
        item.updated_at = timestamp
        
        return {"message": "Item added", "item": item.dict()}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add item: {str(e)}")

@router.put("/{list_id}/items/{item_id}")
async def update_item(list_id: str, item_id: str, item: ListItem):
    """Update an item in a list"""
    try:
        timestamp = datetime.now().isoformat()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE list_items 
            SET text = ?, description = ?, priority = ?, status = ?, due_date = ?,
                estimated_duration = ?, actual_duration = ?, tags = ?, metadata = ?, updated_at = ?
            WHERE id = ? AND list_id = ?
        """, (
            item.text,
            item.description,
            item.priority.value,
            item.status.value,
            item.due_date,
            item.estimated_duration,
            item.actual_duration,
            json.dumps(item.tags),
            json.dumps(item.metadata),
            timestamp,
            item_id,
            list_id
        ))
        
        # Update list updated_at
        cursor.execute("UPDATE lists SET updated_at = ? WHERE id = ?", (timestamp, list_id))
        
        conn.commit()
        conn.close()
        
        return {"message": "Item updated", "item_id": item_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update item: {str(e)}")

@router.delete("/{list_id}/items/{item_id}")
async def delete_item(list_id: str, item_id: str):
    """Delete an item from a list"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM list_items WHERE id = ? AND list_id = ?", (item_id, list_id))
        
        # Update list updated_at
        timestamp = datetime.now().isoformat()
        cursor.execute("UPDATE lists SET updated_at = ? WHERE id = ?", (timestamp, list_id))
        
        conn.commit()
        conn.close()
        
        return {"message": "Item deleted", "item_id": item_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete item: {str(e)}")

# ============================================================================
# WELLNESS & BURNOUT PREVENTION
# ============================================================================

@router.get("/wellness/status")
async def get_wellness_status():
    """Get current wellness status and burnout risk"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get work tasks from last 7 days
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        cursor.execute("""
            SELECT COUNT(*) as total_tasks, 
                   SUM(actual_duration) as total_duration,
                   COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_tasks
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE l.list_type = 'work_todos' AND li.created_at >= ?
        """, (week_ago,))
        
        work_stats = cursor.fetchone()
        
        # Get wellness tasks
        cursor.execute("""
            SELECT COUNT(*) as wellness_tasks
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE l.list_type = 'wellness_tasks' AND li.status = 'completed'
        """)
        
        wellness_stats = cursor.fetchone()
        
        # Calculate burnout risk
        burnout_risk = "low"
        if work_stats['total_duration'] and work_stats['total_duration'] > 50 * 60:  # 50 hours
            burnout_risk = "high"
        elif work_stats['total_duration'] and work_stats['total_duration'] > 40 * 60:  # 40 hours
            burnout_risk = "medium"
        
        # Get break suggestions
        break_suggestions = []
        if work_stats['total_duration'] and work_stats['total_duration'] > 2 * 60:  # 2 hours
            break_suggestions.append("Take a 15-minute break")
        if work_stats['total_duration'] and work_stats['total_duration'] > 4 * 60:  # 4 hours
            break_suggestions.append("Consider a longer break or lunch")
        if work_stats['total_duration'] and work_stats['total_duration'] > 8 * 60:  # 8 hours
            break_suggestions.append("Time to wrap up for the day")
        
        conn.close()
        
        return {
            "burnout_risk": burnout_risk,
            "work_hours_this_week": round(work_stats['total_duration'] / 60, 1) if work_stats['total_duration'] else 0,
            "tasks_completed": work_stats['completed_tasks'] or 0,
            "wellness_tasks_completed": wellness_stats['wellness_tasks'] or 0,
            "break_suggestions": break_suggestions,
            "recommendations": get_wellness_recommendations(burnout_risk)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get wellness status: {str(e)}")

def get_wellness_recommendations(burnout_risk: str) -> List[str]:
    """Get wellness recommendations based on burnout risk"""
    if burnout_risk == "high":
        return [
            "Schedule a day off or light work day",
            "Take regular breaks every hour",
            "Consider delegating some tasks",
            "Practice stress management techniques"
        ]
    elif burnout_risk == "medium":
        return [
            "Take regular breaks",
            "Ensure work-life balance",
            "Stay hydrated and eat well"
        ]
    else:
        return [
            "Maintain current healthy habits",
            "Continue regular breaks",
            "Keep up the good work!"
        ]

@router.post("/wellness/break-reminder")
async def create_break_reminder(duration_minutes: int = 15):
    """Create a break reminder"""
    try:
        reminder_id = generate_id()
        due_time = datetime.now() + timedelta(minutes=duration_minutes)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO reminders (id, text, due_date, reminder_type, priority, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            reminder_id,
            f"Take a {duration_minutes}-minute break",
            due_time.isoformat(),
            "notification",
            "medium",
            json.dumps({"type": "break_reminder", "duration": duration_minutes})
        ))
        
        conn.commit()
        conn.close()
        
        return {"message": "Break reminder created", "reminder_id": reminder_id, "due_at": due_time.isoformat()}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create break reminder: {str(e)}")

# ============================================================================
# FOCUS TIMER & PRODUCTIVITY
# ============================================================================

@router.post("/focus/pomodoro")
async def start_pomodoro_session(duration_minutes: int = 25, break_minutes: int = 5):
    """Start a Pomodoro focus session"""
    try:
        session_id = generate_id()
        end_time = datetime.now() + timedelta(minutes=duration_minutes)
        break_time = end_time + timedelta(minutes=break_minutes)
        
        # Create focus session reminder
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO reminders (id, text, due_date, reminder_type, priority, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            f"Focus session complete! Take a {break_minutes}-minute break",
            end_time.isoformat(),
            "notification",
            "high",
            json.dumps({"type": "pomodoro", "duration": duration_minutes, "break_duration": break_minutes})
        ))
        
        conn.commit()
        conn.close()
        
        return {
            "message": "Pomodoro session started",
            "session_id": session_id,
            "focus_until": end_time.isoformat(),
            "break_until": break_time.isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start Pomodoro session: {str(e)}")

# ============================================================================
# NOTES MANAGEMENT
# ============================================================================

@router.get("/notes")
async def get_notes(category: Optional[str] = None):
    """Get all notes, optionally filtered by category"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if category:
            cursor.execute("SELECT * FROM notes WHERE category = ? ORDER BY updated_at DESC", (category,))
        else:
            cursor.execute("SELECT * FROM notes ORDER BY updated_at DESC")
        
        notes = []
        for row in cursor.fetchall():
            notes.append(Note(
                id=row['id'],
                title=row['title'],
                content=row['content'],
                tags=json.loads(row['tags']) if row['tags'] else [],
                category=row['category'],
                created_at=row['created_at'],
                updated_at=row['updated_at']
            ))
        
        conn.close()
        return {"notes": [note.dict() for note in notes]}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get notes: {str(e)}")

@router.post("/notes")
async def create_note(note: Note):
    """Create a new note"""
    try:
        note_id = generate_id()
        timestamp = datetime.now().isoformat()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO notes (id, title, content, tags, category, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            note_id,
            note.title,
            note.content,
            json.dumps(note.tags),
            note.category,
            timestamp,
            timestamp
        ))
        
        conn.commit()
        conn.close()
        
        note.id = note_id
        note.created_at = timestamp
        note.updated_at = timestamp
        
        return {"message": "Note created", "note": note.dict()}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create note: {str(e)}")

# ============================================================================
# REMINDERS MANAGEMENT
# ============================================================================

@router.get("/reminders")
async def get_reminders(upcoming_hours: int = 24):
    """Get upcoming reminders"""
    try:
        cutoff_time = datetime.now() + timedelta(hours=upcoming_hours)
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM reminders 
            WHERE due_date <= ? AND completed = FALSE 
            ORDER BY due_date ASC
        """, (cutoff_time.isoformat(),))
        
        reminders = []
        for row in cursor.fetchall():
            reminders.append(Reminder(
                id=row['id'],
                text=row['text'],
                due_date=row['due_date'],
                reminder_type=row['reminder_type'],
                priority=Priority(row['priority']),
                completed=bool(row['completed']),
                metadata=json.loads(row['metadata']) if row['metadata'] else {}
            ))
        
        conn.close()
        return {"reminders": [reminder.dict() for reminder in reminders]}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get reminders: {str(e)}")

# ============================================================================
# CALENDAR INTEGRATION
# ============================================================================

async def schedule_task_in_calendar(item_id: str, text: str, due_date: str, duration_minutes: Optional[int]):
    """Schedule a task in the calendar"""
    try:
        async with httpx.AsyncClient() as client:
            await client.post("http://localhost:8000/api/calendar/events", json={
                "title": text,
                "start_time": due_date,
                "end_time": (datetime.fromisoformat(due_date) + timedelta(minutes=duration_minutes or 60)).isoformat(),
                "description": f"Task from lists: {text}",
                "metadata": {"list_item_id": item_id}
            })
    except Exception as e:
        logger.error(f"Failed to schedule task in calendar: {e}")

# ============================================================================
# ANALYTICS & INSIGHTS
# ============================================================================

@router.get("/analytics/productivity")
async def get_productivity_analytics(days: int = 7):
    """Get productivity analytics"""
    try:
        start_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get task completion stats
        cursor.execute("""
            SELECT l.list_type,
                   COUNT(*) as total_tasks,
                   COUNT(CASE WHEN li.status = 'completed' THEN 1 END) as completed_tasks,
                   AVG(li.actual_duration) as avg_duration
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE li.created_at >= ?
            GROUP BY l.list_type
        """, (start_date,))
        
        stats = cursor.fetchall()
        
        # Calculate productivity metrics
        total_tasks = sum(row['total_tasks'] for row in stats)
        completed_tasks = sum(row['completed_tasks'] for row in stats)
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        
        conn.close()
        
        return {
            "period_days": days,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "completion_rate": round(completion_rate, 1),
            "by_list_type": [
                {
                    "list_type": row['list_type'],
                    "total": row['total_tasks'],
                    "completed": row['completed_tasks'],
                    "completion_rate": round((row['completed_tasks'] / row['total_tasks'] * 100) if row['total_tasks'] > 0 else 0, 1),
                    "avg_duration": round(row['avg_duration'], 1) if row['avg_duration'] else 0
                }
                for row in stats
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get productivity analytics: {str(e)}")

# ============================================================================
# VOICE COMMANDS INTEGRATION
# ============================================================================

@router.post("/voice-command")
async def process_voice_command(command: str, background_tasks: BackgroundTasks):
    """Process voice commands for task management"""
    try:
        command_lower = command.lower()
        
        # Task creation commands
        if any(phrase in command_lower for phrase in ["create task", "add task", "new task", "remind me"]):
            # Extract task details
            task_text = command.replace("create task", "").replace("add task", "").replace("new task", "").replace("remind me", "").strip()
            
            # Determine list type based on context
            list_type = ListType.PERSONAL_TODOS
            if any(word in command_lower for word in ["work", "office", "meeting", "project"]):
                list_type = ListType.WORK_TODOS
            elif any(word in command_lower for word in ["health", "exercise", "break", "wellness"]):
                list_type = ListType.WELLNESS_TASKS
            elif any(word in command_lower for word in ["home", "house", "clean", "fix"]):
                list_type = ListType.HOME_TASKS
            
            # Find or create appropriate list
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM lists WHERE list_type = ? LIMIT 1", (list_type.value,))
            list_row = cursor.fetchone()
            
            if not list_row:
                # Create default list
                list_id = generate_id()
                cursor.execute("""
                    INSERT INTO lists (id, name, list_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (list_id, f"Default {list_type.value.replace('_', ' ').title()}", list_type.value, datetime.now().isoformat(), datetime.now().isoformat()))
            else:
                list_id = list_row[0]
            
            conn.close()
            
            # Add task
            task = ListItem(text=task_text, priority=Priority.MEDIUM)
            await add_item(list_id, task, background_tasks)
            
            return {"message": f"Task created: {task_text}", "list_type": list_type.value}
        
        # Break reminder commands
        elif any(phrase in command_lower for phrase in ["break reminder", "remind me to break", "take a break"]):
            duration = 15  # default
            if "30" in command_lower:
                duration = 30
            elif "60" in command_lower or "hour" in command_lower:
                duration = 60
            
            await create_break_reminder(duration)
            return {"message": f"Break reminder set for {duration} minutes"}
        
        # Pomodoro commands
        elif any(phrase in command_lower for phrase in ["pomodoro", "focus session", "work session"]):
            duration = 25
            if "50" in command_lower:
                duration = 50
            
            result = await start_pomodoro_session(duration)
            return result
        
        else:
            return {"message": "Command not recognized", "suggestions": [
                "Try: 'create task [description]'",
                "Try: 'break reminder'",
                "Try: 'pomodoro session'"
            ]}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process voice command: {str(e)}")

# ============================================================================
# SMART NOTIFICATIONS
# ============================================================================

@router.get("/notifications/smart")
async def get_smart_notifications():
    """Get smart notification suggestions"""
    try:
        notifications = []
        
        # Check for overdue tasks
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT li.text, li.due_date, l.name as list_name
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE li.due_date < ? AND li.status = 'pending'
            ORDER BY li.due_date ASC
        """, (datetime.now().isoformat(),))
        
        overdue_tasks = cursor.fetchall()
        for task in overdue_tasks:
            notifications.append({
                "type": "overdue_task",
                "priority": "high",
                "message": f"Overdue: {task['text']} from {task['list_name']}",
                "action": "complete_task"
            })
        
        # Check for upcoming tasks
        upcoming_time = datetime.now() + timedelta(hours=2)
        cursor.execute("""
            SELECT li.text, li.due_date, l.name as list_name
            FROM list_items li
            JOIN lists l ON li.list_id = l.id
            WHERE li.due_date BETWEEN ? AND ? AND li.status = 'pending'
            ORDER BY li.due_date ASC
        """, (datetime.now().isoformat(), upcoming_time.isoformat()))
        
        upcoming_tasks = cursor.fetchall()
        for task in upcoming_tasks:
            notifications.append({
                "type": "upcoming_task",
                "priority": "medium",
                "message": f"Upcoming: {task['text']} from {task['list_name']}",
                "action": "view_task"
            })
        
        conn.close()
        
        return {"notifications": notifications}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get smart notifications: {str(e)}")






