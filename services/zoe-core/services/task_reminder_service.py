"""
Task Reminder Service
Automatically sends push notifications for tasks that are due soon
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


class TaskReminderService:
    """Service for sending task due reminders"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.getenv("DATABASE_PATH", os.getenv("DATABASE_PATH", str(Path(__file__).parent.parent.parent.resolve() / "data" / "zoe.db")))
        self.db_path = db_path
        self.push_service = get_push_service()
        self.check_interval = 300  # Check every 5 minutes (less frequent than calendar)
        self._running = False
        self._task = None
    
    async def start(self):
        """Start the reminder service"""
        if self._running:
            logger.warning("Task reminder service already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._reminder_loop())
        logger.info("✅ Task reminder service started")
    
    async def stop(self):
        """Stop the reminder service"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Task reminder service stopped")
    
    async def _reminder_loop(self):
        """Main loop that checks for due tasks"""
        while self._running:
            try:
                await self._check_due_tasks()
            except Exception as e:
                logger.error(f"❌ Error in task reminder loop: {e}")
            
            # Wait before next check
            await asyncio.sleep(self.check_interval)
    
    async def _check_due_tasks(self):
        """Check for tasks that are due soon"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Get all users with task alerts enabled
            cursor = await db.execute("""
                SELECT DISTINCT u.user_id, np.task_due_hours
                FROM users u
                LEFT JOIN notification_preferences np ON u.user_id = np.user_id
                WHERE (np.task_due_alerts IS NULL OR np.task_due_alerts = 1)
            """)
            users = await cursor.fetchall()
            
            for user in users:
                user_id = user['user_id']
                due_hours = user['task_due_hours'] or 24
                
                await self._send_task_alerts_for_user(user_id, due_hours)
    
    async def _send_task_alerts_for_user(self, user_id: str, due_hours: int):
        """Send task alerts for a specific user"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Calculate due time window
            now = datetime.now()
            due_threshold = now + timedelta(hours=due_hours)
            
            # Get incomplete tasks that are due within the threshold
            # and haven't been reminded in the last 24 hours
            cursor = await db.execute("""
                SELECT 
                    li.id, li.item_text, li.due_date, li.due_time,
                    l.name as list_name, li.priority
                FROM list_items li
                JOIN lists l ON li.list_id = l.id
                LEFT JOIN notification_log nl ON (
                    nl.user_id = li.user_id AND
                    nl.notification_type = 'task' AND
                    nl.data LIKE '%"task_id":' || li.id || '%' AND
                    nl.sent_at > datetime('now', '-24 hours')
                )
                WHERE li.user_id = ?
                  AND li.completed = 0
                  AND li.due_date IS NOT NULL
                  AND datetime(li.due_date || ' ' || COALESCE(li.due_time, '23:59:59')) <= datetime(?)
                  AND datetime(li.due_date || ' ' || COALESCE(li.due_time, '23:59:59')) >= datetime('now')
                  AND nl.id IS NULL
                ORDER BY li.due_date, li.due_time
                LIMIT 10
            """, (
                user_id,
                due_threshold.strftime('%Y-%m-%d %H:%M:%S')
            ))
            
            tasks = await cursor.fetchall()
            
            for task in tasks:
                await self._send_task_reminder(user_id, dict(task), due_hours)
    
    async def _send_task_reminder(self, user_id: str, task: Dict[str, Any], hours_before: int):
        """Send push notification for a specific task"""
        try:
            # Calculate time until due
            due_datetime_str = f"{task['due_date']} {task.get('due_time') or '23:59:59'}"
            due_datetime = datetime.strptime(due_datetime_str, '%Y-%m-%d %H:%M:%S')
            now = datetime.now()
            time_until = due_datetime - now
            
            hours_until = time_until.total_seconds() / 3600
            
            # Format time remaining
            if hours_until < 1:
                time_str = f"{int(time_until.total_seconds() / 60)} minutes"
            elif hours_until < 24:
                time_str = f"{int(hours_until)} hours"
            else:
                days = int(hours_until / 24)
                time_str = f"{days} day{'s' if days > 1 else ''}"
            
            # Build notification
            priority_emoji = "🔴" if task.get('priority') == 'high' else "🟡" if task.get('priority') == 'medium' else "🟢"
            
            title = f"✅ Task Due Soon: {task['item_text']}"
            
            body_parts = [f"Due in {time_str}"]
            if task.get('list_name'):
                body_parts.append(f"List: {task['list_name']}")
            if task.get('priority'):
                body_parts.append(f"{priority_emoji} {task['priority'].title()} priority")
            
            body = " • ".join(body_parts)
            
            # Create notification payload
            payload = NotificationPayload(
                title=title,
                body=body,
                url=f"/lists.html?list={task.get('list_name', 'all')}&highlight={task['id']}",
                icon="/icons/icon-192.png",
                badge="/icons/icon-72.png",
                tag=f"task-{task['id']}",
                vibrate=[200, 100, 200],
                requireInteraction=True,
                data={
                    "task_id": task['id'],
                    "type": "task_reminder",
                    "due_date": task['due_date'],
                    "priority": task.get('priority')
                }
            )
            
            # Send notification
            result = await self.push_service.send_notification(user_id, payload)
            
            if result['success']:
                logger.info(f"✅ Sent task reminder to {user_id}: {task['item_text']}")
            else:
                logger.warning(f"⚠️ Failed to send task reminder: {result.get('message')}")
            
        except Exception as e:
            logger.error(f"❌ Error sending task reminder: {e}")


# Global instance
_task_reminder_service = None

def get_task_reminder_service() -> TaskReminderService:
    """Get or create task reminder service instance"""
    global _task_reminder_service
    if _task_reminder_service is None:
        _task_reminder_service = TaskReminderService()
    return _task_reminder_service

async def start_task_reminder_service():
    """Start the task reminder service"""
    service = get_task_reminder_service()
    await service.start()

async def stop_task_reminder_service():
    """Stop the task reminder service"""
    service = get_task_reminder_service()
    await service.stop()

