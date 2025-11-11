"""
Push Notification Service
Handles sending web push notifications to subscribed users
"""

import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
import aiosqlite
from pywebpush import webpush, WebPushException
from py_vapid import Vapid
import os
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

from models.push_subscription import NotificationPayload

logger = logging.getLogger(__name__)


class PushNotificationService:
    """Service for sending web push notifications"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.getenv("DATABASE_PATH", os.getenv("DATABASE_PATH", str(Path(__file__).parent.parent.parent.resolve() / "data" / "zoe.db")))
        self.db_path = db_path
        self.vapid_private_key_path = None
        self.vapid_public_key = None
        self.vapid_claims = None
        self._load_vapid_keys()
    
    def _load_vapid_keys(self):
        """Load or generate VAPID keys for authentication"""
        private_key_path = str(PROJECT_ROOT / "config/vapid_private.pem")
        public_key_path = str(PROJECT_ROOT / "config/vapid_public.pem")
        
        try:
            # Try to load existing keys
            if os.path.exists(private_key_path) and os.path.exists(public_key_path):
                with open(private_key_path, 'rb') as f:
                    private_pem = f.read()
                with open(public_key_path, 'r') as f:
                    self.vapid_public_key = f.read().strip()
                
                # Store the private key path (pywebpush can load it directly)
                self.vapid_private_key_path = private_key_path
                
                logger.info("✅ VAPID keys loaded successfully")
            else:
                # Keys don't exist - user needs to generate them
                error_msg = (
                    "VAPID keys not found! Please generate them:\n"
                    f"  python3 {PROJECT_ROOT}/scripts/utilities/generate-vapid-keys.py"
                )
                logger.error(f"❌ {error_msg}")
                raise FileNotFoundError(error_msg)
            
            # Set VAPID claims
            self.vapid_claims = {
                "sub": "mailto:zoe@localhost"  # TODO: Make this configurable
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to load/generate VAPID keys: {e}")
            raise
    
    def get_public_key(self) -> str:
        """Get the public VAPID key for client subscription"""
        return self.vapid_public_key
    
    async def save_subscription(
        self,
        user_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: Optional[str] = None,
        device_type: Optional[str] = "unknown"
    ) -> int:
        """Save a push subscription to database"""
        async with aiosqlite.connect(self.db_path) as db:
            # Check if subscription already exists
            cursor = await db.execute(
                "SELECT id, active FROM push_subscriptions WHERE endpoint = ?",
                (endpoint,)
            )
            existing = await cursor.fetchone()
            
            if existing:
                # Reactivate if it was deactivated
                subscription_id = existing[0]
                await db.execute(
                    """UPDATE push_subscriptions 
                       SET active = 1, last_used = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (subscription_id,)
                )
                await db.commit()
                logger.info(f"✅ Reactivated subscription {subscription_id} for user {user_id}")
                return subscription_id
            
            # Insert new subscription
            cursor = await db.execute(
                """INSERT INTO push_subscriptions 
                   (user_id, endpoint, keys_p256dh, keys_auth, user_agent, device_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, endpoint, p256dh, auth, user_agent, device_type)
            )
            await db.commit()
            subscription_id = cursor.lastrowid
            logger.info(f"✅ Saved new subscription {subscription_id} for user {user_id}")
            return subscription_id
    
    async def remove_subscription(self, endpoint: str) -> bool:
        """Remove (deactivate) a push subscription"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE push_subscriptions SET active = 0 WHERE endpoint = ?",
                (endpoint,)
            )
            await db.commit()
            logger.info(f"✅ Deactivated subscription: {endpoint[:50]}...")
            return True
    
    async def get_user_subscriptions(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all active subscriptions for a user"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, endpoint, keys_p256dh, keys_auth, device_type
                   FROM push_subscriptions
                   WHERE user_id = ? AND active = 1""",
                (user_id,)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def send_notification(
        self,
        user_id: str,
        payload: NotificationPayload
    ) -> Dict[str, Any]:
        """Send notification to all user's subscriptions"""
        subscriptions = await self.get_user_subscriptions(user_id)
        
        if not subscriptions:
            logger.warning(f"⚠️ No subscriptions found for user {user_id}")
            return {"success": False, "message": "No subscriptions found", "sent": 0}
        
        # Check quiet hours
        if await self._is_quiet_hours(user_id):
            logger.info(f"🔕 Quiet hours active for {user_id}, skipping notification")
            return {"success": False, "message": "Quiet hours active", "sent": 0}
        
        # Prepare notification data
        notification_data = payload.model_dump(exclude_none=True)
        payload_json = json.dumps(notification_data)
        
        sent_count = 0
        failed_count = 0
        errors = []
        
        for sub in subscriptions:
            try:
                # Send push notification using private key path
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {
                            "p256dh": sub["keys_p256dh"],
                            "auth": sub["keys_auth"]
                        }
                    },
                    data=payload_json,
                    vapid_private_key=self.vapid_private_key_path,
                    vapid_claims=self.vapid_claims,
                    ttl=86400  # 24 hours
                )
                sent_count += 1
                
                # Update last_used timestamp
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(
                        "UPDATE push_subscriptions SET last_used = CURRENT_TIMESTAMP WHERE id = ?",
                        (sub["id"],)
                    )
                    await db.commit()
                
                # Log successful send
                await self._log_notification(
                    user_id, sub["id"], payload, success=True
                )
                
                logger.info(f"✅ Sent notification to {sub['device_type']} for user {user_id}")
                
            except WebPushException as e:
                failed_count += 1
                error_msg = str(e)
                errors.append(error_msg)
                logger.error(f"❌ WebPush failed for subscription {sub['id']}: {error_msg}")
                
                # If subscription expired (410 Gone), deactivate it
                if hasattr(e, 'response') and e.response and e.response.status_code == 410:
                    await self.remove_subscription(sub["endpoint"])
                    logger.info(f"🗑️ Removed expired subscription {sub['id']}")
                
                # Log failed send
                await self._log_notification(
                    user_id, sub["id"], payload, success=False, error=error_msg
                )
                
            except Exception as e:
                failed_count += 1
                error_msg = str(e)
                errors.append(error_msg)
                logger.error(f"❌ Unexpected error sending notification: {error_msg}")
                await self._log_notification(
                    user_id, sub["id"], payload, success=False, error=error_msg
                )
        
        return {
            "success": sent_count > 0,
            "sent": sent_count,
            "failed": failed_count,
            "errors": errors if errors else None
        }
    
    async def _is_quiet_hours(self, user_id: str) -> bool:
        """Check if user has quiet hours enabled and if current time is within them"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """SELECT quiet_hours_enabled, quiet_hours_start, quiet_hours_end
                   FROM notification_preferences WHERE user_id = ?""",
                (user_id,)
            )
            prefs = await cursor.fetchone()
            
            if not prefs or not prefs[0]:  # quiet_hours_enabled
                return False
            
            # TODO: Implement time range check
            # For now, just return False
            return False
    
    async def _log_notification(
        self,
        user_id: str,
        subscription_id: int,
        payload: NotificationPayload,
        success: bool,
        error: Optional[str] = None
    ):
        """Log notification send attempt"""
        async with aiosqlite.connect(self.db_path) as db:
            # Extract notification type from URL or default to 'general'
            notification_type = 'general'
            if 'calendar' in payload.url:
                notification_type = 'calendar'
            elif 'lists' in payload.url:
                notification_type = 'task'
            elif 'chat' in payload.url:
                notification_type = 'chat'
            
            await db.execute(
                """INSERT INTO notification_log
                   (user_id, subscription_id, notification_type, title, body, 
                    data, success, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, subscription_id, notification_type,
                    payload.title, payload.body,
                    json.dumps(payload.data) if payload.data else None,
                    success, error
                )
            )
            await db.commit()
    
    async def get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        """Get user notification preferences"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM notification_preferences WHERE user_id = ?",
                (user_id,)
            )
            row = await cursor.fetchone()
            
            if row:
                return dict(row)
            else:
                # Return defaults
                return {
                    "calendar_reminders": True,
                    "calendar_reminder_minutes": 15,
                    "task_due_alerts": True,
                    "task_due_hours": 24,
                    "shopping_updates": True,
                    "chat_messages": True,
                    "home_assistant_alerts": False,
                    "journal_prompts": False,
                    "journal_prompt_time": "20:00",
                    "birthday_reminders": True,
                    "quiet_hours_enabled": False,
                    "quiet_hours_start": "22:00",
                    "quiet_hours_end": "08:00"
                }
    
    async def update_preferences(
        self,
        user_id: str,
        preferences: Dict[str, Any]
    ) -> bool:
        """Update user notification preferences"""
        async with aiosqlite.connect(self.db_path) as db:
            # Check if preferences exist
            cursor = await db.execute(
                "SELECT user_id FROM notification_preferences WHERE user_id = ?",
                (user_id,)
            )
            exists = await cursor.fetchone()
            
            if exists:
                # Update existing
                set_clause = ", ".join([f"{k} = ?" for k in preferences.keys()])
                set_clause += ", updated_at = CURRENT_TIMESTAMP"
                values = list(preferences.values()) + [user_id]
                
                await db.execute(
                    f"UPDATE notification_preferences SET {set_clause} WHERE user_id = ?",
                    values
                )
            else:
                # Insert new
                columns = ["user_id"] + list(preferences.keys())
                placeholders = ", ".join(["?"] * len(columns))
                values = [user_id] + list(preferences.values())
                
                await db.execute(
                    f"INSERT INTO notification_preferences ({', '.join(columns)}) VALUES ({placeholders})",
                    values
                )
            
            await db.commit()
            logger.info(f"✅ Updated preferences for user {user_id}")
            return True


# Singleton instance
_push_service = None

def get_push_service() -> PushNotificationService:
    """Get or create push notification service instance"""
    global _push_service
    if _push_service is None:
        _push_service = PushNotificationService()
    return _push_service

