"""
Media Controller
================

Routes playback commands to appropriate devices.
Handles browser playback via WebSocket and HA speakers.

Fallback chain:
1. Target device (browser/panel via WebSocket)
2. Room's HA media_player
3. Broadcast to all user's audio-capable devices
"""

import asyncio
import logging
import sqlite3
import os
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Lazy-load event tracker to avoid circular imports
_event_tracker = None

def _get_event_tracker():
    """Lazy load event tracker."""
    global _event_tracker
    if _event_tracker is None:
        try:
            from .event_tracker import get_event_tracker
            _event_tracker = get_event_tracker()
        except Exception as e:
            logger.warning(f"Event tracker not available: {e}")
    return _event_tracker

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


class MediaController:
    """
    Controls music playback across devices.
    
    Supports:
    - Browser/panel playback via WebSocket
    - Home Assistant media_player devices
    - Fallback broadcast to all devices
    """
    
    def __init__(self, youtube_provider):
        """
        Initialize the media controller.
        
        Args:
            youtube_provider: YouTubeMusicProvider instance
        """
        self.youtube = youtube_provider
        self._init_db()
    
    def _init_db(self):
        """Initialize music playback tables."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Read and execute schema if it exists
            schema_path = "/app/db/schema/music.sql"
            if os.path.exists(schema_path):
                with open(schema_path, 'r') as f:
                    cursor.executescript(f.read())
            else:
                # Inline minimal schema
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS music_playback_state (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        device_id TEXT,
                        provider TEXT DEFAULT 'youtube_music',
                        track_id TEXT,
                        track_title TEXT,
                        artist TEXT,
                        album TEXT,
                        album_art_url TEXT,
                        position_ms INTEGER DEFAULT 0,
                        duration_ms INTEGER,
                        is_playing BOOLEAN DEFAULT FALSE,
                        volume INTEGER DEFAULT 100,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to init music DB: {e}")
    
    async def play(
        self,
        track_id: str,
        target_device_id: Optional[str],
        user_id: str,
        track_info: Optional[Dict] = None,
        mode: str = "audio",
        force_direct: bool = False
    ) -> Dict[str, Any]:
        """
        Play a track on the target device.
        
        Args:
            track_id: Track/video ID
            target_device_id: Device to play on
            user_id: User identifier
            track_info: Optional track metadata
            mode: "audio" for audio-only, "video" for video playback
            force_direct: If True, always return direct stream URL (for fallback)
            
        Returns:
            Result dict with success status and playback info
        """
        # Get stream URL based on mode
        video_data = None
        if mode == "video":
            # Pass track_info so we can search for the actual music video
            video_data = await self.youtube.get_stream_url_video(track_id, track_info)
            stream_url = video_data.get('url') if video_data else None
        else:
            stream_url = await self.youtube.get_stream_url(track_id)
        
        if not stream_url:
            return {
                "success": False,
                "error": "Could not get stream URL"
            }
        
        # Build playback message
        payload = {
            "type": "media_play",
            "url": stream_url,
            "track_id": track_id,
            "track_info": track_info or {},
            "provider": "youtube_music"
        }
        
        played_on = None
        device_info = None
        
        # Get device info
        if target_device_id:
            device_info = await self._get_device(target_device_id)
        
        # Try WebSocket to device
        if target_device_id:
            success = await self._send_to_device(target_device_id, payload)
            if success:
                played_on = {"type": "device", "id": target_device_id}
                logger.info(f"Playing {track_id} on device {target_device_id}")
        
        # Try HA media_player in room
        if not played_on and device_info and device_info.get("room"):
            result = await self._play_on_ha_speaker(
                device_info["room"],
                stream_url,
                track_info
            )
            if result:
                played_on = result
                logger.info(f"Playing {track_id} on HA speaker in {device_info['room']}")
        
        # Fallback: broadcast
        if not played_on:
            count = await self._broadcast_to_user(user_id, payload)
            if count > 0:
                played_on = {"type": "broadcast", "device_count": count}
                logger.info(f"Broadcast {track_id} to {count} devices for user {user_id}")
        
        # If no device received via WebSocket, return stream URL for browser playback
        if not played_on:
            played_on = {"type": "browser_direct", "stream_url": stream_url}
            logger.info(f"Returning stream URL for browser playback: {track_id}")
        
        # Update playback state
            await self._update_state(
                user_id,
                target_device_id,
                track_id,
                is_playing=True,
                track_info=track_info
            )
            
            # Track play start event for recommendations
            tracker = _get_event_tracker()
            if tracker:
                await tracker.track_play_start(
                    user_id=user_id,
                    track_id=track_id,
                    device_id=target_device_id,
                    source="search",  # TODO: Pass actual source
                    track_info=track_info
                )
        
        # For browser_direct, include full stream URL
        result = {
            "success": played_on is not None,
            "played_on": played_on,
            "track_id": track_id,
            "track_info": track_info,
            "mode": mode
        }
        
        # Include full stream URL for browser playback
        if played_on and played_on.get("type") == "browser_direct":
            result["stream_url"] = stream_url
            # For video mode, include video_id for YouTube embed (unless force_direct)
            if mode == "video" and video_data and not force_direct:
                # Use the official music video ID (different from track ID)
                official_video_id = video_data.get('video_id', track_id)
                result["video_id"] = official_video_id
                result["youtube_url"] = f"https://www.youtube.com/watch?v={official_video_id}"
                result["is_music_video"] = video_data.get('is_music_video', False)
            # When force_direct, just return stream_url (fallback from failed embed)
        else:
            result["stream_url"] = stream_url[:100] + "..." if stream_url else None
        
        return result
    
    async def pause(self, user_id: str, device_id: Optional[str] = None) -> Dict:
        """Pause playback."""
        payload = {"type": "media_pause"}
        
        if device_id:
            await self._send_to_device(device_id, payload)
        else:
            await self._broadcast_to_user(user_id, payload)
        
        await self._update_state(user_id, device_id, None, is_playing=False)
        
        return {"success": True, "action": "pause"}
    
    async def resume(self, user_id: str, device_id: Optional[str] = None) -> Dict:
        """Resume playback."""
        payload = {"type": "media_resume"}
        
        if device_id:
            await self._send_to_device(device_id, payload)
        else:
            await self._broadcast_to_user(user_id, payload)
        
        await self._update_state(user_id, device_id, None, is_playing=True)
        
        return {"success": True, "action": "resume"}
    
    async def skip(self, user_id: str, device_id: Optional[str] = None) -> Dict:
        """Skip to next track in queue."""
        # Track skip event for current track
        current_state = await self.get_state(user_id, device_id)
        if current_state and current_state.get("track_id"):
            tracker = _get_event_tracker()
            if tracker:
                await tracker.track_skip(
                    user_id=user_id,
                    track_id=current_state["track_id"],
                    position_ms=current_state.get("position_ms"),
                    duration_ms=current_state.get("duration_ms"),
                    device_id=device_id
                )
        
        next_track = await self._pop_queue(user_id, device_id)
        
        if next_track:
            return await self.play(
                next_track["track_id"],
                device_id,
                user_id,
                next_track
            )
        else:
            await self.pause(user_id, device_id)
            return {"success": True, "action": "skip", "queue_empty": True}
    
    async def previous(self, user_id: str, device_id: Optional[str] = None) -> Dict:
        """Play previous track (from history)."""
        prev_track = await self._get_previous_track(user_id)
        
        if prev_track:
            return await self.play(
                prev_track["track_id"],
                device_id,
                user_id,
                prev_track
            )
        
        return {"success": False, "error": "No previous track"}
    
    async def seek(self, user_id: str, position_ms: int, device_id: Optional[str] = None) -> Dict:
        """Seek to position in current track."""
        payload = {"type": "media_seek", "position_ms": position_ms}
        
        if device_id:
            await self._send_to_device(device_id, payload)
        else:
            await self._broadcast_to_user(user_id, payload)
        
        return {"success": True, "action": "seek", "position_ms": position_ms}
    
    async def set_volume(self, volume: int, device_id: Optional[str] = None, user_id: Optional[str] = None) -> Dict:
        """Set playback volume (0-100)."""
        volume = max(0, min(100, volume))
        payload = {"type": "media_volume", "volume": volume}
        
        if device_id:
            await self._send_to_device(device_id, payload)
        elif user_id:
            await self._broadcast_to_user(user_id, payload)
        
        return {"success": True, "action": "volume", "volume": volume}
    
    async def get_state(self, user_id: str, device_id: Optional[str] = None) -> Optional[Dict]:
        """Get current playback state."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if device_id:
                cursor.execute("""
                    SELECT * FROM music_playback_state
                    WHERE user_id = ? AND device_id = ?
                """, (user_id, device_id))
            else:
                cursor.execute("""
                    SELECT * FROM music_playback_state
                    WHERE user_id = ? AND is_playing = TRUE
                    ORDER BY updated_at DESC LIMIT 1
                """, (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return dict(row)
            return None
            
        except Exception as e:
            logger.error(f"Failed to get playback state: {e}")
            return None
    
    async def add_to_queue(
        self,
        user_id: str,
        track_id: str,
        track_info: Dict,
        device_id: Optional[str] = None,
        position: Optional[int] = None
    ) -> Dict:
        """Add a track to the queue.
        
        Args:
            position: If specified, insert at this position (0 = play next).
                     If None, append to end of queue.
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            if position is not None:
                # Insert at specific position - shift existing items
                cursor.execute("""
                    UPDATE music_queue 
                    SET position = position + 1 
                    WHERE user_id = ? AND (device_id = ? OR device_id IS NULL)
                    AND position >= ?
                """, (user_id, device_id, position))
                insert_position = position
            else:
                # Get next position (append to end)
                cursor.execute("""
                    SELECT COALESCE(MAX(position), -1) + 1 FROM music_queue
                    WHERE user_id = ? AND (device_id = ? OR device_id IS NULL)
                """, (user_id, device_id))
                insert_position = cursor.fetchone()[0]
            
            cursor.execute("""
                INSERT INTO music_queue 
                (user_id, device_id, track_id, provider, track_title, artist, album_art_url, position)
                VALUES (?, ?, ?, 'youtube_music', ?, ?, ?, ?)
            """, (
                user_id, device_id, track_id,
                track_info.get("title"), track_info.get("artist"),
                track_info.get("thumbnail", "") or track_info.get("thumbnail_url", ""),
                insert_position
            ))
            
            conn.commit()
            conn.close()
            
            # Track queue_add event for recommendations
            tracker = _get_event_tracker()
            if tracker:
                await tracker.track_queue_add(
                    user_id=user_id,
                    track_id=track_id,
                    device_id=device_id
                )
            
            return {"success": True, "position": insert_position}
            
        except Exception as e:
            logger.error(f"Failed to add to queue: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_queue(self, user_id: str, device_id: Optional[str] = None) -> List[Dict]:
        """Get the current queue."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT track_id, track_title, artist, album_art_url, position, provider
                FROM music_queue
                WHERE user_id = ? AND (device_id = ? OR device_id IS NULL)
                ORDER BY position
            """, (user_id, device_id))
            
            rows = cursor.fetchall()
            conn.close()
            
            # Return with consistent field names for frontend
            return [{
                'id': row['track_id'],  # Use track_id as the primary ID
                'videoId': row['track_id'],
                'title': row['track_title'] or 'Unknown',
                'artist': row['artist'] or '',
                'thumbnail_url': row['album_art_url'] or '',
                'position': row['position'],
                'provider': row['provider']
            } for row in rows]
            
        except Exception as e:
            logger.error(f"Failed to get queue: {e}")
            return []
    
    async def remove_from_queue(
        self,
        user_id: str,
        track_id: str,
        device_id: Optional[str] = None
    ) -> Dict:
        """Remove a track from the queue."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Get the position of the track being removed
            cursor.execute("""
                SELECT position FROM music_queue
                WHERE user_id = ? AND track_id = ? AND (device_id = ? OR device_id IS NULL)
            """, (user_id, track_id, device_id))
            row = cursor.fetchone()
            
            if not row:
                conn.close()
                return {"success": False, "error": "Track not found in queue"}
            
            removed_position = row[0]
            
            # Delete the track
            cursor.execute("""
                DELETE FROM music_queue
                WHERE user_id = ? AND track_id = ? AND (device_id = ? OR device_id IS NULL)
            """, (user_id, track_id, device_id))
            
            # Shift remaining items down
            cursor.execute("""
                UPDATE music_queue
                SET position = position - 1
                WHERE user_id = ? AND (device_id = ? OR device_id IS NULL)
                AND position > ?
            """, (user_id, device_id, removed_position))
            
            conn.commit()
            conn.close()
            
            return {"success": True}
            
        except Exception as e:
            logger.error(f"Failed to remove from queue: {e}")
            return {"success": False, "error": str(e)}
    
    async def reorder_queue(
        self,
        user_id: str,
        track_id: str,
        new_position: int,
        device_id: Optional[str] = None
    ) -> Dict:
        """Reorder a track in the queue."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Get current position
            cursor.execute("""
                SELECT position FROM music_queue
                WHERE user_id = ? AND track_id = ? AND (device_id = ? OR device_id IS NULL)
            """, (user_id, track_id, device_id))
            row = cursor.fetchone()
            
            if not row:
                conn.close()
                return {"success": False, "error": "Track not found in queue"}
            
            old_position = row[0]
            
            if old_position == new_position:
                conn.close()
                return {"success": True}
            
            if new_position < old_position:
                # Moving up: shift items between new and old position down
                cursor.execute("""
                    UPDATE music_queue
                    SET position = position + 1
                    WHERE user_id = ? AND (device_id = ? OR device_id IS NULL)
                    AND position >= ? AND position < ?
                """, (user_id, device_id, new_position, old_position))
            else:
                # Moving down: shift items between old and new position up
                cursor.execute("""
                    UPDATE music_queue
                    SET position = position - 1
                    WHERE user_id = ? AND (device_id = ? OR device_id IS NULL)
                    AND position > ? AND position <= ?
                """, (user_id, device_id, old_position, new_position))
            
            # Move the track to new position
            cursor.execute("""
                UPDATE music_queue
                SET position = ?
                WHERE user_id = ? AND track_id = ? AND (device_id = ? OR device_id IS NULL)
            """, (new_position, user_id, track_id, device_id))
            
            conn.commit()
            conn.close()
            
            return {"success": True}
            
        except Exception as e:
            logger.error(f"Failed to reorder queue: {e}")
            return {"success": False, "error": str(e)}
    
    # ============================================================
    # Private Methods
    # ============================================================
    
    async def _get_device(self, device_id: str) -> Optional[Dict]:
        """Get device info from database."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM devices WHERE id = ?", (device_id,))
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            return None
    
    async def _send_to_device(self, device_id: str, message: Dict) -> bool:
        """Send message to device via WebSocket."""
        try:
            from routers.websocket import send_to_device
            return await send_to_device(device_id, message)
        except Exception as e:
            logger.warning(f"Failed to send to device {device_id}: {e}")
            return False
    
    async def _broadcast_to_user(self, user_id: str, message: Dict) -> int:
        """Broadcast to all user's devices."""
        try:
            from routers.websocket import broadcast_to_user
            return await broadcast_to_user(user_id, message)
        except Exception as e:
            logger.warning(f"Failed to broadcast to user {user_id}: {e}")
            return 0
    
    async def _play_on_ha_speaker(
        self,
        room: str,
        stream_url: str,
        track_info: Optional[Dict] = None
    ) -> Optional[Dict]:
        """Play on Home Assistant media_player in room."""
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                # Get media_players in room
                response = await client.get(
                    "http://localhost:8000/api/homeassistant/entities",
                    params={"domain": "media_player"},
                    timeout=5.0
                )
                
                if response.status_code != 200:
                    return None
                
                data = response.json()
                entities = data.get("entities", [])
                
                # Find matching room
                room_normalized = room.lower().replace(" ", "_")
                for entity in entities:
                    entity_id = entity.get("entity_id", "")
                    if room_normalized in entity_id.lower():
                        # Call play_media
                        await client.post(
                            "http://localhost:8000/api/homeassistant/service",
                            json={
                                "service": "media_player.play_media",
                                "entity_id": entity_id,
                                "data": {
                                    "media_content_id": stream_url,
                                    "media_content_type": "music"
                                }
                            },
                            headers={"X-Auth-Token": "internal"},
                            timeout=10.0
                        )
                        
                        return {
                            "type": "ha_media_player",
                            "entity_id": entity_id,
                            "room": room
                        }
            
            return None
            
        except Exception as e:
            logger.warning(f"Failed to play on HA speaker: {e}")
            return None
    
    async def _update_state(
        self,
        user_id: str,
        device_id: Optional[str],
        track_id: Optional[str],
        is_playing: bool,
        track_info: Optional[Dict] = None
    ):
        """Update playback state in database."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            state_id = f"{user_id}_{device_id or 'default'}"
            
            cursor.execute("""
                INSERT OR REPLACE INTO music_playback_state 
                (id, user_id, device_id, track_id, track_title, artist, album, 
                 album_art_url, is_playing, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                state_id, user_id, device_id, track_id,
                track_info.get("title") if track_info else None,
                track_info.get("artist") if track_info else None,
                track_info.get("album") if track_info else None,
                track_info.get("thumbnail_url") if track_info else None,
                is_playing
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to update playback state: {e}")
    
    async def _pop_queue(self, user_id: str, device_id: Optional[str]) -> Optional[Dict]:
        """Pop the next track from queue."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get first item
            cursor.execute("""
                SELECT * FROM music_queue
                WHERE user_id = ? AND (device_id = ? OR device_id IS NULL)
                ORDER BY position LIMIT 1
            """, (user_id, device_id))
            
            row = cursor.fetchone()
            
            if row:
                # Delete it
                cursor.execute("DELETE FROM music_queue WHERE id = ?", (row["id"],))
                conn.commit()
                conn.close()
                return dict(row)
            
            conn.close()
            return None
            
        except Exception as e:
            logger.error(f"Failed to pop queue: {e}")
            return None
    
    async def _get_previous_track(self, user_id: str) -> Optional[Dict]:
        """Get previous track from history."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT track_id, track_title as title, artist, album
                FROM music_history
                WHERE user_id = ?
                ORDER BY played_at DESC
                LIMIT 1 OFFSET 1
            """, (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            return dict(row) if row else None
            
        except Exception as e:
            logger.error(f"Failed to get previous track: {e}")
            return None


# Singleton
_media_controller: Optional[MediaController] = None


def get_media_controller() -> MediaController:
    """Get the singleton media controller instance."""
    global _media_controller
    if _media_controller is None:
        from services.music.youtube_music import get_youtube_music
        _media_controller = MediaController(get_youtube_music())
    return _media_controller

