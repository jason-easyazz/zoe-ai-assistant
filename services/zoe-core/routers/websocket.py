"""
WebSocket Manager with Device Tracking
======================================

Enhanced WebSocket management that tracks connections by user_id and device_id,
enabling targeted message delivery for features like timer alerts.

Features:
- Track connections by user_id and device_id
- Send to specific device
- Broadcast to user's devices
- Broadcast to all connections
- Room-based messaging

Endpoints:
- WS /ws/device - Device-aware WebSocket connection
"""

import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, Any, Set, Optional, List
from dataclasses import dataclass, field
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@dataclass
class DeviceConnection:
    """Represents a WebSocket connection with device metadata."""
    websocket: WebSocket
    user_id: str
    device_id: Optional[str] = None
    room: Optional[str] = None
    connected_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)


class DeviceAwareWebSocketManager:
    """
    WebSocket manager that tracks connections by user and device.
    
    Supports:
    - Sending to specific device
    - Broadcasting to user's devices
    - Broadcasting to room
    - Broadcasting to all
    """
    
    def __init__(self):
        # All active connections
        self.connections: Dict[WebSocket, DeviceConnection] = {}
        
        # Index by user_id for fast lookup
        self.user_connections: Dict[str, Set[WebSocket]] = {}
        
        # Index by device_id for fast lookup
        self.device_connections: Dict[str, WebSocket] = {}
        
        # Index by room for fast lookup
        self.room_connections: Dict[str, Set[WebSocket]] = {}
        
        self.lock = asyncio.Lock()
    
    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        device_id: Optional[str] = None,
        room: Optional[str] = None
    ) -> DeviceConnection:
        """
        Accept and register a new WebSocket connection.
        
        Args:
            websocket: The WebSocket connection
            user_id: User identifier
            device_id: Device identifier (optional)
            room: Room/location (optional)
            
        Returns:
            DeviceConnection object
        """
        await websocket.accept()
        
        conn = DeviceConnection(
            websocket=websocket,
            user_id=user_id,
            device_id=device_id,
            room=room
        )
        
        async with self.lock:
            # Store connection
            self.connections[websocket] = conn
            
            # Index by user
            if user_id not in self.user_connections:
                self.user_connections[user_id] = set()
            self.user_connections[user_id].add(websocket)
            
            # Index by device
            if device_id:
                # Disconnect old connection for this device if exists
                if device_id in self.device_connections:
                    old_ws = self.device_connections[device_id]
                    await self._remove_connection(old_ws)
                self.device_connections[device_id] = websocket
            
            # Index by room
            if room:
                if room not in self.room_connections:
                    self.room_connections[room] = set()
                self.room_connections[room].add(websocket)
        
        logger.info(f"📱 WebSocket connected: user={user_id}, device={device_id}, room={room}")
        return conn
    
    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        async with self.lock:
            await self._remove_connection(websocket)
    
    async def _remove_connection(self, websocket: WebSocket) -> None:
        """Internal method to remove a connection (must hold lock)."""
        if websocket not in self.connections:
            return
        
        conn = self.connections[websocket]
        
        # Remove from user index
        if conn.user_id in self.user_connections:
            self.user_connections[conn.user_id].discard(websocket)
            if not self.user_connections[conn.user_id]:
                del self.user_connections[conn.user_id]
        
        # Remove from device index
        if conn.device_id and conn.device_id in self.device_connections:
            if self.device_connections[conn.device_id] == websocket:
                del self.device_connections[conn.device_id]
        
        # Remove from room index
        if conn.room and conn.room in self.room_connections:
            self.room_connections[conn.room].discard(websocket)
            if not self.room_connections[conn.room]:
                del self.room_connections[conn.room]
        
        # Remove main connection
        del self.connections[websocket]
        
        # Try to close the websocket
        try:
            await websocket.close()
        except Exception:
            pass
        
        logger.info(f"📱 WebSocket disconnected: user={conn.user_id}, device={conn.device_id}")
    
    async def send_to_device(self, device_id: str, message: Dict[str, Any]) -> bool:
        """
        Send a message to a specific device.
        
        Args:
            device_id: Target device identifier
            message: Message payload
            
        Returns:
            True if message was sent, False if device not connected
        """
        async with self.lock:
            if device_id not in self.device_connections:
                return False
            websocket = self.device_connections[device_id]
        
        try:
            await websocket.send_json(message)
            return True
        except Exception as e:
            logger.warning(f"Failed to send to device {device_id}: {e}")
            await self.disconnect(websocket)
            return False
    
    async def broadcast_to_user(self, user_id: str, message: Dict[str, Any]) -> int:
        """
        Broadcast a message to all of a user's connected devices.
        
        Args:
            user_id: Target user identifier
            message: Message payload
            
        Returns:
            Number of devices that received the message
        """
        async with self.lock:
            if user_id not in self.user_connections:
                return 0
            websockets = list(self.user_connections[user_id])
        
        sent_count = 0
        failed = []
        
        for ws in websockets:
            try:
                await ws.send_json(message)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send to user {user_id} device: {e}")
                failed.append(ws)
        
        # Clean up failed connections
        for ws in failed:
            await self.disconnect(ws)
        
        return sent_count
    
    async def broadcast_to_room(self, room: str, message: Dict[str, Any]) -> int:
        """
        Broadcast a message to all devices in a room.
        
        Args:
            room: Target room
            message: Message payload
            
        Returns:
            Number of devices that received the message
        """
        async with self.lock:
            if room not in self.room_connections:
                return 0
            websockets = list(self.room_connections[room])
        
        sent_count = 0
        failed = []
        
        for ws in websockets:
            try:
                await ws.send_json(message)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send to room {room}: {e}")
                failed.append(ws)
        
        # Clean up failed connections
        for ws in failed:
            await self.disconnect(ws)
        
        return sent_count
    
    async def broadcast_all(self, message: Dict[str, Any]) -> int:
        """
        Broadcast a message to all connected devices.
        
        Args:
            message: Message payload
            
        Returns:
            Number of devices that received the message
        """
        async with self.lock:
            websockets = list(self.connections.keys())
        
        sent_count = 0
        failed = []
        
        for ws in websockets:
            try:
                await ws.send_json(message)
                sent_count += 1
            except Exception:
                failed.append(ws)
        
        for ws in failed:
            await self.disconnect(ws)
        
        return sent_count
    
    def get_user_device_count(self, user_id: str) -> int:
        """Get number of connected devices for a user."""
        return len(self.user_connections.get(user_id, set()))
    
    def is_device_online(self, device_id: str) -> bool:
        """Check if a device is currently connected."""
        return device_id in self.device_connections
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        return {
            "total_connections": len(self.connections),
            "unique_users": len(self.user_connections),
            "unique_devices": len(self.device_connections),
            "rooms": list(self.room_connections.keys())
        }


