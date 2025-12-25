"""
Calendar Intent Handlers
========================

Handles calendar/event-related intents:
- CalendarShow: Show upcoming events
- CalendarAdd: Create new events
- CalendarToday: Show today's events
- CalendarDelete: Delete an event
- CalendarComplete: Mark an event as completed
"""

import logging
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, Any

from intent_system.classifiers import ZoeIntent

logger = logging.getLogger(__name__)


def get_db_connection():
    """Get SQLite database connection."""
    return sqlite3.connect("/app/data/zoe.db")


async def handle_calendar_show(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle CalendarShow intent - show upcoming calendar events.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        # Get date range from slots or default to next 7 days
        days_ahead = intent.slots.get("days", 7)
        today = date.today()
        end_date = today + timedelta(days=days_ahead)
        
        # Query database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                id, title, start_date, start_time, end_time, 
                description, category, location
            FROM events 
            WHERE user_id = ? 
                AND start_date >= ? 
                AND start_date <= ?
            ORDER BY start_date ASC, start_time ASC
            LIMIT 20
        """, (user_id, today.isoformat(), end_date.isoformat()))
        
        events = cursor.fetchall()
        conn.close()
        
        if not events:
            return {
                "success": True,
                "message": f"📅 You don't have any events scheduled for the next {days_ahead} days.",
                "data": {"events": [], "count": 0}
            }
        
        # Format events nicely
        response_lines = [f"📅 **Your upcoming events:**\n"]
        current_date = None
        
        for event in events:
            event_id, title, event_date, start_time, end_time, desc, category, location = event
            
            # Add date header if changed
            if event_date != current_date:
                current_date = event_date
                # Format date nicely (e.g., "Today", "Tomorrow", or "Mon, Dec 25")
                event_date_obj = datetime.strptime(event_date, "%Y-%m-%d").date()
                days_diff = (event_date_obj - today).days
                
                if days_diff == 0:
                    date_str = "**Today**"
                elif days_diff == 1:
                    date_str = "**Tomorrow**"
                else:
                    date_str = f"**{event_date_obj.strftime('%A, %b %d')}**"
                
                response_lines.append(f"\n{date_str}")
            
            # Format time
            time_str = start_time if start_time else "All day"
            if end_time and end_time != start_time:
                time_str = f"{start_time} - {end_time}"
            
            # Build event line
            event_line = f"• {time_str}: {title}"
            if location:
                event_line += f" @ {location}"
            
            response_lines.append(event_line)
        
        message = "\n".join(response_lines)
        
        return {
            "success": True,
            "message": message,
            "data": {
                "events": [
                    {
                        "id": e[0],
                        "title": e[1],
                        "date": e[2],
                        "start_time": e[3],
                        "end_time": e[4]
                    }
                    for e in events
                ],
                "count": len(events)
            }
        }
        
    except Exception as e:
        logger.error(f"Calendar show failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't retrieve your calendar events."
        }


async def handle_calendar_today(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle CalendarToday intent - show today's events.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        today = date.today()
        
        # Query database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                id, title, start_time, end_time, location, description
            FROM events 
            WHERE user_id = ? AND start_date = ?
            ORDER BY start_time ASC
        """, (user_id, today.isoformat()))
        
        events = cursor.fetchall()
        conn.close()
        
        if not events:
            return {
                "success": True,
                "message": "📅 You don't have any events scheduled for today.",
                "data": {"events": [], "count": 0}
            }
        
        # Format events
        response_lines = [f"📅 **Today's schedule:**\n"]
        
        for event in events:
            event_id, title, start_time, end_time, location, desc = event
            
            # Format time
            time_str = start_time if start_time else "All day"
            if end_time and end_time != start_time:
                time_str = f"{start_time} - {end_time}"
            
            # Build event line
            event_line = f"• {time_str}: {title}"
            if location:
                event_line += f" @ {location}"
            
            response_lines.append(event_line)
        
        message = "\n".join(response_lines)
        
        return {
            "success": True,
            "message": message,
            "data": {
                "events": [
                    {
                        "id": e[0],
                        "title": e[1],
                        "start_time": e[2],
                        "end_time": e[3]
                    }
                    for e in events
                ],
                "count": len(events)
            }
        }
        
    except Exception as e:
        logger.error(f"Calendar today failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't retrieve today's events."
        }


