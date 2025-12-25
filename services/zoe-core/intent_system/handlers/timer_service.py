"""
Timer Background Service
========================

Monitors active timers and triggers alerts when they expire.
Runs as an async background task within the FastAPI application.
"""

import asyncio
import logging
import sqlite3
import os
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Global state for the timer service
_timer_task: Optional[asyncio.Task] = None
_callbacks: Dict[str, callable] = {}


def register_timer_callback(callback_id: str, callback: callable):
    """
    Register a callback to be called when a timer expires.
    
    Args:
        callback_id: Unique identifier for this callback
        callback: Async function(user_id, timer_data) to call
    """
    _callbacks[callback_id] = callback
    logger.info(f"Registered timer callback: {callback_id}")


def unregister_timer_callback(callback_id: str):
    """Remove a registered callback."""
    if callback_id in _callbacks:
        del _callbacks[callback_id]
        logger.info(f"Unregistered timer callback: {callback_id}")


async def check_expired_timers() -> List[Dict]:
    """
    Check for expired timers and mark them as completed.
    
    Returns:
        List of expired timer data dicts (includes source device info)
    """
    expired = []
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Find expired but not completed/cancelled timers
        # Include source device info for routing
        now_iso = datetime.now().isoformat()
        cursor.execute("""
            SELECT id, user_id, label, duration_seconds, created_at, expires_at,
                   source_device_id, source_session_id, source_room
            FROM timers
            WHERE completed = FALSE 
            AND cancelled = FALSE
            AND expires_at <= ?
        """, (now_iso,))
        
        rows = cursor.fetchall()
        
        for row in rows:
            timer_data = {
                "timer_id": row["id"],
                "user_id": row["user_id"],
                "label": row["label"],
                "duration_seconds": row["duration_seconds"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                # Source device info for routing
                "source_device_id": row["source_device_id"] if "source_device_id" in row.keys() else None,
                "source_session_id": row["source_session_id"] if "source_session_id" in row.keys() else None,
                "source_room": row["source_room"] if "source_room" in row.keys() else None
            }
            expired.append(timer_data)
            
            # Mark as completed
            cursor.execute("""
                UPDATE timers SET completed = TRUE WHERE id = ?
            """, (row["id"],))
        
        if expired:
            conn.commit()
            logger.info(f"⏰ Found {len(expired)} expired timer(s)")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Error checking expired timers: {e}", exc_info=True)
    
    return expired


async def trigger_timer_alerts(expired_timers: List[Dict]):
    """
    Trigger alerts for expired timers, routing to appropriate devices.
    
    Routing priority:
    1. Source device (where timer was set) - if online
    2. All user's devices in the same room
    3. User's primary alert device
    4. All user's online devices
    
    Args:
        expired_timers: List of expired timer data dicts
    """
    for timer in expired_timers:
        user_id = timer["user_id"]
        label = timer.get("label", "Timer")
        source_device_id = timer.get("source_device_id")
        source_room = timer.get("source_room")
        
        logger.info(f"🔔 Timer expired for user {user_id}: {label} (source: {source_device_id}, room: {source_room})")
        
        # Call all registered callbacks
        for callback_id, callback in _callbacks.items():
            try:
                await callback(user_id, timer)
            except Exception as e:
                logger.error(f"Timer callback {callback_id} failed: {e}")
        
        # Build notification payload
        notification = {
            "type": "timer_expired",
            "timer_id": timer["timer_id"],
            "label": label,
            "message": f"⏰ Timer complete: {label}!",
            "source_device_id": source_device_id,
            "source_room": source_room,
            "priority": "high"
        }
        
        # Route notification to appropriate device(s)
        await route_timer_notification(user_id, notification, source_device_id, source_room)


async def timer_check_loop(check_interval: float = 1.0):
    """
    Background loop that periodically checks for expired timers.
    
    Args:
        check_interval: How often to check (seconds)
    """
    logger.info(f"⏱️ Timer service started (checking every {check_interval}s)")
    
    while True:
        try:
            expired = await check_expired_timers()
            
            if expired:
                await trigger_timer_alerts(expired)
                
        except asyncio.CancelledError:
            logger.info("⏱️ Timer service stopped")
            break
        except Exception as e:
            logger.error(f"Timer check loop error: {e}", exc_info=True)
        
        await asyncio.sleep(check_interval)


def start_timer_service(check_interval: float = 1.0):
    """
    Start the timer background service.
    
    Args:
        check_interval: How often to check for expired timers (seconds)
    """
    global _timer_task
    
    if _timer_task is not None and not _timer_task.done():
        logger.warning("Timer service already running")
        return
    
    _timer_task = asyncio.create_task(timer_check_loop(check_interval))
    logger.info("⏱️ Timer service task created")


def stop_timer_service():
    """Stop the timer background service."""
    global _timer_task
    
    if _timer_task is not None and not _timer_task.done():
        _timer_task.cancel()
        logger.info("⏱️ Timer service cancelled")
    
    _timer_task = None


async def get_active_timers(user_id: str) -> List[Dict]:
    """
    Get all active timers for a user.
    
    Args:
        user_id: User identifier
        
    Returns:
        List of active timer data dicts
    """
    timers = []
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, label, duration_seconds, created_at, expires_at
            FROM timers
            WHERE user_id = ?
            AND completed = FALSE
            AND cancelled = FALSE
            AND expires_at > datetime('now')
            ORDER BY expires_at ASC
        """, (user_id,))
        
        for row in cursor.fetchall():
            expires_at = datetime.fromisoformat(row["expires_at"])
            remaining = (expires_at - datetime.now()).total_seconds()
            
            timers.append({
                "timer_id": row["id"],
                "label": row["label"],
                "duration_seconds": row["duration_seconds"],
                "expires_at": row["expires_at"],
                "remaining_seconds": max(0, int(remaining))
            })
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Error getting active timers: {e}", exc_info=True)
    
    return timers


async def route_timer_notification(
    user_id: str,
    notification: Dict,
    source_device_id: Optional[str],
    source_room: Optional[str]
):
    """
    Route timer notification to appropriate device(s).
    
    Routing priority:
    1. Source device (where timer was set) - if online
    2. All devices in the same room - if source has room
    3. User's primary alert device
    4. Broadcast to all user's online devices
    
    Args:
        user_id: User to notify
        notification: Notification payload
        source_device_id: Device that set the timer
        source_room: Room where timer was set
    """
    notified_devices = set()
    
    try:
        # Import device helpers
        from routers.devices import get_user_devices, get_primary_alert_device, get_device_by_room
        from routers.devices import queue_device_notification
        
        # Try WebSocket first for real-time delivery
        try:
            from routers.websocket import send_to_device, broadcast_to_user
            
            # 1. Try source device first
            if source_device_id:
                success = await send_to_device(source_device_id, notification)
                if success:
                    notified_devices.add(source_device_id)
                    logger.info(f"📱 Sent timer alert to source device {source_device_id}")
            
            # 2. Send to other devices in same room
            if source_room:
                room_devices = await get_device_by_room(user_id, source_room)
                for device in room_devices:
                    device_id = device["id"]
                    if device_id not in notified_devices and device.get("is_online"):
                        success = await send_to_device(device_id, notification)
                        if success:
                            notified_devices.add(device_id)
                            logger.info(f"📱 Sent timer alert to room device {device_id}")
            
            # 3. If no notifications sent yet, try primary device
            if not notified_devices:
                primary = await get_primary_alert_device(user_id)
                if primary and primary.get("is_online"):
                    success = await send_to_device(primary["id"], notification)
                    if success:
                        notified_devices.add(primary["id"])
                        logger.info(f"📱 Sent timer alert to primary device {primary['id']}")
            
            # 4. Fallback: broadcast to all user devices
            if not notified_devices:
                await broadcast_to_user(user_id, notification)
                logger.info(f"📢 Broadcast timer alert to all devices for user {user_id}")
        
        except ImportError as e:
            logger.debug(f"WebSocket module not available: {e}")
        
        # Queue notification for offline delivery
        if source_device_id and source_device_id not in notified_devices:
            await queue_device_notification(
                device_id=source_device_id,
                user_id=user_id,
                notification_type="timer",
                message=notification["message"],
                title="Timer Complete",
                priority="high",
                payload=notification,
                expires_in_seconds=3600  # 1 hour
            )
            logger.info(f"📬 Queued timer notification for offline device {source_device_id}")
    
    except Exception as e:
        logger.error(f"Error routing timer notification: {e}", exc_info=True)
        
        # Last resort: try basic broadcast
        try:
            from routers.websocket import broadcast_to_user
            await broadcast_to_user(user_id, notification)
        except Exception:
            pass


# Default callback that logs timer expirations
async def default_timer_callback(user_id: str, timer: Dict):
    """Default callback that logs timer expirations."""
    label = timer.get("label", "Timer")
    source = timer.get("source_device_id", "unknown")
    room = timer.get("source_room", "unknown")
    logger.info(f"🔔 TIMER ALERT for {user_id}: {label} has finished! (device: {source}, room: {room})")


# Register default callback
register_timer_callback("default_logger", default_timer_callback)

