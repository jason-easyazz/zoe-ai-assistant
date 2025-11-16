"""
Reminders Management System
Handles time-based alerts, notifications, and recurring reminders
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date, time, timedelta
import sqlite3
import json
import os
import asyncio
from enum import Enum
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/reminders", tags=["reminders"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class ReminderType(str, Enum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"

class ReminderCategory(str, Enum):
    MEDICAL = "medical"
    HOUSEHOLD = "household"
    PERSONAL = "personal"
    WORK = "work"
    FAMILY = "family"

class ReminderPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ReminderCreate(BaseModel):
    title: str
    description: Optional[str] = None
    reminder_type: ReminderType = ReminderType.ONCE
    category: ReminderCategory = ReminderCategory.PERSONAL
    priority: ReminderPriority = ReminderPriority.MEDIUM
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    recurring_pattern: Optional[Dict[str, Any]] = None
    linked_list_id: Optional[int] = None
    linked_list_item_id: Optional[int] = None
    family_member: Optional[str] = None
    snooze_minutes: int = 5
    requires_acknowledgment: bool = False

class ReminderUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    reminder_type: Optional[ReminderType] = None
    category: Optional[ReminderCategory] = None
    priority: Optional[ReminderPriority] = None
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    recurring_pattern: Optional[Dict[str, Any]] = None
    family_member: Optional[str] = None
    snooze_minutes: Optional[int] = None
    requires_acknowledgment: Optional[bool] = None
    is_active: Optional[bool] = None

class NotificationCreate(BaseModel):
    reminder_id: int
    notification_time: datetime
    message: str
    notification_type: str = "reminder"
    priority: ReminderPriority = ReminderPriority.MEDIUM

def init_reminders_db():
    """Initialize reminders and notifications tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Reminders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            title TEXT NOT NULL,
            description TEXT,
            reminder_type TEXT NOT NULL DEFAULT 'once',
            category TEXT NOT NULL DEFAULT 'personal',
            priority TEXT NOT NULL DEFAULT 'medium',
            due_date DATE,
            due_time TIME,
            recurring_pattern JSON,
            linked_list_id INTEGER,
            linked_list_item_id INTEGER,
            family_member TEXT,
            snooze_minutes INTEGER DEFAULT 5,
            requires_acknowledgment BOOLEAN DEFAULT FALSE,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (linked_list_id) REFERENCES lists(id),
            FOREIGN KEY (linked_list_item_id) REFERENCES list_items(id)
        )
    """)
    
    # Notifications table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            reminder_id INTEGER,
            notification_time TIMESTAMP NOT NULL,
            message TEXT NOT NULL,
            notification_type TEXT DEFAULT 'reminder',
            priority TEXT DEFAULT 'medium',
            is_delivered BOOLEAN DEFAULT FALSE,
            is_acknowledged BOOLEAN DEFAULT FALSE,
            acknowledged_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reminder_id) REFERENCES reminders(id)
        )
    """)
    
    # Reminder history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminder_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            reminder_id INTEGER,
            action TEXT NOT NULL, -- completed, snoozed, dismissed, missed
            action_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (reminder_id) REFERENCES reminders(id)
        )
    """)
    
    # Create indexes (only if columns exist)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user_active ON reminders(user_id, is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(due_date, due_time)")
    # Check if is_delivered column exists before creating index
    try:
        cursor.execute("SELECT is_delivered FROM notifications LIMIT 1")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_delivered ON notifications(is_delivered)")
    except sqlite3.OperationalError:
        # Column doesn't exist, skip index
        pass
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id)")
    
    conn.commit()
    conn.close()

@router.post("/", response_model=Dict[str, Any])
async def create_reminder(reminder: ReminderCreate, session: AuthenticatedSession = Depends(validate_session)):
    """Create a new reminder"""
    try:
        user_id = session.user_id
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Calculate reminder_time from due_date and due_time
        reminder_timestamp = None
        if reminder.due_date and reminder.due_time:
            reminder_timestamp = datetime.combine(reminder.due_date, reminder.due_time).isoformat()
        elif reminder.due_date:
            reminder_timestamp = datetime.combine(reminder.due_date, datetime.min.time()).isoformat()
        else:
            # Default to tomorrow at 9am if not specified
            reminder_timestamp = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0).isoformat()
        
        # Insert reminder (removed reminder_time column as it doesn't exist in schema)
        cursor.execute("""
            INSERT INTO reminders (
                user_id, title, description, reminder_type, category, priority,
                due_date, due_time, recurring_pattern, linked_list_id, linked_list_item_id,
                family_member, snooze_minutes, requires_acknowledgment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, reminder.title, reminder.description,
            reminder.reminder_type.value, reminder.category.value, reminder.priority.value,
            reminder.due_date, reminder.due_time.isoformat() if reminder.due_time else None, 
            json.dumps(reminder.recurring_pattern) if reminder.recurring_pattern else None,
            reminder.linked_list_id, reminder.linked_list_item_id, reminder.family_member,
            reminder.snooze_minutes, reminder.requires_acknowledgment
        ))
        
        reminder_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return {
            "reminder_id": reminder_id,
            "message": "Reminder created successfully",
            "reminder": {
                "id": reminder_id,
                "title": reminder.title,
                "category": reminder.category.value,
                "priority": reminder.priority.value,
                "due_date": reminder.due_date.isoformat() if reminder.due_date else None,
                "due_time": reminder.due_time.isoformat() if reminder.due_time else None
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=Dict[str, Any])
async def get_reminders(
    session: AuthenticatedSession = Depends(validate_session),
    category: Optional[ReminderCategory] = Query(None),
    priority: Optional[ReminderPriority] = Query(None),
    is_active: bool = Query(True),
    limit: int = Query(50)
):
    """Get reminders with optional filtering"""
    try:
        user_id = session.user_id
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM reminders WHERE user_id = ? AND is_active = ?"
        params = [user_id, is_active]
        
        # Order by due_date and due_time (not reminder_time which doesn't exist in schema)
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
                "recurring_pattern": json.loads(row["recurring_pattern"]) if row["recurring_pattern"] else None,
                "linked_list_id": row["linked_list_id"],
                "linked_list_item_id": row["linked_list_item_id"],
                "family_member": row["family_member"],
                "snooze_minutes": row["snooze_minutes"],
                "requires_acknowledgment": bool(row["requires_acknowledgment"]),
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }
            reminders.append(reminder)
        
        conn.close()
        return {"reminders": reminders, "count": len(reminders)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/today", response_model=Dict[str, Any])
async def get_todays_reminders(session: AuthenticatedSession = Depends(validate_session)):
    """Get today's reminders"""
    try:
        user_id = session.user_id
        today = date.today()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM reminders 
            WHERE user_id = ? AND is_active = TRUE 
            AND (due_date = ? OR due_date IS NULL)
            ORDER BY due_time ASC
        """, (user_id, today.isoformat()))
        
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
                "family_member": row["family_member"]
            }
            reminders.append(reminder)
        
        conn.close()
        return {"reminders": reminders, "date": today.isoformat(), "count": len(reminders)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{reminder_id}", response_model=Dict[str, Any])
async def update_reminder(
    reminder_id: int,
    reminder_update: ReminderUpdate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update a reminder"""
    try:
        user_id = session.user_id
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Build update query dynamically
        update_fields = []
        params = []
        
        if reminder_update.title is not None:
            update_fields.append("title = ?")
            params.append(reminder_update.title)
        
        if reminder_update.description is not None:
            update_fields.append("description = ?")
            params.append(reminder_update.description)
        
        if reminder_update.reminder_type is not None:
            update_fields.append("reminder_type = ?")
            params.append(reminder_update.reminder_type.value)
        
        if reminder_update.category is not None:
            update_fields.append("category = ?")
            params.append(reminder_update.category.value)
        
        if reminder_update.priority is not None:
            update_fields.append("priority = ?")
            params.append(reminder_update.priority.value)
        
        if reminder_update.due_date is not None:
            update_fields.append("due_date = ?")
            params.append(reminder_update.due_date)
        
        if reminder_update.due_time is not None:
            update_fields.append("due_time = ?")
            params.append(reminder_update.due_time.isoformat())
        
        if reminder_update.recurring_pattern is not None:
            update_fields.append("recurring_pattern = ?")
            params.append(json.dumps(reminder_update.recurring_pattern))
        
        if reminder_update.family_member is not None:
            update_fields.append("family_member = ?")
            params.append(reminder_update.family_member)
        
        if reminder_update.snooze_minutes is not None:
            update_fields.append("snooze_minutes = ?")
            params.append(reminder_update.snooze_minutes)
        
        if reminder_update.requires_acknowledgment is not None:
            update_fields.append("requires_acknowledgment = ?")
            params.append(reminder_update.requires_acknowledgment)
        
        if reminder_update.is_active is not None:
            update_fields.append("is_active = ?")
            params.append(reminder_update.is_active)
        
        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        update_fields.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.extend([reminder_id, user_id])
        
        query = f"UPDATE reminders SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
        cursor.execute(query, params)
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Reminder not found")
        
        conn.commit()
        conn.close()
        
        return {"message": "Reminder updated successfully", "reminder_id": reminder_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{reminder_id}", response_model=Dict[str, Any])
async def delete_reminder(reminder_id: int, session: AuthenticatedSession = Depends(validate_session)):
    """Delete a reminder"""
    try:
        user_id = session.user_id
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Soft delete by setting is_active to False
        cursor.execute("""
            UPDATE reminders 
            SET is_active = FALSE, updated_at = ? 
            WHERE id = ? AND user_id = ?
        """, (datetime.now().isoformat(), reminder_id, user_id))
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Reminder not found")
        
        conn.commit()
        conn.close()
        
        return {"message": "Reminder deleted successfully", "reminder_id": reminder_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{reminder_id}/snooze", response_model=Dict[str, Any])
async def snooze_reminder(
    reminder_id: int,
    snooze_minutes: int = Query(5),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Snooze a reminder"""
    try:
        user_id = session.user_id
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get current reminder
        cursor.execute("SELECT * FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
        reminder = cursor.fetchone()
        
        if not reminder:
            conn.close()
            raise HTTPException(status_code=404, detail="Reminder not found")
        
        # Calculate new due time
        if reminder[6] and reminder[7]:  # due_date and due_time
            current_due = datetime.combine(reminder[6], reminder[7])
            new_due = current_due + timedelta(minutes=snooze_minutes)
            
            cursor.execute("""
                UPDATE reminders 
                SET due_date = ?, due_time = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
            """, (new_due.date(), new_due.time(), datetime.now().isoformat(), reminder_id, user_id))
        else:
            # If no specific time, just update the reminder
            cursor.execute("""
                UPDATE reminders 
                SET updated_at = ?
                WHERE id = ? AND user_id = ?
            """, (datetime.now().isoformat(), reminder_id, user_id))
        
        # Log snooze action
        cursor.execute("""
            INSERT INTO reminder_history (user_id, reminder_id, action, notes)
            VALUES (?, ?, ?, ?)
        """, (user_id, reminder_id, "snoozed", f"Snoozed for {snooze_minutes} minutes"))
        
        conn.commit()
        conn.close()
        
        return {"message": f"Reminder snoozed for {snooze_minutes} minutes", "reminder_id": reminder_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{reminder_id}/acknowledge", response_model=Dict[str, Any])
async def acknowledge_reminder(
    reminder_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Acknowledge a reminder"""
    try:
        user_id = session.user_id
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Log acknowledgment
        cursor.execute("""
            INSERT INTO reminder_history (user_id, reminder_id, action, notes)
            VALUES (?, ?, ?, ?)
        """, (user_id, reminder_id, "acknowledged", "Reminder acknowledged"))
        
        # If it's a one-time reminder, mark as completed
        cursor.execute("""
            UPDATE reminders 
            SET is_active = FALSE, updated_at = ?
            WHERE id = ? AND user_id = ? AND reminder_type = 'once'
        """, (datetime.now().isoformat(), reminder_id, user_id))
        
        conn.commit()
        conn.close()
        
        return {"message": "Reminder acknowledged", "reminder_id": reminder_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/notifications/pending", response_model=Dict[str, Any])
async def get_pending_notifications(session: AuthenticatedSession = Depends(validate_session)):
    """Get pending notifications"""
    try:
        user_id = session.user_id
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT n.*
            FROM notifications n
            WHERE n.user_id = ? AND n.is_read = FALSE 
            ORDER BY n.created_at ASC
        """, (user_id,))
        
        notifications = []
        for row in cursor.fetchall():
            notification = {
                "id": row["id"],
                "title": row["title"] if "title" in row.keys() else "",
                "message": row["message"],
                "notification_type": row["notification_type"],
                "is_read": bool(row["is_read"]),
                "is_delivered": bool(row["is_read"]),  # Map is_read to is_delivered for compatibility
                "priority": row["priority"] if row["priority"] else "medium",
                "action_url": row["action_url"] if "action_url" in row.keys() else None,
                "created_at": row["created_at"],
                "read_at": row["read_at"] if "read_at" in row.keys() else None
            }
            notifications.append(notification)
        
        conn.close()
        return {"notifications": notifications, "count": len(notifications)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/notifications/{notification_id}/deliver", response_model=Dict[str, Any])
async def mark_notification_delivered(
    notification_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Mark notification as delivered"""
    try:
        user_id = session.user_id
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE notifications 
            SET is_read = TRUE, read_at = ? 
            WHERE id = ? AND user_id = ?
        """, (datetime.now().isoformat(), notification_id, user_id))
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Notification not found")
        
        conn.commit()
        conn.close()
        
        return {"message": "Notification marked as delivered", "notification_id": notification_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Initialize database on startup
init_reminders_db()