def get_user_name(user_id: str) -> str:
    """Get user's first name for personalized responses."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT first_name FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except:
        pass
    return ""


def infer_event_category(title: str) -> str:
    """Infer event category from title."""
    title_lower = title.lower()
    
    work_keywords = ["meeting", "client", "project", "deadline", "presentation", 
                     "standup", "review", "interview", "conference", "call with"]
    personal_keywords = ["doctor", "dentist", "birthday", "anniversary", "gym",
                        "haircut", "lunch with", "dinner with", "date"]
    
    for kw in work_keywords:
        if kw in title_lower:
            return "Work"
    for kw in personal_keywords:
        if kw in title_lower:
            return "Personal"
    
    return "General"


async def handle_calendar_add(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle CalendarAdd intent - create a new calendar event.
    
    Features:
    - Smart category inference (work vs personal)
    - Asks for time if not provided
    - Personalized confirmations
    """
    try:
        # Extract fields from slots
        title = intent.slots.get("title") or intent.slots.get("event") or intent.slots.get("task")
        event_date = intent.slots.get("date")
        start_time = intent.slots.get("time") or intent.slots.get("start_time")
        
        user_name = get_user_name(user_id)
        name_part = f" {user_name}" if user_name else ""
        
        if not title:
            return {
                "success": False,
                "message": f"What would you like me to add to your calendar{name_part}?",
                "needs_clarification": True,
                "clarification_type": "event_title"
            }
        
        # If no time specified for a meeting-type event, ask for clarification
        if not start_time and any(kw in title.lower() for kw in ["meeting", "call", "appointment"]):
            return {
                "success": False,
                "message": f"Got it{name_part}! When should I schedule \"{title}\"?",
                "needs_clarification": True,
                "clarification_type": "event_time",
                "pending_event": title,
                "data": {"title": title, "date": event_date}
            }
        
        # Default to today if no date specified
        if not event_date:
            event_date = date.today().isoformat()
        
        # Smart category inference
        category = infer_event_category(title)
        
        # Optional fields
        end_time = intent.slots.get("end_time")
        description = intent.slots.get("description", "")
        location = intent.slots.get("location", "")
        
        # Insert into database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO events (
                user_id, title, start_date, start_time, end_time,
                description, category, location, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, title, event_date, start_time, end_time,
            description, category, location,
            datetime.now().isoformat(),
            datetime.now().isoformat()
        ))
        
        conn.commit()
        event_id = cursor.lastrowid
        conn.close()
        
        # Format personalized confirmation
        date_obj = datetime.strptime(event_date, "%Y-%m-%d").date()
        today = date.today()
        
        if date_obj == today:
            date_str = "today"
        elif date_obj == today + timedelta(days=1):
            date_str = "tomorrow"
        else:
            date_str = date_obj.strftime("%A, %B %d")
        
        # Build response based on category
        if category == "Work":
            if user_name:
                message = f"No worries {user_name}, I've added \"{title}\" as a work event"
            else:
                message = f"Added \"{title}\" as a work event"
            message += " 💼"
        else:
            if user_name:
                message = f"Got it {user_name}! I've added \"{title}\" to your calendar"
            else:
                message = f"Added \"{title}\" to your calendar"
            message += " 📅"
        
        if start_time:
            message += f" for {date_str} at {start_time}"
        else:
            message += f" for {date_str}"
        
        if location:
            message += f" @ {location}"
        
        return {
            "success": True,
            "message": message,
            "data": {
                "event_id": event_id,
                "title": title,
                "date": event_date,
                "start_time": start_time,
                "category": category,
                "inferred_category": True
            }
        }
        
    except Exception as e:
        logger.error(f"Calendar add failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't add that event. Can you try again?"
        }


