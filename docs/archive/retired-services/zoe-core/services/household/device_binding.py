"""
Device Binding Manager
======================

Manages device-to-user bindings for personalized experiences.
"""

import os
import asyncio
import logging
import json
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

logger = logging.getLogger(__name__)


@dataclass
class Device:
    """A registered device."""
    id: str
    name: str
    type: str  # speaker, display, computer, phone
    household_id: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    room: Optional[str] = None
    capabilities: List[str] = None
    ip_address: Optional[str] = None
    is_online: bool = False
    last_seen: Optional[datetime] = None
    
    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ["audio"]


@dataclass
class DeviceBinding:
    """A binding between a device and user."""
    device_id: str
    user_id: str
    binding_type: str  # primary, shared, temporary
    priority: int = 1
    expires_at: Optional[datetime] = None


class DeviceBindingManager:
    """
    Manages device-to-user bindings.
    
    Devices can be bound to users in different ways:
    - Primary: User's personal device (their phone, laptop)
    - Shared: Shared household device (living room speaker)
    - Temporary: Voice-activated binding (who last spoke)
    
    Usage:
        manager = DeviceBindingManager(db_path)
        await manager.init()
        
        # Register device
        device = await manager.register_device("Living Room Speaker", "speaker")
        
        # Bind to user
        await manager.bind_device(device.id, user_id, "shared")
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize device binding manager."""
        self.db_path = db_path or os.getenv("ZOE_DB_PATH", "/app/data/zoe.db")
        self._conn = None
        self._initialized = False
    
    async def init(self) -> None:
        """Initialize database connection."""
        if self._initialized:
            return
        
        import aiosqlite
        
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        
        self._initialized = True
        logger.info("DeviceBindingManager initialized")
    
    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            self._initialized = False
    
    # ========================================
    # Device Management
    # ========================================
    
    async def register_device(
        self,
        name: str,
        device_type: str,
        household_id: Optional[str] = None,
        room: Optional[str] = None,
        manufacturer: Optional[str] = None,
        model: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        ip_address: Optional[str] = None,
        device_id: Optional[str] = None
    ) -> Device:
        """
        Register a new device.
        
        Args:
            name: Human-readable device name
            device_type: Type (speaker, display, computer, phone)
            household_id: Associated household
            room: Room location
            manufacturer: Device manufacturer
            model: Device model
            capabilities: List of capabilities (audio, video, voice)
            ip_address: Network address
            device_id: Optional custom device ID
        
        Returns:
            Registered Device object
        """
        device_id = device_id or str(uuid.uuid4())
        capabilities = capabilities or ["audio"]
        
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO devices 
            (id, household_id, name, type, manufacturer, model, room, 
             capabilities, ip_address, is_online, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                device_id, household_id, name, device_type, manufacturer,
                model, room, json.dumps(capabilities), ip_address, True
            )
        )
        await self._conn.commit()
        
        logger.info(f"Registered device: {name} ({device_id})")
        
        return Device(
            id=device_id,
            name=name,
            type=device_type,
            household_id=household_id,
            manufacturer=manufacturer,
            model=model,
            room=room,
            capabilities=capabilities,
            ip_address=ip_address,
            is_online=True
        )
    
    async def get_device(self, device_id: str) -> Optional[Device]:
        """Get a device by ID."""
        cursor = await self._conn.execute(
            "SELECT * FROM devices WHERE id = ?",
            (device_id,)
        )
        row = await cursor.fetchone()
        
        if not row:
            return None
        
        return self._row_to_device(row)
    
    async def get_household_devices(self, household_id: str) -> List[Device]:
        """Get all devices in a household."""
        cursor = await self._conn.execute(
            "SELECT * FROM devices WHERE household_id = ?",
            (household_id,)
        )
        rows = await cursor.fetchall()
        
        return [self._row_to_device(row) for row in rows]
    
    async def update_device(
        self,
        device_id: str,
        name: Optional[str] = None,
        room: Optional[str] = None,
        is_online: Optional[bool] = None,
        ip_address: Optional[str] = None
    ) -> bool:
        """Update device details."""
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if room is not None:
            updates.append("room = ?")
            params.append(room)
        
        if is_online is not None:
            updates.append("is_online = ?")
            params.append(is_online)
            if is_online:
                updates.append("last_seen = datetime('now')")
        
        if ip_address is not None:
            updates.append("ip_address = ?")
            params.append(ip_address)
        
        if not updates:
            return False
        
        params.append(device_id)
        
        await self._conn.execute(
            f"UPDATE devices SET {', '.join(updates)} WHERE id = ?",
            params
        )
        await self._conn.commit()
        
        return True
    
    async def delete_device(self, device_id: str) -> bool:
        """Delete a device."""
        await self._conn.execute(
            "DELETE FROM devices WHERE id = ?",
            (device_id,)
        )
        await self._conn.commit()
        
        logger.info(f"Deleted device: {device_id}")
        return True
    
    def _row_to_device(self, row) -> Device:
        """Convert database row to Device object."""
        return Device(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            household_id=row["household_id"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            room=row["room"],
            capabilities=json.loads(row["capabilities"] or '["audio"]'),
            ip_address=row["ip_address"],
            is_online=bool(row["is_online"]),
            last_seen=row["last_seen"]
        )
    
    # ========================================
    # Binding Management
    # ========================================
    
    async def bind_device(
        self,
        device_id: str,
        user_id: str,
        binding_type: str = "primary",
        priority: int = 1,
        duration_minutes: Optional[int] = None
    ) -> DeviceBinding:
        """
        Bind a device to a user.
        
        Args:
            device_id: Device to bind
            user_id: User to bind to
            binding_type: primary, shared, or temporary
            priority: Priority level (lower = higher priority)
            duration_minutes: For temporary bindings, how long until expiry
        
        Returns:
            DeviceBinding object
        """
        expires_at = None
        if duration_minutes:
            expires_at = datetime.now() + timedelta(minutes=duration_minutes)
        
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO device_user_bindings 
            (device_id, user_id, binding_type, priority, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                device_id, user_id, binding_type, priority,
                expires_at.isoformat() if expires_at else None
            )
        )
        await self._conn.commit()
        
        logger.info(f"Bound device {device_id} to user {user_id} ({binding_type})")
        
        return DeviceBinding(
            device_id=device_id,
            user_id=user_id,
            binding_type=binding_type,
            priority=priority,
            expires_at=expires_at
        )
    
    async def unbind_device(self, device_id: str, user_id: str) -> bool:
        """Remove a device binding."""
        await self._conn.execute(
            "DELETE FROM device_user_bindings WHERE device_id = ? AND user_id = ?",
            (device_id, user_id)
        )
        await self._conn.commit()
        
        logger.info(f"Unbound device {device_id} from user {user_id}")
        return True
    
    async def get_device_bindings(self, device_id: str) -> List[DeviceBinding]:
        """Get all bindings for a device."""
        cursor = await self._conn.execute(
            """
            SELECT * FROM device_user_bindings 
            WHERE device_id = ?
            ORDER BY priority ASC
            """,
            (device_id,)
        )
        rows = await cursor.fetchall()
        
        return [self._row_to_binding(row) for row in rows]
    
    async def get_user_devices(self, user_id: str) -> List[Device]:
        """Get all devices bound to a user."""
        cursor = await self._conn.execute(
            """
            SELECT d.* FROM devices d
            JOIN device_user_bindings dub ON d.id = dub.device_id
            WHERE dub.user_id = ?
            ORDER BY dub.priority ASC
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()
        
        return [self._row_to_device(row) for row in rows]
    
    async def get_active_user_for_device(self, device_id: str) -> Optional[str]:
        """
        Get the currently active user for a device.
        
        Returns the highest-priority non-expired binding.
        """
        # Clean expired bindings first
        await self._cleanup_expired_bindings()
        
        cursor = await self._conn.execute(
            """
            SELECT user_id FROM device_user_bindings
            WHERE device_id = ?
            AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY priority ASC
            LIMIT 1
            """,
            (device_id,)
        )
        row = await cursor.fetchone()
        
        return row["user_id"] if row else None
    
    async def _cleanup_expired_bindings(self) -> None:
        """Remove expired temporary bindings."""
        await self._conn.execute(
            """
            DELETE FROM device_user_bindings
            WHERE expires_at IS NOT NULL AND expires_at < datetime('now')
            """
        )
        await self._conn.commit()
    
    def _row_to_binding(self, row) -> DeviceBinding:
        """Convert database row to DeviceBinding object."""
        return DeviceBinding(
            device_id=row["device_id"],
            user_id=row["user_id"],
            binding_type=row["binding_type"],
            priority=row["priority"],
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
        )
    
    # ========================================
    # Voice-Activated Binding
    # ========================================
    
    async def set_voice_active_user(
        self,
        device_id: str,
        user_id: str,
        duration_minutes: int = 30
    ) -> DeviceBinding:
        """
        Set the active user based on voice identification.
        
        Creates a temporary binding that expires after inactivity.
        
        Args:
            device_id: Device where voice was detected
            user_id: Identified user
            duration_minutes: How long binding should last
        
        Returns:
            Temporary DeviceBinding
        """
        # Remove any existing temporary bindings for this device
        await self._conn.execute(
            """
            DELETE FROM device_user_bindings
            WHERE device_id = ? AND binding_type = 'temporary'
            """,
            (device_id,)
        )
        
        # Create new temporary binding with high priority
        return await self.bind_device(
            device_id=device_id,
            user_id=user_id,
            binding_type="temporary",
            priority=0,  # Highest priority
            duration_minutes=duration_minutes
        )
    
    async def extend_voice_binding(
        self,
        device_id: str,
        user_id: str,
        additional_minutes: int = 10
    ) -> bool:
        """Extend an existing voice-activated binding."""
        cursor = await self._conn.execute(
            """
            SELECT expires_at FROM device_user_bindings
            WHERE device_id = ? AND user_id = ? AND binding_type = 'temporary'
            """,
            (device_id, user_id)
        )
        row = await cursor.fetchone()
        
        if not row:
            return False
        
        current_expiry = datetime.fromisoformat(row["expires_at"])
        new_expiry = max(current_expiry, datetime.now()) + timedelta(minutes=additional_minutes)
        
        await self._conn.execute(
            """
            UPDATE device_user_bindings SET expires_at = ?
            WHERE device_id = ? AND user_id = ? AND binding_type = 'temporary'
            """,
            (new_expiry.isoformat(), device_id, user_id)
        )
        await self._conn.commit()
        
        return True


# Singleton instance
_device_binding_manager: Optional[DeviceBindingManager] = None


async def get_device_binding_manager() -> DeviceBindingManager:
    """Get the singleton device binding manager instance."""
    global _device_binding_manager
    if _device_binding_manager is None:
        _device_binding_manager = DeviceBindingManager()
        await _device_binding_manager.init()
    return _device_binding_manager

