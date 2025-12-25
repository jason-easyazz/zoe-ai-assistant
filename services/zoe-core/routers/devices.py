"""
Device Registry API
====================

Unified device management for Zoe AI Assistant.
Enables per-device settings, notifications, and presence tracking.

Endpoints:
- POST /api/devices/register - Register/update a device
- GET /api/devices - List user's devices
- GET /api/devices/{device_id} - Get device details
- PATCH /api/devices/{device_id} - Update device settings
- DELETE /api/devices/{device_id} - Unregister device
- POST /api/devices/{device_id}/heartbeat - Update online status
- GET /api/devices/{device_id}/notifications - Get pending notifications
"""

import logging
import sqlite3
import os
import json
import secrets
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Request, Header, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
import asyncio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


# ============================================================
# Pydantic Models
# ============================================================

class DeviceCapabilities(BaseModel):
    has_display: bool = True
    has_audio: bool = False
    has_microphone: bool = False
    has_camera: bool = False
    screen_size: Optional[str] = None  # small, medium, large, xlarge


class DeviceRegisterRequest(BaseModel):
    device_id: Optional[str] = None  # Auto-generated if not provided
    name: str
    device_type: str  # touch_panel, browser, mobile, speaker, tablet
    room: Optional[str] = None
    capabilities: Optional[DeviceCapabilities] = None
    push_token: Optional[str] = None
    push_provider: Optional[str] = None
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    app_version: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = "en-US"


class DeviceUpdateRequest(BaseModel):
    name: Optional[str] = None
    room: Optional[str] = None
    capabilities: Optional[DeviceCapabilities] = None
    is_primary_alert_device: Optional[bool] = None
    notification_volume: Optional[int] = None
    alert_sound: Optional[str] = None
    do_not_disturb: Optional[bool] = None
    dnd_schedule: Optional[Dict[str, Any]] = None
    push_token: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None


class DeviceResponse(BaseModel):
    id: str
    user_id: str
    name: str
    device_type: str
    room: Optional[str]
    is_online: bool
    last_seen_at: Optional[str]
    is_primary_alert_device: bool
    capabilities: DeviceCapabilities
    created_at: str


class NotificationResponse(BaseModel):
    id: int
    notification_type: str
    title: Optional[str]
    message: str
    priority: str
    payload: Optional[Dict[str, Any]]
    created_at: str


# ============================================================
# Database Helpers
# ============================================================

def get_connection():
    """Get database connection."""
    return sqlite3.connect(DB_PATH)


def init_devices_db():
    """Initialize devices tables."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Read and execute schema
    schema_path = "/app/db/schema/devices.sql"
    if os.path.exists(schema_path):
        with open(schema_path, 'r') as f:
            schema = f.read()
            cursor.executescript(schema)
    else:
        # Inline schema if file not found
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                room TEXT,
                has_display BOOLEAN DEFAULT TRUE,
                has_audio BOOLEAN DEFAULT FALSE,
                has_microphone BOOLEAN DEFAULT FALSE,
                has_camera BOOLEAN DEFAULT FALSE,
                screen_size TEXT,
                is_online BOOLEAN DEFAULT FALSE,
                last_seen_at TIMESTAMP,
                ip_address TEXT,
                user_agent TEXT,
                push_token TEXT,
                push_provider TEXT,
                is_primary_alert_device BOOLEAN DEFAULT FALSE,
                notification_volume INTEGER DEFAULT 100,
                alert_sound TEXT DEFAULT 'default',
                do_not_disturb BOOLEAN DEFAULT FALSE,
                dnd_schedule TEXT,
                os_type TEXT,
                os_version TEXT,
                app_version TEXT,
                timezone TEXT,
                locale TEXT DEFAULT 'en-US',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS device_sessions (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_type TEXT NOT NULL,
                connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE,
                metadata TEXT
            );
            
            CREATE TABLE IF NOT EXISTS device_notification_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                title TEXT,
                message TEXT NOT NULL,
                priority TEXT DEFAULT 'normal',
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                delivered_at TIMESTAMP,
                read_at TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
            CREATE INDEX IF NOT EXISTS idx_devices_online ON devices(is_online);
        """)
    
    # Add source_device_id to timers if not exists
    try:
        cursor.execute("ALTER TABLE timers ADD COLUMN source_device_id TEXT")
        logger.info("Added source_device_id column to timers table")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    conn.commit()
    conn.close()
    logger.info("✅ Devices database initialized")