# Global instance
websocket_manager = DeviceAwareWebSocketManager()


# ============================================================
# Convenience Functions (for use by other modules)
# ============================================================

async def send_to_device(device_id: str, message: Dict[str, Any]) -> bool:
    """Send a message to a specific device."""
    return await websocket_manager.send_to_device(device_id, message)


async def broadcast_to_user(user_id: str, message: Dict[str, Any]) -> int:
    """Broadcast to all of a user's devices."""
    return await websocket_manager.broadcast_to_user(user_id, message)


async def broadcast_to_room(room: str, message: Dict[str, Any]) -> int:
    """Broadcast to all devices in a room."""
    return await websocket_manager.broadcast_to_room(room, message)


async def broadcast_all(message: Dict[str, Any]) -> int:
    """Broadcast to all connected devices."""
    return await websocket_manager.broadcast_all(message)


def is_device_online(device_id: str) -> bool:
    """Check if a device is online."""
    return websocket_manager.is_device_online(device_id)


# ============================================================
# WebSocket Endpoints
# ============================================================

@router.websocket("/ws/device")
async def device_websocket(
    websocket: WebSocket,
    user_id: str = Query(..., description="User identifier"),
    device_id: Optional[str] = Query(None, description="Device identifier"),
    room: Optional[str] = Query(None, description="Room/location")
):
    """
    Device-aware WebSocket endpoint.
    
    Connect with user_id (required) and optionally device_id and room.
    Enables targeted message delivery for timers, notifications, etc.
    
    Example: ws://host/ws/device?user_id=jason&device_id=kitchen-panel&room=kitchen
    """
    conn = await websocket_manager.connect(websocket, user_id, device_id, room)
    
    try:
        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "data": {
                "user_id": user_id,
                "device_id": device_id,
                "room": room,
                "connected_at": conn.connected_at.isoformat()
            }
        })
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Wait for messages (with timeout for heartbeat)
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=30.0
                )
                
                # Handle incoming message types
                msg_type = data.get("type", "unknown")
                
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "ts": datetime.now().isoformat()})
                
                elif msg_type == "update_room":
                    # Allow device to update its room
                    new_room = data.get("room")
                    if new_room:
                        async with websocket_manager.lock:
                            if websocket in websocket_manager.connections:
                                old_room = websocket_manager.connections[websocket].room
                                
                                # Remove from old room index
                                if old_room and old_room in websocket_manager.room_connections:
                                    websocket_manager.room_connections[old_room].discard(websocket)
                                
                                # Add to new room index
                                if new_room not in websocket_manager.room_connections:
                                    websocket_manager.room_connections[new_room] = set()
                                websocket_manager.room_connections[new_room].add(websocket)
                                
                                # Update connection
                                websocket_manager.connections[websocket].room = new_room
                        
                        await websocket.send_json({
                            "type": "room_updated",
                            "room": new_room
                        })
                
                elif msg_type == "subscribe":
                    # Subscribe to specific event types (future enhancement)
                    pass
                
                elif msg_type == "join_zone":
                    # Join a music zone
                    zone_id = data.get("zone_id")
                    role = data.get("as", "both")  # player, controller, both
                    if zone_id:
                        try:
                            from services.music.zone_manager import zone_manager
                            await zone_manager.device_connected(zone_id, device_id or user_id, websocket)
                            await websocket.send_json({
                                "type": "zone_joined",
                                "zone_id": zone_id,
                                "role": role
                            })
                        except Exception as e:
                            logger.warning(f"Failed to join zone {zone_id}: {e}")
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Failed to join zone: {str(e)}"
                            })
                
                elif msg_type == "leave_zone":
                    # Leave a music zone
                    zone_id = data.get("zone_id")
                    if zone_id:
                        try:
                            from services.music.zone_manager import zone_manager
                            await zone_manager.device_disconnected(device_id or user_id)
                            await websocket.send_json({
                                "type": "zone_left",
                                "zone_id": zone_id
                            })
                        except Exception as e:
                            logger.warning(f"Failed to leave zone {zone_id}: {e}")
                
                elif msg_type == "zone_command":
                    # Send a command to a zone (play, pause, skip, etc.)
                    zone_id = data.get("zone_id")
                    command = data.get("command")
                    command_data = data.get("data", {})
                    if zone_id and command:
                        try:
                            from services.music.zone_manager import zone_manager
                            result = await _handle_zone_command(zone_manager, zone_id, command, command_data)
                            await websocket.send_json({
                                "type": "zone_command_result",
                                "zone_id": zone_id,
                                "command": command,
                                "success": result.get("success", False),
                                "data": result
                            })
                        except Exception as e:
                            logger.warning(f"Zone command failed: {e}")
                            await websocket.send_json({
                                "type": "error",
                                "message": str(e)
                            })
                
            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "ts": datetime.now().isoformat()
                    })
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket error for {device_id or user_id}: {e}")
    finally:
        await websocket_manager.disconnect(websocket)
        
        # Update device offline status
        if device_id:
            try:
                from routers.devices import set_device_offline
                await set_device_offline(device_id)
            except Exception:
                pass