async def handle_calendar_delete(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle CalendarDelete intent - delete a calendar event.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        # Get event identifier from slots
        event_id = intent.slots.get("event_id")
        title = intent.slots.get("title") or intent.slots.get("event")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if event_id:
            # Delete by ID
            cursor.execute("""
                DELETE FROM events 
                WHERE id = ? AND user_id = ?
            """, (event_id, user_id))
            deleted_title = f"event #{event_id}"
        elif title:
            # First find the event by title (partial match)
            cursor.execute("""
                SELECT id, title FROM events 
                WHERE user_id = ? AND title LIKE ?
                ORDER BY start_date DESC
                LIMIT 1
            """, (user_id, f"%{title}%"))
            
            row = cursor.fetchone()
            if row:
                cursor.execute("DELETE FROM events WHERE id = ?", (row[0],))
                deleted_title = row[1]
            else:
                conn.close()
                return {
                    "success": False,
                    "message": f"I couldn't find an event matching '{title}'."
                }
        else:
            # Try to use context (last mentioned event)
            last_event_id = context.get("last_event_id")
            if last_event_id:
                cursor.execute("""
                    SELECT title FROM events WHERE id = ? AND user_id = ?
                """, (last_event_id, user_id))
                row = cursor.fetchone()
                if row:
                    cursor.execute("DELETE FROM events WHERE id = ?", (last_event_id,))
                    deleted_title = row[0]
                else:
                    conn.close()
                    return {
                        "success": False,
                        "message": "Which event would you like to delete?"
                    }
            else:
                conn.close()
                return {
                    "success": False,
                    "message": "Which event would you like to delete? Please tell me the event name."
                }
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            return {
                "success": True,
                "message": f"🗑️ Deleted **{deleted_title}** from your calendar.",
                "data": {"deleted_count": deleted_count}
            }
        else:
            return {
                "success": False,
                "message": "I couldn't find that event to delete."
            }
        
    except Exception as e:
        logger.error(f"Calendar delete failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't delete that event."
        }


async def handle_calendar_complete(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle CalendarComplete intent - mark a calendar event as completed.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        # Get event identifier from slots
        event_id = intent.slots.get("event_id")
        title = intent.slots.get("title") or intent.slots.get("event")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if completed column exists, add if not
        cursor.execute("PRAGMA table_info(events)")
        columns = [row[1] for row in cursor.fetchall()]
        if "completed" not in columns:
            cursor.execute("ALTER TABLE events ADD COLUMN completed BOOLEAN DEFAULT FALSE")
            conn.commit()
        
        if event_id:
            # Update by ID
            cursor.execute("""
                UPDATE events SET completed = TRUE, updated_at = ?
                WHERE id = ? AND user_id = ?
            """, (datetime.now().isoformat(), event_id, user_id))
            completed_title = f"event #{event_id}"
        elif title:
            # First find the event by title
            cursor.execute("""
                SELECT id, title FROM events 
                WHERE user_id = ? AND title LIKE ? AND (completed IS NULL OR completed = FALSE)
                ORDER BY start_date DESC
                LIMIT 1
            """, (user_id, f"%{title}%"))
            
            row = cursor.fetchone()
            if row:
                cursor.execute("""
                    UPDATE events SET completed = TRUE, updated_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), row[0]))
                completed_title = row[1]
            else:
                conn.close()
                return {
                    "success": False,
                    "message": f"I couldn't find an active event matching '{title}'."
                }
        else:
            # Try to use context
            last_event_id = context.get("last_event_id")
            if last_event_id:
                cursor.execute("""
                    SELECT title FROM events WHERE id = ? AND user_id = ?
                """, (last_event_id, user_id))
                row = cursor.fetchone()
                if row:
                    cursor.execute("""
                        UPDATE events SET completed = TRUE, updated_at = ?
                        WHERE id = ?
                    """, (datetime.now().isoformat(), last_event_id))
                    completed_title = row[0]
                else:
                    conn.close()
                    return {
                        "success": False,
                        "message": "Which event would you like to mark as complete?"
                    }
            else:
                conn.close()
                return {
                    "success": False,
                    "message": "Which event would you like to mark as complete? Please tell me the event name."
                }
        
        updated_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if updated_count > 0:
            return {
                "success": True,
                "message": f"✅ Marked **{completed_title}** as complete!",
                "data": {"completed_count": updated_count}
            }
        else:
            return {
                "success": False,
                "message": "I couldn't find that event to mark as complete."
            }
        
    except Exception as e:
        logger.error(f"Calendar complete failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't mark that event as complete."
        }