def get_user_from_request(request: Request) -> Optional[str]:
    """Extract user_id from request headers or session."""
    # Check X-Auth-Token header
    auth_token = request.headers.get("X-Auth-Token")
    if auth_token:
        # For now, map common tokens to users
        if auth_token == "test":
            return "developer"
        # Could validate against session store here
        return auth_token
    return None


# Initialize on module load
try:
    init_devices_db()
except Exception as e:
    logger.error(f"Failed to initialize devices DB: {e}")


# ============================================================
# API Endpoints
# ============================================================

@router.post("/register", response_model=DeviceResponse)
async def register_device(
    device: DeviceRegisterRequest,
    request: Request,
    x_device_id: Optional[str] = Header(None)
):
    """
    Register or update a device.
    
    Called by clients on startup to register themselves.
    If device_id exists, updates the device. Otherwise creates new.
    """
    user_id = get_user_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Use provided device_id or generate one
    device_id = device.device_id or x_device_id or secrets.token_hex(16)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check if device exists
        cursor.execute("SELECT id FROM devices WHERE id = ?", (device_id,))
        existing = cursor.fetchone()
        
        now = datetime.now().isoformat()
        
        # Extract capabilities
        caps = device.capabilities or DeviceCapabilities()
        
        # Get client info
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("User-Agent", "")
        
        if existing:
            # Update existing device
            cursor.execute("""
                UPDATE devices SET
                    name = ?,
                    device_type = ?,
                    room = ?,
                    has_display = ?,
                    has_audio = ?,
                    has_microphone = ?,
                    has_camera = ?,
                    screen_size = ?,
                    is_online = TRUE,
                    last_seen_at = ?,
                    ip_address = ?,
                    user_agent = ?,
                    push_token = COALESCE(?, push_token),
                    push_provider = COALESCE(?, push_provider),
                    os_type = COALESCE(?, os_type),
                    os_version = COALESCE(?, os_version),
                    app_version = COALESCE(?, app_version),
                    timezone = COALESCE(?, timezone),
                    locale = COALESCE(?, locale),
                    updated_at = ?
                WHERE id = ?
            """, (
                device.name, device.device_type, device.room,
                caps.has_display, caps.has_audio, caps.has_microphone, caps.has_camera,
                caps.screen_size, now, ip_address, user_agent,
                device.push_token, device.push_provider,
                device.os_type, device.os_version, device.app_version,
                device.timezone, device.locale, now, device_id
            ))
            logger.info(f"📱 Updated device: {device.name} ({device_id})")
        else:
            # Insert new device
            cursor.execute("""
                INSERT INTO devices (
                    id, user_id, name, device_type, room,
                    has_display, has_audio, has_microphone, has_camera, screen_size,
                    is_online, last_seen_at, ip_address, user_agent,
                    push_token, push_provider,
                    os_type, os_version, app_version, timezone, locale,
                    created_at, updated_at, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                device_id, user_id, device.name, device.device_type, device.room,
                caps.has_display, caps.has_audio, caps.has_microphone, caps.has_camera,
                caps.screen_size, now, ip_address, user_agent,
                device.push_token, device.push_provider,
                device.os_type, device.os_version, device.app_version,
                device.timezone, device.locale, now, now, now
            ))
            logger.info(f"📱 Registered new device: {device.name} ({device_id}) for user {user_id}")
        
        conn.commit()
        
        return DeviceResponse(
            id=device_id,
            user_id=user_id,
            name=device.name,
            device_type=device.device_type,
            room=device.room,
            is_online=True,
            last_seen_at=now,
            is_primary_alert_device=False,
            capabilities=caps,
            created_at=now
        )
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to register device: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get("/", response_model=List[DeviceResponse])
async def list_devices(request: Request):
    """List all devices for the current user."""
    user_id = get_user_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM devices WHERE user_id = ? ORDER BY name
        """, (user_id,))
        
        devices = []
        for row in cursor.fetchall():
            devices.append(DeviceResponse(
                id=row["id"],
                user_id=row["user_id"],
                name=row["name"],
                device_type=row["device_type"],
                room=row["room"],
                is_online=bool(row["is_online"]),
                last_seen_at=row["last_seen_at"],
                is_primary_alert_device=bool(row["is_primary_alert_device"]),
                capabilities=DeviceCapabilities(
                    has_display=bool(row["has_display"]),
                    has_audio=bool(row["has_audio"]),
                    has_microphone=bool(row["has_microphone"]),
                    has_camera=bool(row["has_camera"]) if row["has_camera"] else False,
                    screen_size=row["screen_size"]
                ),
                created_at=row["created_at"]
            ))
        
        return devices
        
    finally:
        conn.close()


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str, request: Request):
    """Get device details."""
    user_id = get_user_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM devices WHERE id = ? AND user_id = ?
        """, (device_id, user_id))
        
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Device not found")
        
        return DeviceResponse(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            device_type=row["device_type"],
            room=row["room"],
            is_online=bool(row["is_online"]),
            last_seen_at=row["last_seen_at"],
            is_primary_alert_device=bool(row["is_primary_alert_device"]),
            capabilities=DeviceCapabilities(
                has_display=bool(row["has_display"]),
                has_audio=bool(row["has_audio"]),
                has_microphone=bool(row["has_microphone"]),
                has_camera=bool(row["has_camera"]) if row["has_camera"] else False,
                screen_size=row["screen_size"]
            ),
            created_at=row["created_at"]
        )
        
    finally:
        conn.close()


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(device_id: str, update: DeviceUpdateRequest, request: Request):
    """Update device settings."""
    user_id = get_user_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Build update query dynamically
        updates = []
        params = []
        
        if update.name is not None:
            updates.append("name = ?")
            params.append(update.name)
        if update.room is not None:
            updates.append("room = ?")
            params.append(update.room)
        if update.is_primary_alert_device is not None:
            # If setting as primary, unset other primaries first
            if update.is_primary_alert_device:
                cursor.execute("""
                    UPDATE devices SET is_primary_alert_device = FALSE
                    WHERE user_id = ? AND id != ?
                """, (user_id, device_id))
            updates.append("is_primary_alert_device = ?")
            params.append(update.is_primary_alert_device)
        if update.notification_volume is not None:
            updates.append("notification_volume = ?")
            params.append(max(0, min(100, update.notification_volume)))
        if update.alert_sound is not None:
            updates.append("alert_sound = ?")
            params.append(update.alert_sound)
        if update.do_not_disturb is not None:
            updates.append("do_not_disturb = ?")
            params.append(update.do_not_disturb)
        if update.dnd_schedule is not None:
            updates.append("dnd_schedule = ?")
            params.append(json.dumps(update.dnd_schedule))
        if update.push_token is not None:
            updates.append("push_token = ?")
            params.append(update.push_token)
        if update.timezone is not None:
            updates.append("timezone = ?")
            params.append(update.timezone)
        if update.locale is not None:
            updates.append("locale = ?")
            params.append(update.locale)
        if update.capabilities is not None:
            caps = update.capabilities
            updates.extend([
                "has_display = ?", "has_audio = ?", "has_microphone = ?",
                "has_camera = ?", "screen_size = ?"
            ])
            params.extend([
                caps.has_display, caps.has_audio, caps.has_microphone,
                caps.has_camera, caps.screen_size
            ])
        
        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")
        
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        
        params.extend([device_id, user_id])
        
        cursor.execute(f"""
            UPDATE devices SET {", ".join(updates)}
            WHERE id = ? AND user_id = ?
        """, params)
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Device not found")
        
        conn.commit()
        
        # Return updated device
        return await get_device(device_id, request)
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update device: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.delete("/{device_id}")
async def delete_device(device_id: str, request: Request):
    """Unregister a device."""
    user_id = get_user_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            DELETE FROM devices WHERE id = ? AND user_id = ?
        """, (device_id, user_id))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Device not found")
        
        conn.commit()
        logger.info(f"📱 Deleted device: {device_id}")
        
        return {"message": "Device unregistered", "device_id": device_id}
        
    finally:
        conn.close()