async def _handle_zone_command(zone_manager, zone_id: str, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a zone command from WebSocket."""
    if command == "play":
        track_id = data.get("track_id")
        track_info = data.get("track_info", {})
        stream_url = data.get("stream_url")
        if track_id:
            success = await zone_manager.play(zone_id, track_id, track_info, stream_url)
            return {"success": success}
        return {"success": False, "error": "No track_id provided"}
    
    elif command == "pause":
        success = await zone_manager.pause(zone_id)
        return {"success": success}
    
    elif command == "resume":
        success = await zone_manager.resume(zone_id)
        return {"success": success}
    
    elif command == "skip":
        next_track = await zone_manager.skip(zone_id)
        return {"success": next_track is not None, "track": next_track}
    
    elif command == "previous":
        prev_track = await zone_manager.previous(zone_id)
        return {"success": prev_track is not None, "track": prev_track}
    
    elif command == "seek":
        position_ms = data.get("position_ms", 0)
        success = await zone_manager.seek(zone_id, position_ms)
        return {"success": success}
    
    elif command == "volume":
        volume = data.get("volume", 80)
        success = await zone_manager.set_volume(zone_id, volume)
        return {"success": success}
    
    elif command == "add_to_queue":
        track = data.get("track", {})
        success = await zone_manager.add_to_queue(zone_id, track)
        return {"success": success}
    
    elif command == "clear_queue":
        success = await zone_manager.clear_queue(zone_id)
        return {"success": success}
    
    else:
        return {"success": False, "error": f"Unknown command: {command}"}


@router.websocket("/api/ws/device")
async def device_websocket_api(
    websocket: WebSocket,
    user_id: str = Query(...),
    device_id: Optional[str] = Query(None),
    room: Optional[str] = Query(None)
):
    """API-prefixed alias for device websocket."""
    await device_websocket(websocket, user_id, device_id, room)


@router.get("/api/websocket/stats")
async def websocket_stats():
    """Get WebSocket connection statistics."""
    return websocket_manager.get_connection_stats()

