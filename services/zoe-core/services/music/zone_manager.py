"""
Music Zone Manager
Handles multi-zone playback with device routing and state synchronization
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict
import uuid

logger = logging.getLogger(__name__)


@dataclass
class ZoneState:
    """Current playback state for a zone"""
    zone_id: str
    current_track_id: Optional[str] = None
    track_info: Optional[Dict] = None
    position_ms: int = 0
    is_playing: bool = False
    volume: int = 80
    shuffle: bool = False
    repeat_mode: str = 'off'  # off, one, all
    queue: List[Dict] = field(default_factory=list)
    queue_index: int = 0


@dataclass
class ZoneDevice:
    """Device in a zone"""
    device_id: str
    device_type: str  # browser, chromecast, airplay
    device_name: str
    role: str = 'player'  # player, controller, both
    is_connected: bool = False
    supports_video: bool = False


@dataclass 
class Zone:
    """Music zone configuration"""
    id: str
    name: str
    user_id: str
    room_id: Optional[str] = None
    icon: str = '🎵'
    color: Optional[str] = None
    is_default: bool = False
    devices: List[ZoneDevice] = field(default_factory=list)
    state: Optional[ZoneState] = None


class ZoneManager:
    """
    Manages music zones for multi-device playback
    
    Features:
    - Create/delete zones
    - Assign devices to zones
    - Broadcast state changes via WebSocket
    - Handle play/pause/skip commands per zone
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path
        self.zones: Dict[str, Zone] = {}
        self.zone_states: Dict[str, ZoneState] = {}
        self.connected_devices: Dict[str, Set[str]] = {}  # zone_id -> set of device_ids
        self.device_websockets: Dict[str, Any] = {}  # device_id -> websocket
        self._lock = asyncio.Lock()
    
    async def init(self, db_path: str = None):
        """Initialize the zone manager and load existing zones"""
        if db_path:
            self.db_path = db_path
        
        await self._load_zones_from_db()
        logger.info(f"ZoneManager initialized with {len(self.zones)} zones")
    
    async def _load_zones_from_db(self):
        """Load zones from database"""
        if not self.db_path:
            return
            
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Load zones
                async with db.execute(
                    "SELECT id, name, user_id, room_id, icon, color, is_default FROM music_zones"
                ) as cursor:
                    async for row in cursor:
                        zone = Zone(
                            id=row[0],
                            name=row[1],
                            user_id=row[2],
                            room_id=row[3],
                            icon=row[4] or '🎵',
                            color=row[5],
                            is_default=bool(row[6])
                        )
                        self.zones[zone.id] = zone
                
                # Load zone states
                async with db.execute(
                    """SELECT zone_id, current_track_id, track_info, position_ms, 
                       is_playing, volume, shuffle, repeat_mode, queue, queue_index
                       FROM zone_playback_state"""
                ) as cursor:
                    async for row in cursor:
                        state = ZoneState(
                            zone_id=row[0],
                            current_track_id=row[1],
                            track_info=json.loads(row[2]) if row[2] else None,
                            position_ms=row[3] or 0,
                            is_playing=bool(row[4]),
                            volume=row[5] or 80,
                            shuffle=bool(row[6]),
                            repeat_mode=row[7] or 'off',
                            queue=json.loads(row[8]) if row[8] else [],
                            queue_index=row[9] or 0
                        )
                        self.zone_states[state.zone_id] = state
                        if state.zone_id in self.zones:
                            self.zones[state.zone_id].state = state
                
                # Load zone devices
                async with db.execute(
                    """SELECT zone_id, device_id, device_type, device_name, 
                       role, is_connected, supports_video
                       FROM zone_devices WHERE is_active = 1"""
                ) as cursor:
                    async for row in cursor:
                        device = ZoneDevice(
                            device_id=row[1],
                            device_type=row[2],
                            device_name=row[3] or row[1],
                            role=row[4] or 'player',
                            is_connected=bool(row[5]),
                            supports_video=bool(row[6])
                        )
                        zone_id = row[0]
                        if zone_id in self.zones:
                            self.zones[zone_id].devices.append(device)
                            
        except Exception as e:
            logger.error(f"Failed to load zones from database: {e}")
    
    # ==================== Zone CRUD ====================
    
    async def create_zone(
        self, 
        user_id: str, 
        name: str, 
        room_id: Optional[str] = None,
        icon: str = '🎵',
        is_default: bool = False
    ) -> Zone:
        """Create a new music zone"""
        zone_id = str(uuid.uuid4())[:8]
        
        zone = Zone(
            id=zone_id,
            name=name,
            user_id=user_id,
            room_id=room_id,
            icon=icon,
            is_default=is_default
        )
        
        # Create initial state
        state = ZoneState(zone_id=zone_id)
        zone.state = state
        
        async with self._lock:
            self.zones[zone_id] = zone
            self.zone_states[zone_id] = state
            self.connected_devices[zone_id] = set()
        
        # Persist to database
        await self._save_zone_to_db(zone)
        
        logger.info(f"Created zone '{name}' ({zone_id}) for user {user_id}")
        return zone
    
    async def delete_zone(self, zone_id: str, user_id: str) -> bool:
        """Delete a zone (only owner can delete)"""
        zone = self.zones.get(zone_id)
        if not zone or zone.user_id != user_id:
            return False
        
        async with self._lock:
            del self.zones[zone_id]
            if zone_id in self.zone_states:
                del self.zone_states[zone_id]
            if zone_id in self.connected_devices:
                del self.connected_devices[zone_id]
        
        # Remove from database
        await self._delete_zone_from_db(zone_id)
        
        logger.info(f"Deleted zone {zone_id}")
        return True
    
    async def get_zones_for_user(self, user_id: str) -> List[Zone]:
        """Get all zones accessible by a user"""
        return [z for z in self.zones.values() if z.user_id == user_id]
    
    async def get_zone(self, zone_id: str) -> Optional[Zone]:
        """Get a zone by ID"""
        return self.zones.get(zone_id)
    
    async def get_zone_state(self, zone_id: str) -> Optional[ZoneState]:
        """Get current playback state for a zone"""
        return self.zone_states.get(zone_id)
    
    # ==================== Device Management ====================
    
    async def add_device_to_zone(
        self,
        zone_id: str,
        device_id: str,
        device_type: str,
        device_name: str,
        role: str = 'player',
        supports_video: bool = False
    ) -> bool:
        """Add a device to a zone"""
        zone = self.zones.get(zone_id)
        if not zone:
            return False
        
        device = ZoneDevice(
            device_id=device_id,
            device_type=device_type,
            device_name=device_name,
            role=role,
            supports_video=supports_video
        )
        
        async with self._lock:
            # Remove from other zones first
            for z in self.zones.values():
                z.devices = [d for d in z.devices if d.device_id != device_id]
            
            zone.devices.append(device)
        
        await self._save_zone_device_to_db(zone_id, device)
        logger.info(f"Added device {device_id} to zone {zone_id}")
        return True
    
    async def remove_device_from_zone(self, zone_id: str, device_id: str) -> bool:
        """Remove a device from a zone"""
        zone = self.zones.get(zone_id)
        if not zone:
            return False
        
        async with self._lock:
            zone.devices = [d for d in zone.devices if d.device_id != device_id]
            if zone_id in self.connected_devices:
                self.connected_devices[zone_id].discard(device_id)
        
        await self._remove_zone_device_from_db(zone_id, device_id)
        return True
    
    async def device_connected(self, zone_id: str, device_id: str, websocket: Any):
        """Mark a device as connected and store its websocket"""
        async with self._lock:
            if zone_id not in self.connected_devices:
                self.connected_devices[zone_id] = set()
            self.connected_devices[zone_id].add(device_id)
            self.device_websockets[device_id] = websocket
        
        # Update device connection state
        zone = self.zones.get(zone_id)
        if zone:
            for device in zone.devices:
                if device.device_id == device_id:
                    device.is_connected = True
                    break
        
        # Send current zone state to the device
        state = self.zone_states.get(zone_id)
        if state and websocket:
            await self._send_to_device(device_id, {
                'type': 'zone_state',
                'zone_id': zone_id,
                'state': self._state_to_dict(state)
            })
    
    async def device_disconnected(self, device_id: str):
        """Mark a device as disconnected"""
        async with self._lock:
            if device_id in self.device_websockets:
                del self.device_websockets[device_id]
            
            for zone_id, devices in self.connected_devices.items():
                devices.discard(device_id)
        
        # Update device connection state in zones
        for zone in self.zones.values():
            for device in zone.devices:
                if device.device_id == device_id:
                    device.is_connected = False
    
    # ==================== Playback Control ====================
    
    async def play(
        self,
        zone_id: str,
        track_id: str,
        track_info: Dict,
        stream_url: Optional[str] = None
    ) -> bool:
        """Start playback in a zone"""
        state = self.zone_states.get(zone_id)
        if not state:
            return False
        
        state.current_track_id = track_id
        state.track_info = track_info
        state.position_ms = 0
        state.is_playing = True
        
        await self._save_zone_state_to_db(state)
        
        # Broadcast to all connected devices in zone
        await self._broadcast_to_zone(zone_id, {
            'type': 'media_play',
            'zone_id': zone_id,
            'track_id': track_id,
            'track_info': track_info,
            'url': stream_url
        })
        
        # Also send updated zone state
        await self._broadcast_zone_state(zone_id)
        
        return True
    
    async def pause(self, zone_id: str) -> bool:
        """Pause playback in a zone"""
        state = self.zone_states.get(zone_id)
        if not state:
            return False
        
        state.is_playing = False
        await self._save_zone_state_to_db(state)
        
        await self._broadcast_to_zone(zone_id, {
            'type': 'media_pause',
            'zone_id': zone_id
        })
        
        await self._broadcast_zone_state(zone_id)
        return True
    
    async def resume(self, zone_id: str) -> bool:
        """Resume playback in a zone"""
        state = self.zone_states.get(zone_id)
        if not state:
            return False
        
        state.is_playing = True
        await self._save_zone_state_to_db(state)
        
        await self._broadcast_to_zone(zone_id, {
            'type': 'media_resume',
            'zone_id': zone_id
        })
        
        await self._broadcast_zone_state(zone_id)
        return True
    
    async def seek(self, zone_id: str, position_ms: int) -> bool:
        """Seek to position in a zone"""
        state = self.zone_states.get(zone_id)
        if not state:
            return False
        
        state.position_ms = position_ms
        await self._save_zone_state_to_db(state)
        
        await self._broadcast_to_zone(zone_id, {
            'type': 'media_seek',
            'zone_id': zone_id,
            'position_ms': position_ms
        })
        
        return True
    
    async def set_volume(self, zone_id: str, volume: int) -> bool:
        """Set volume for a zone"""
        state = self.zone_states.get(zone_id)
        if not state:
            return False
        
        state.volume = max(0, min(100, volume))
        await self._save_zone_state_to_db(state)
        
        await self._broadcast_to_zone(zone_id, {
            'type': 'media_volume',
            'zone_id': zone_id,
            'volume': state.volume
        })
        
        return True
    
    async def skip(self, zone_id: str) -> Optional[Dict]:
        """Skip to next track in queue"""
        state = self.zone_states.get(zone_id)
        if not state or not state.queue:
            return None
        
        if state.queue_index + 1 < len(state.queue):
            state.queue_index += 1
            next_track = state.queue[state.queue_index]
            state.current_track_id = next_track.get('track_id')
            state.track_info = next_track
            state.position_ms = 0
            
            await self._save_zone_state_to_db(state)
            await self._broadcast_zone_state(zone_id)
            
            return next_track
        
        return None
    
    async def previous(self, zone_id: str) -> Optional[Dict]:
        """Go to previous track in queue"""
        state = self.zone_states.get(zone_id)
        if not state or not state.queue:
            return None
        
        if state.queue_index > 0:
            state.queue_index -= 1
            prev_track = state.queue[state.queue_index]
            state.current_track_id = prev_track.get('track_id')
            state.track_info = prev_track
            state.position_ms = 0
            
            await self._save_zone_state_to_db(state)
            await self._broadcast_zone_state(zone_id)
            
            return prev_track
        
        return None
    
    async def add_to_queue(self, zone_id: str, track: Dict) -> bool:
        """Add a track to the zone queue"""
        state = self.zone_states.get(zone_id)
        if not state:
            return False
        
        state.queue.append(track)
        await self._save_zone_state_to_db(state)
        await self._broadcast_zone_state(zone_id)
        return True
    
    async def clear_queue(self, zone_id: str) -> bool:
        """Clear the zone queue"""
        state = self.zone_states.get(zone_id)
        if not state:
            return False
        
        state.queue = []
        state.queue_index = 0
        await self._save_zone_state_to_db(state)
        await self._broadcast_zone_state(zone_id)
        return True
    
    # ==================== Broadcasting ====================
    
    async def _broadcast_to_zone(self, zone_id: str, message: Dict):
        """Send message to all connected devices in a zone"""
        device_ids = self.connected_devices.get(zone_id, set())
        
        for device_id in device_ids:
            await self._send_to_device(device_id, message)
    
    async def _broadcast_zone_state(self, zone_id: str):
        """Broadcast current zone state to all connected devices"""
        state = self.zone_states.get(zone_id)
        if not state:
            return
        
        await self._broadcast_to_zone(zone_id, {
            'type': 'zone_state',
            'zone_id': zone_id,
            'state': self._state_to_dict(state)
        })
    
    async def _send_to_device(self, device_id: str, message: Dict):
        """Send a message to a specific device"""
        websocket = self.device_websockets.get(device_id)
        if websocket:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to device {device_id}: {e}")
                await self.device_disconnected(device_id)
    
    def _state_to_dict(self, state: ZoneState) -> Dict:
        """Convert ZoneState to dictionary"""
        return {
            'zone_id': state.zone_id,
            'current_track_id': state.current_track_id,
            'track_info': state.track_info,
            'position_ms': state.position_ms,
            'is_playing': state.is_playing,
            'volume': state.volume,
            'shuffle': state.shuffle,
            'repeat_mode': state.repeat_mode,
            'queue': state.queue,
            'queue_index': state.queue_index
        }
    
    # ==================== Database Persistence ====================
    
    async def _save_zone_to_db(self, zone: Zone):
        """Save zone to database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT OR REPLACE INTO music_zones 
                       (id, name, user_id, room_id, icon, color, is_default)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (zone.id, zone.name, zone.user_id, zone.room_id, 
                     zone.icon, zone.color, zone.is_default)
                )
                
                # Also create initial state
                await db.execute(
                    """INSERT OR IGNORE INTO zone_playback_state (zone_id)
                       VALUES (?)""",
                    (zone.id,)
                )
                
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to save zone to database: {e}")
    
    async def _delete_zone_from_db(self, zone_id: str):
        """Delete zone from database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM music_zones WHERE id = ?", (zone_id,))
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to delete zone from database: {e}")
    
    async def _save_zone_state_to_db(self, state: ZoneState):
        """Save zone state to database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT OR REPLACE INTO zone_playback_state 
                       (zone_id, current_track_id, track_info, position_ms, 
                        is_playing, volume, shuffle, repeat_mode, queue, queue_index)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (state.zone_id, state.current_track_id, 
                     json.dumps(state.track_info) if state.track_info else None,
                     state.position_ms, state.is_playing, state.volume,
                     state.shuffle, state.repeat_mode,
                     json.dumps(state.queue), state.queue_index)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to save zone state to database: {e}")
    
    async def _save_zone_device_to_db(self, zone_id: str, device: ZoneDevice):
        """Save zone device to database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT OR REPLACE INTO zone_devices 
                       (zone_id, device_id, device_type, device_name, role, 
                        is_connected, supports_video, is_active)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                    (zone_id, device.device_id, device.device_type, 
                     device.device_name, device.role, device.is_connected,
                     device.supports_video)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to save zone device to database: {e}")
    
    async def _remove_zone_device_from_db(self, zone_id: str, device_id: str):
        """Remove zone device from database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM zone_devices WHERE zone_id = ? AND device_id = ?",
                    (zone_id, device_id)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to remove zone device from database: {e}")


# Global instance
zone_manager = ZoneManager()