@router.post("/{device_id}/heartbeat")
async def device_heartbeat(device_id: str, request: Request):
    """
    Update device online status.
    
    Called periodically by clients to indicate they're still connected.
    """
    user_id = get_user_from_request(request)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        now = datetime.now().isoformat()
        ip_address = request.client.host if request.client else None
        
        cursor.execute("""
            UPDATE devices SET
                is_online = TRUE,
                last_seen_at = ?,
                ip_address = ?
            WHERE id = ?
        """, (now, ip_address, device_id))
        
        conn.commit()
        
        return {"status": "ok", "last_seen_at": now}
        
    finally:
        conn.close()


@router.get("/{device_id}/notifications", response_model=List[NotificationResponse])
async def get_device_notifications(
    device_id: str,
    request: Request,
    pending_only: bool = True
):
    """Get notifications for a device."""
    user_id = get_user_from_request(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        if pending_only:
            cursor.execute("""
                SELECT * FROM device_notification_queue
                WHERE device_id = ? AND user_id = ? AND delivered_at IS NULL
                ORDER BY created_at DESC
            """, (device_id, user_id))
        else:
            cursor.execute("""
                SELECT * FROM device_notification_queue
                WHERE device_id = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT 50
            """, (device_id, user_id))
        
        notifications = []
        for row in cursor.fetchall():
            payload = None
            if row["payload"]:
                try:
                    payload = json.loads(row["payload"])
                except:
                    pass
            
            notifications.append(NotificationResponse(
                id=row["id"],
                notification_type=row["notification_type"],
                title=row["title"],
                message=row["message"],
                priority=row["priority"],
                payload=payload,
                created_at=row["created_at"]
            ))
        
        return notifications
        
    finally:
        conn.close()


@router.post("/{device_id}/notifications/{notification_id}/delivered")
async def mark_notification_delivered(device_id: str, notification_id: int, request: Request):
    """Mark a notification as delivered."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            UPDATE device_notification_queue
            SET delivered_at = ?
            WHERE id = ? AND device_id = ?
        """, (datetime.now().isoformat(), notification_id, device_id))
        
        conn.commit()
        return {"status": "ok"}
        
    finally:
        conn.close()


