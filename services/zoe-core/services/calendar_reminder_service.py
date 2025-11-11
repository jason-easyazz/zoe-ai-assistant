"""
Calendar Reminder Service
Automatically sends push notifications for upcoming calendar events
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any
import aiosqlite

from services.push_notification_service import get_push_service
from models.push_subscription import NotificationPayload

logger = logging.getLogger(__name__)


class CalendarReminderService:
    """Service for sending calendar event reminders"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.getenv("DATABASE_PATH", os.getenv("DATABASE_PATH", str(Path(__file__).parent.parent.parent.resolve() / "data" / "zoe.db")))
        self.db_path = db_path
        self.push_service = get_push_service()
        self.check_interval = 60  # Check every 60 seconds
        self._running = False
        self._task = None
    
    async def start(self):
        """Start the reminder service"""
        if self._running:
            logger.warning("Calendar reminder service already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._reminder_loop())
        logger.info("✅ Calendar reminder service started")
    
    async def stop(self):
        """Stop the reminder service"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Calendar reminder service stopped")
    
    async def _reminder_loop(self):
        """Main loop that checks for upcoming events"""
        while self._running:
            try:
                await self._check_upcoming_events()
            except Exception as e:
                logger.error(f"❌ Error in reminder loop: {e}")
            
            # Wait before next check
            await asyncio.sleep(self.check_interval)
    
    async def _check_upcoming_events(self):
        """Check for events that need reminders"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Get all users with calendar reminders enabled
            cursor = await db.execute("""
                SELECT DISTINCT u.user_id, np.calendar_reminder_minutes
                FROM users u
                LEFT JOIN notification_preferences np ON u.user_id = np.user_id
                WHERE (np.calendar_reminders IS NULL OR np.calendar_reminders = 1)
            """)
            users = await cursor.fetchall()
            
            for user in users:
                user_id = user['user_id']
                reminder_minutes = user['calendar_reminder_minutes'] or 15
                
                await self._send_reminders_for_user(user_id, reminder_minutes)
    
    async def _send_reminders_for_user(self, user_id: str, reminder_minutes: int):
        """Send reminders for a specific user"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Calculate time window for reminders
            now = datetime.now()
            reminder_time = now + timedelta(minutes=reminder_minutes)
            window_start = reminder_time - timedelta(minutes=1)
            window_end = reminder_time + timedelta(minutes=1)
            
            # Get events in the reminder window that haven't been reminded yet
            cursor = await db.execute("""
                SELECT 
                    e.id, e.title, e.start_date, e.start_time, 
                    e.end_time, e.location, e.description
                FROM calendar_events e
                LEFT JOIN notification_log nl ON (
                    nl.user_id = e.user_id AND
                    nl.notification_type = 'calendar' AND
                    nl.data LIKE '%"event_id":' || e.id || '%' AND
                    nl.sent_at > datetime('now', '-24 hours')
                )
                WHERE e.user_id = ?
                  AND e.start_date = date(?)
                  AND e.start_time >= time(?)
                  AND e.start_time <= time(?)
                  AND nl.id IS NULL
                ORDER BY e.start_time
                LIMIT 5
            """, (
                user_id,
                window_start.date(),
                window_start.time(),
                window_end.time()
            ))
            
            events = await cursor.fetchall()
            
            for event in events:
                await self._send_event_reminder(user_id, dict(event), reminder_minutes)
    
    async def _send_event_reminder(self, user_id: str, event: Dict[str, Any], minutes_before: int):
        """Send push notification for a specific event"""
        try:
            # Format event time
            event_time = event['start_time']
            if event_time:
                try:
                    time_obj = datetime.strptime(event_time, '%H:%M:%S')
                    event_time_str = time_obj.strftime('%I:%M %p')
                except:
                    event_time_str = event_time
            else:
                event_time_str = 'All day'
            
            # Build notification
            title = f"📅 {event['title']} in {minutes_before} min"
            
            body_parts = [f"Starting at {event_time_str}"]
            if event.get('location'):
                body_parts.append(f"📍 {event['location']}")
            
            body = " • ".join(body_parts)
            
            # Create notification payload
            payload = NotificationPayload(
                title=title,
                body=body,
                url=f"/calendar.html?date={event['start_date']}&event={event['id']}",
                icon="/icons/icon-192.png",
                badge="/icons/icon-72.png",
                tag=f"calendar-{event['id']}",
                vibrate=[200, 100, 200],
                requireInteraction=True,  # Keep notification visible
                data={
                    "event_id": event['id'],
                    "type": "calendar_reminder",
                    "start_time": event['start_time']
                }
            )
            
            # Send notification
            result = await self.push_service.send_notification(user_id, payload)
            
            if result['success']:
                logger.info(f"✅ Sent calendar reminder to {user_id}: {event['title']}")
            else:
                logger.warning(f"⚠️ Failed to send reminder: {result.get('message')}")
            
        except Exception as e:
            logger.error(f"❌ Error sending event reminder: {e}")


# Global instance
_reminder_service = None

def get_reminder_service() -> CalendarReminderService:
    """Get or create calendar reminder service instance"""
    global _reminder_service
    if _reminder_service is None:
        _reminder_service = CalendarReminderService()
    return _reminder_service

async def start_reminder_service():
    """Start the calendar reminder service"""
    service = get_reminder_service()
    await service.start()

async def stop_reminder_service():
    """Stop the calendar reminder service"""
    service = get_reminder_service()
    await service.stop()

