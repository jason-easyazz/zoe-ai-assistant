"""
Time Intent Handlers
====================

Handles time-related intents:
- TimeNow: Get current time
- DateToday: Get today's date
- TimerSet: Set a countdown timer
- TimerCancel: Cancel an active timer
"""

import logging
import re
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

from intent_system.classifiers import ZoeIntent

logger = logging.getLogger(__name__)

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Default timezone (can be overridden by user settings)
DEFAULT_TIMEZONE = "Australia/Sydney"


def get_user_timezone(user_id: str) -> str:
    """Get user's timezone from settings, or return default."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT setting_value FROM user_settings 
            WHERE user_id = ? AND setting_key = 'timezone'
        """, (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.warning(f"Could not get user timezone: {e}")
    
    return DEFAULT_TIMEZONE


def parse_duration(duration_str: str) -> Optional[int]:
    """
    Parse a duration string into seconds.
    
    Examples:
        "5 minutes" -> 300
        "1 hour" -> 3600
        "90 seconds" -> 90
        "1 hour 30 minutes" -> 5400
        "half an hour" -> 1800
    """
    if not duration_str:
        return None
    
    duration_str = duration_str.lower().strip()
    total_seconds = 0
    
    # Handle special cases
    if "half an hour" in duration_str or "half hour" in duration_str:
        return 1800
    if "quarter hour" in duration_str or "quarter of an hour" in duration_str:
        return 900
    
    # Parse hours
    hours_match = re.search(r'(\d+)\s*(?:hour|hr)s?', duration_str)
    if hours_match:
        total_seconds += int(hours_match.group(1)) * 3600
    
    # Parse minutes
    minutes_match = re.search(r'(\d+)\s*(?:minute|min)s?', duration_str)
    if minutes_match:
        total_seconds += int(minutes_match.group(1)) * 60
    
    # Parse seconds
    seconds_match = re.search(r'(\d+)\s*(?:second|sec)s?', duration_str)
    if seconds_match:
        total_seconds += int(seconds_match.group(1))
    
    # If just a number, assume minutes
    if total_seconds == 0:
        number_match = re.search(r'^(\d+)$', duration_str)
        if number_match:
            total_seconds = int(number_match.group(1)) * 60
    
    return total_seconds if total_seconds > 0 else None


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    elif seconds < 3600:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if remaining_seconds == 0:
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        return f"{minutes} minute{'s' if minutes != 1 else ''} {remaining_seconds} second{'s' if remaining_seconds != 1 else ''}"
    else:
        hours = seconds // 3600
        remaining_minutes = (seconds % 3600) // 60
        if remaining_minutes == 0:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        return f"{hours} hour{'s' if hours != 1 else ''} {remaining_minutes} minute{'s' if remaining_minutes != 1 else ''}"


def ensure_timers_table():
    """Ensure timers table exists with all columns."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS timers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                label TEXT,
                duration_seconds INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                completed BOOLEAN DEFAULT FALSE,
                cancelled BOOLEAN DEFAULT FALSE,
                source_device_id TEXT,
                source_session_id TEXT,
                source_room TEXT
            )
        """)
        
        # Add source columns if they don't exist (migration)
        for col in ["source_device_id", "source_session_id", "source_room"]:
            try:
                cursor.execute(f"ALTER TABLE timers ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to create timers table: {e}")