# ============================================================
# Helper Functions for Other Modules
# ============================================================

async def queue_device_notification(
    device_id: str,
    user_id: str,
    notification_type: str,
    message: str,
    title: Optional[str] = None,
    priority: str = "normal",
    payload: Optional[Dict] = None,
    expires_in_seconds: Optional[int] = None
) -> int:
    """
    Queue a notification for a specific device.
    
    Returns: notification ID
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        expires_at = None
        if expires_in_seconds:
            expires_at = (datetime.now() + timedelta(seconds=expires_in_seconds)).isoformat()
        
        cursor.execute("""
            INSERT INTO device_notification_queue
            (device_id, user_id, notification_type, title, message, priority, payload, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            device_id, user_id, notification_type, title, message, priority,
            json.dumps(payload) if payload else None, expires_at
        ))
        
        notification_id = cursor.lastrowid
        conn.commit()
        
        logger.info(f"📬 Queued {notification_type} notification for device {device_id}")
        return notification_id
        
    finally:
        conn.close()


async def get_user_devices(user_id: str, online_only: bool = False) -> List[Dict]:
    """Get all devices for a user."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        if online_only:
            cursor.execute("""
                SELECT * FROM devices WHERE user_id = ? AND is_online = TRUE
            """, (user_id,))
        else:
            cursor.execute("""
                SELECT * FROM devices WHERE user_id = ?
            """, (user_id,))
        
        return [dict(row) for row in cursor.fetchall()]
        
    finally:
        conn.close()


async def get_primary_alert_device(user_id: str) -> Optional[Dict]:
    """Get user's primary alert device."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM devices
            WHERE user_id = ? AND is_primary_alert_device = TRUE
            LIMIT 1
        """, (user_id,))
        
        row = cursor.fetchone()
        return dict(row) if row else None
        
    finally:
        conn.close()


async def get_device_by_room(user_id: str, room: str) -> List[Dict]:
    """Get devices in a specific room."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT * FROM devices
            WHERE user_id = ? AND room = ?
        """, (user_id, room))
        
        return [dict(row) for row in cursor.fetchall()]
        
    finally:
        conn.close()


async def set_device_offline(device_id: str):
    """Mark a device as offline."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            UPDATE devices SET is_online = FALSE WHERE id = ?
        """, (device_id,))
        conn.commit()
    finally:
        conn.close()

