"""
Music Event Tracker
====================

Captures listening behavior events for affinity scoring and recommendations.

Events tracked:
- play_start: Track started playing
- play_end: Track completed (>80% listened)
- skip: Track skipped early (<30% listened)
- repeat: Track played again within session
- like: Explicit like action
- queue_add: Added to queue (intent signal)

All events contribute to affinity scoring with temporal decay.
"""

import sqlite3
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


@dataclass
class MusicEvent:
    """A music listening event"""
    user_id: str
    track_id: str
    event_type: str
    device_id: Optional[str] = None
    session_id: Optional[str] = None
    source: Optional[str] = None
    position_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    completion_pct: Optional[float] = None
    timestamp: Optional[datetime] = None


class MusicEventTracker:
    """
    Track listening events for behavioral learning.
    
    Events are stored in music_events table and used by AffinityEngine
    to calculate per-track and per-artist preference scores.
    """
    
    # Event type weights for affinity scoring
    EVENT_WEIGHTS = {
        "play_start": 0.0,      # Neutral - just started
        "play_end": 1.0,        # Positive - completed
        "skip": -1.5,           # Negative - skipped early
        "repeat": 2.0,          # Strong positive - played again
        "like": 3.0,            # Explicit positive
        "queue_add": 2.5,       # Positive intent
    }
    
    def __init__(self):
        """Initialize the event tracker and ensure tables exist."""
        self._init_db()
    
    def _init_db(self):
        """Ensure music_events table exists."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS music_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    device_id TEXT,
                    session_id TEXT,
                    source TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    position_ms INTEGER,
                    duration_ms INTEGER,
                    completion_pct REAL
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_user_time 
                ON music_events(user_id, timestamp DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_track 
                ON music_events(track_id)
            """)
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to init music_events table: {e}")
    
    async def track_event(
        self,
        user_id: str,
        track_id: str,
        event_type: str,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
        position_ms: Optional[int] = None,
        duration_ms: Optional[int] = None,
        track_info: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Record a listening event.
        
        Args:
            user_id: User identifier
            track_id: Track/video ID
            event_type: Type of event (play_start, play_end, skip, repeat, like, queue_add)
            device_id: Source device
            session_id: Listening session ID
            source: How track was accessed (search, playlist, radio, similar, queue)
            position_ms: Playback position when event occurred
            duration_ms: Total track duration
            track_info: Optional track metadata for logging
            
        Returns:
            True if event was recorded successfully
        """
        try:
            # Calculate completion percentage if we have position and duration
            completion_pct = None
            if position_ms is not None and duration_ms and duration_ms > 0:
                completion_pct = position_ms / duration_ms
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO music_events 
                (user_id, track_id, event_type, device_id, session_id, 
                 source, position_ms, duration_ms, completion_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, track_id, event_type, device_id, session_id,
                source, position_ms, duration_ms, completion_pct
            ))
            
            conn.commit()
            conn.close()
            
            # Log for debugging
            track_name = track_info.get("title", track_id) if track_info else track_id
            logger.debug(f"🎵 Event: {event_type} for '{track_name}' by user {user_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to track music event: {e}")
            return False
    
    async def track_play_start(
        self,
        user_id: str,
        track_id: str,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
        track_info: Optional[Dict] = None
    ) -> bool:
        """Track play start event."""
        return await self.track_event(
            user_id=user_id,
            track_id=track_id,
            event_type="play_start",
            device_id=device_id,
            session_id=session_id,
            source=source,
            track_info=track_info
        )
    
    async def track_play_end(
        self,
        user_id: str,
        track_id: str,
        position_ms: int,
        duration_ms: int,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        track_info: Optional[Dict] = None
    ) -> bool:
        """
        Track play end event.
        
        Automatically determines if this was a skip or completion based on
        how much of the track was listened to.
        """
        if duration_ms <= 0:
            return False
        
        completion = position_ms / duration_ms
        
        # Determine event type based on completion
        if completion < 0.3:
            event_type = "skip"
        else:
            event_type = "play_end"
        
        return await self.track_event(
            user_id=user_id,
            track_id=track_id,
            event_type=event_type,
            device_id=device_id,
            session_id=session_id,
            position_ms=position_ms,
            duration_ms=duration_ms,
            track_info=track_info
        )
    
    async def track_skip(
        self,
        user_id: str,
        track_id: str,
        position_ms: Optional[int] = None,
        duration_ms: Optional[int] = None,
        device_id: Optional[str] = None
    ) -> bool:
        """Track explicit skip event."""
        return await self.track_event(
            user_id=user_id,
            track_id=track_id,
            event_type="skip",
            device_id=device_id,
            position_ms=position_ms,
            duration_ms=duration_ms
        )
    
    async def track_like(
        self,
        user_id: str,
        track_id: str,
        device_id: Optional[str] = None
    ) -> bool:
        """Track like event."""
        return await self.track_event(
            user_id=user_id,
            track_id=track_id,
            event_type="like",
            device_id=device_id
        )
    
    async def track_repeat(
        self,
        user_id: str,
        track_id: str,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> bool:
        """Track repeat play event."""
        return await self.track_event(
            user_id=user_id,
            track_id=track_id,
            event_type="repeat",
            device_id=device_id,
            session_id=session_id
        )
    
    async def track_queue_add(
        self,
        user_id: str,
        track_id: str,
        device_id: Optional[str] = None
    ) -> bool:
        """Track queue add event."""
        return await self.track_event(
            user_id=user_id,
            track_id=track_id,
            event_type="queue_add",
            device_id=device_id
        )
    
    def get_event_weight(self, event_type: str) -> float:
        """Get the affinity weight for an event type."""
        return self.EVENT_WEIGHTS.get(event_type, 0.0)


# Singleton instance
_event_tracker: Optional[MusicEventTracker] = None


def get_event_tracker() -> MusicEventTracker:
    """Get the singleton event tracker instance."""
    global _event_tracker
    if _event_tracker is None:
        _event_tracker = MusicEventTracker()
    return _event_tracker