async def handle_time_now(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle TimeNow intent - return current time.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        # Get user's timezone
        tz_name = get_user_timezone(user_id)
        tz = ZoneInfo(tz_name)
        
        now = datetime.now(tz)
        
        # Format time nicely (12-hour with AM/PM)
        time_str = now.strftime("%-I:%M %p")  # e.g., "3:45 PM"
        
        return {
            "success": True,
            "message": f"🕐 It's {time_str}",
            "data": {
                "time": now.strftime("%H:%M:%S"),
                "time_12h": time_str,
                "timezone": tz_name
            }
        }
        
    except Exception as e:
        logger.error(f"TimeNow handler failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't get the current time."
        }


async def handle_date_today(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle DateToday intent - return today's date.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        # Get user's timezone
        tz_name = get_user_timezone(user_id)
        tz = ZoneInfo(tz_name)
        
        now = datetime.now(tz)
        
        # Format date nicely
        day_name = now.strftime("%A")  # e.g., "Saturday"
        date_str = now.strftime("%B %-d, %Y")  # e.g., "December 21, 2024"
        
        return {
            "success": True,
            "message": f"📅 Today is {day_name}, {date_str}",
            "data": {
                "date": now.strftime("%Y-%m-%d"),
                "day_name": day_name,
                "formatted": f"{day_name}, {date_str}",
                "timezone": tz_name
            }
        }
        
    except Exception as e:
        logger.error(f"DateToday handler failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't get today's date."
        }


async def handle_timer_set(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle TimerSet intent - create a countdown timer.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context (includes device_id for alert routing)
        
    Returns:
        Dict with success, message, and data
    """
    try:
        ensure_timers_table()
        
        # Extract duration from slots
        duration_str = intent.slots.get("duration", "")
        label = intent.slots.get("label", "Timer")
        
        if not duration_str:
            return {
                "success": False,
                "message": "How long should I set the timer for?"
            }
        
        # Parse duration
        duration_seconds = parse_duration(duration_str)
        
        if not duration_seconds:
            return {
                "success": False,
                "message": f"I couldn't understand the duration '{duration_str}'. Try something like '5 minutes' or '1 hour'."
            }
        
        # Calculate expiration time
        now = datetime.now()
        expires_at = now + timedelta(seconds=duration_seconds)
        
        # Get source device info from context (for alert routing)
        source_device_id = context.get("device_id")
        source_session_id = context.get("session_id")
        source_room = None
        
        # Try to get room from device registry
        if source_device_id:
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT room FROM devices WHERE id = ?", (source_device_id,))
                row = cursor.fetchone()
                if row:
                    source_room = row[0]
                conn.close()
            except Exception:
                pass
        
        # Store timer in database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO timers (user_id, label, duration_seconds, created_at, expires_at,
                               source_device_id, source_session_id, source_room)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, label, duration_seconds, now.isoformat(), expires_at.isoformat(),
              source_device_id, source_session_id, source_room))
        
        timer_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Format confirmation message
        duration_formatted = format_duration(duration_seconds)
        expires_str = expires_at.strftime("%-I:%M %p")
        
        # Personalize if we know the room
        location_msg = f" on this device" if source_device_id else ""
        if source_room:
            location_msg = f" in the {source_room}"
        
        logger.info(f"⏱️ Timer set for {duration_formatted} by user {user_id} from device {source_device_id} (room: {source_room})")
        
        return {
            "success": True,
            "message": f"⏱️ Timer set for {duration_formatted}. I'll alert you{location_msg} at {expires_str}.",
            "data": {
                "timer_id": timer_id,
                "duration_seconds": duration_seconds,
                "duration_formatted": duration_formatted,
                "expires_at": expires_at.isoformat(),
                "label": label,
                "source_device_id": source_device_id,
                "source_room": source_room
            }
        }
        
    except Exception as e:
        logger.error(f"TimerSet handler failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't set that timer."
        }


async def handle_timer_show(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle TimerShow intent - show active timers.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        ensure_timers_table()
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, label, duration_seconds, expires_at
            FROM timers
            WHERE user_id = ?
            AND completed = FALSE
            AND cancelled = FALSE
            AND expires_at > datetime('now')
            ORDER BY expires_at ASC
        """, (user_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return {
                "success": True,
                "message": "You don't have any active timers. ⏱️",
                "data": {"timers": []}
            }
        
        timers = []
        lines = ["Your active timers:"]
        
        for row in rows:
            expires_at = datetime.fromisoformat(row["expires_at"])
            remaining = (expires_at - datetime.now()).total_seconds()
            remaining_formatted = format_duration(max(0, int(remaining)))
            
            timers.append({
                "timer_id": row["id"],
                "label": row["label"],
                "remaining": remaining_formatted
            })
            
            label = row["label"] or "Timer"
            lines.append(f"⏱️ {label}: {remaining_formatted} remaining")
        
        return {
            "success": True,
            "message": "\n".join(lines),
            "data": {"timers": timers}
        }
        
    except Exception as e:
        logger.error(f"TimerShow handler failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't check your timers."
        }


async def handle_timer_cancel(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle TimerCancel intent - cancel an active timer.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        ensure_timers_table()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get active timers for this user
        cursor.execute("""
            SELECT id, label, duration_seconds, expires_at
            FROM timers
            WHERE user_id = ? AND completed = FALSE AND cancelled = FALSE
            AND expires_at > datetime('now')
            ORDER BY expires_at ASC
        """, (user_id,))
        
        active_timers = cursor.fetchall()
        
        if not active_timers:
            conn.close()
            return {
                "success": True,
                "message": "You don't have any active timers."
            }
        
        # Cancel all active timers (or specific one if label provided)
        label_filter = intent.slots.get("label")
        
        if label_filter:
            # Cancel timer with matching label
            cursor.execute("""
                UPDATE timers SET cancelled = TRUE
                WHERE user_id = ? AND completed = FALSE AND cancelled = FALSE
                AND label LIKE ?
            """, (user_id, f"%{label_filter}%"))
            cancelled_count = cursor.rowcount
        else:
            # Cancel all active timers
            cursor.execute("""
                UPDATE timers SET cancelled = TRUE
                WHERE user_id = ? AND completed = FALSE AND cancelled = FALSE
                AND expires_at > datetime('now')
            """, (user_id,))
            cancelled_count = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        if cancelled_count == 0:
            return {
                "success": True,
                "message": "No matching timers found to cancel."
            }
        elif cancelled_count == 1:
            return {
                "success": True,
                "message": "⏱️ Timer cancelled.",
                "data": {"cancelled_count": cancelled_count}
            }
        else:
            return {
                "success": True,
                "message": f"⏱️ Cancelled {cancelled_count} timers.",
                "data": {"cancelled_count": cancelled_count}
            }
        
    except Exception as e:
        logger.error(f"TimerCancel handler failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't cancel the timer."
        }

