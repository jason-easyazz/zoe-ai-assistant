"""
Music Affinity Engine
=====================

Calculates user preferences from listening behavior using weighted scoring
with temporal decay. Recent events matter more than old ones.

Scoring Formula:
    score = Σ (event_weight × e^(-age_days / half_life))

Event Weights:
    play_end: +1.0 (completed track)
    skip: -1.5 (skipped early)
    repeat: +2.0 (played again)
    like: +3.0 (explicit like)
    queue_add: +2.5 (intent signal)

Temporal Decay:
    Half-life of 30 days (events from 30 days ago have 50% weight)
"""

import sqlite3
import math
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Event weights (positive = like, negative = dislike)
EVENT_WEIGHTS = {
    "play_start": 0.0,    # Neutral - doesn't indicate preference
    "play_end": 1.0,      # Completed = liked
    "skip": -1.5,         # Skipped = didn't like
    "repeat": 2.0,        # Repeated = really liked
    "like": 3.0,          # Explicit like = strong signal
    "queue_add": 2.5,     # Added to queue = intent to listen
}

# Temporal decay half-life in days
HALF_LIFE_DAYS = 30


@dataclass
class AffinityScore:
    """Affinity score with metadata"""
    entity_id: str  # track_id or artist name
    entity_type: str  # "track" or "artist"
    score: float
    event_count: int
    last_event: Optional[datetime] = None


class AffinityEngine:
    """
    Calculate affinity scores for tracks and artists.
    
    Scores are weighted sums of events with temporal decay,
    meaning recent events matter more than old ones.
    """
    
    def __init__(self, half_life_days: float = HALF_LIFE_DAYS):
        """
        Initialize the affinity engine.
        
        Args:
            half_life_days: Events from this many days ago have 50% weight
        """
        self.half_life = half_life_days
    
    def _calculate_decay(self, age_days: float) -> float:
        """
        Calculate temporal decay factor.
        
        Uses exponential decay: e^(-age / half_life)
        This gives 50% weight at half_life days, 25% at 2*half_life, etc.
        """
        return math.exp(-age_days / self.half_life)
    
    def calculate_track_affinity(self, user_id: str, track_id: str) -> float:
        """
        Get affinity score for a specific track.
        
        Args:
            user_id: User identifier
            track_id: Track/video ID
            
        Returns:
            Affinity score (positive = liked, negative = disliked, 0 = neutral)
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT event_type, timestamp 
                FROM music_events 
                WHERE user_id = ? AND track_id = ?
            """, (user_id, track_id))
            
            events = cursor.fetchall()
            conn.close()
            
            if not events:
                return 0.0
            
            now = datetime.now()
            score = 0.0
            
            for event_type, timestamp_str in events:
                weight = EVENT_WEIGHTS.get(event_type, 0)
                
                # Parse timestamp
                try:
                    event_time = datetime.fromisoformat(timestamp_str)
                except (ValueError, TypeError):
                    event_time = now  # Fallback to now if parsing fails
                
                age_days = (now - event_time).total_seconds() / 86400
                decay = self._calculate_decay(age_days)
                score += weight * decay
            
            return score
            
        except Exception as e:
            logger.error(f"Failed to calculate track affinity: {e}")
            return 0.0
    
    def calculate_artist_affinity(self, user_id: str, artist: str) -> float:
        """
        Get affinity score for an artist.
        
        Aggregates affinity across all tracks by this artist.
        
        Args:
            user_id: User identifier
            artist: Artist name
            
        Returns:
            Affinity score
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Join events with playback state to get artist info
            cursor.execute("""
                SELECT me.event_type, me.timestamp
                FROM music_events me
                JOIN music_playback_state mps ON me.track_id = mps.track_id
                WHERE me.user_id = ? AND mps.artist = ?
            """, (user_id, artist))
            
            events = cursor.fetchall()
            conn.close()
            
            if not events:
                return 0.0
            
            now = datetime.now()
            score = 0.0
            
            for event_type, timestamp_str in events:
                weight = EVENT_WEIGHTS.get(event_type, 0)
                
                try:
                    event_time = datetime.fromisoformat(timestamp_str)
                except (ValueError, TypeError):
                    event_time = now
                
                age_days = (now - event_time).total_seconds() / 86400
                decay = self._calculate_decay(age_days)
                score += weight * decay
            
            return score
            
        except Exception as e:
            logger.error(f"Failed to calculate artist affinity: {e}")
            return 0.0
    
    def get_top_tracks(
        self,
        user_id: str,
        limit: int = 20,
        min_score: float = 0.0
    ) -> List[Tuple[str, float]]:
        """
        Get user's top tracks by affinity score.
        
        Args:
            user_id: User identifier
            limit: Max tracks to return
            min_score: Minimum score threshold
            
        Returns:
            List of (track_id, score) tuples, sorted by score descending
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Get all unique tracks for this user
            cursor.execute("""
                SELECT DISTINCT track_id FROM music_events WHERE user_id = ?
            """, (user_id,))
            
            track_ids = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            # Calculate scores
            scores = []
            for track_id in track_ids:
                score = self.calculate_track_affinity(user_id, track_id)
                if score >= min_score:
                    scores.append((track_id, score))
            
            # Sort by score descending
            scores.sort(key=lambda x: x[1], reverse=True)
            
            return scores[:limit]
            
        except Exception as e:
            logger.error(f"Failed to get top tracks: {e}")
            return []
    
    def get_top_artists(
        self,
        user_id: str,
        limit: int = 10,
        min_score: float = 0.0
    ) -> List[Tuple[str, float]]:
        """
        Get user's top artists by affinity score.
        
        Args:
            user_id: User identifier
            limit: Max artists to return
            min_score: Minimum score threshold
            
        Returns:
            List of (artist, score) tuples, sorted by score descending
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Get all unique artists for this user
            cursor.execute("""
                SELECT DISTINCT mps.artist
                FROM music_events me
                JOIN music_playback_state mps ON me.track_id = mps.track_id
                WHERE me.user_id = ? AND mps.artist IS NOT NULL
            """, (user_id,))
            
            artists = [row[0] for row in cursor.fetchall() if row[0]]
            conn.close()
            
            # Calculate scores
            scores = []
            for artist in artists:
                score = self.calculate_artist_affinity(user_id, artist)
                if score >= min_score:
                    scores.append((artist, score))
            
            # Sort by score descending
            scores.sort(key=lambda x: x[1], reverse=True)
            
            return scores[:limit]
            
        except Exception as e:
            logger.error(f"Failed to get top artists: {e}")
            return []
    
    def get_listening_stats(self, user_id: str) -> Dict[str, Any]:
        """
        Get aggregate listening statistics for a user.
        
        Returns:
            Dict with total_plays, total_skips, total_likes, etc.
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Count events by type
            cursor.execute("""
                SELECT event_type, COUNT(*) 
                FROM music_events 
                WHERE user_id = ?
                GROUP BY event_type
            """, (user_id,))
            
            event_counts = dict(cursor.fetchall())
            
            # Get total listening time (from play_end events with duration)
            cursor.execute("""
                SELECT SUM(duration_ms)
                FROM music_events
                WHERE user_id = ? AND event_type = 'play_end' AND duration_ms > 0
            """, (user_id,))
            
            total_ms = cursor.fetchone()[0] or 0
            
            # Get unique track count
            cursor.execute("""
                SELECT COUNT(DISTINCT track_id)
                FROM music_events
                WHERE user_id = ?
            """, (user_id,))
            
            unique_tracks = cursor.fetchone()[0] or 0
            
            # Get unique artist count
            cursor.execute("""
                SELECT COUNT(DISTINCT mps.artist)
                FROM music_events me
                JOIN music_playback_state mps ON me.track_id = mps.track_id
                WHERE me.user_id = ? AND mps.artist IS NOT NULL
            """, (user_id,))
            
            unique_artists = cursor.fetchone()[0] or 0
            
            conn.close()
            
            return {
                "total_plays": event_counts.get("play_end", 0),
                "total_skips": event_counts.get("skip", 0),
                "total_likes": event_counts.get("like", 0),
                "total_repeats": event_counts.get("repeat", 0),
                "total_listening_ms": total_ms,
                "total_listening_hours": round(total_ms / (1000 * 60 * 60), 1) if total_ms else 0,
                "unique_tracks": unique_tracks,
                "unique_artists": unique_artists,
                "skip_rate": round(
                    event_counts.get("skip", 0) / 
                    max(event_counts.get("play_end", 0) + event_counts.get("skip", 0), 1) * 100, 1
                ),
            }
            
        except Exception as e:
            logger.error(f"Failed to get listening stats: {e}")
            return {
                "total_plays": 0,
                "total_skips": 0,
                "total_likes": 0,
                "total_repeats": 0,
                "total_listening_ms": 0,
                "total_listening_hours": 0,
                "unique_tracks": 0,
                "unique_artists": 0,
                "skip_rate": 0,
            }
    
    def rank_tracks_by_affinity(
        self,
        tracks: List[Dict],
        user_id: str,
        exploration_bonus: float = 0.5
    ) -> List[Dict]:
        """
        Rank a list of tracks by user affinity.
        
        Args:
            tracks: List of track dicts (must have 'videoId' or 'id')
            user_id: User identifier
            exploration_bonus: Score to add for unplayed tracks (encourages discovery)
            
        Returns:
            Tracks sorted by affinity score (highest first)
        """
        scored_tracks = []
        
        for track in tracks:
            track_id = track.get("videoId") or track.get("id")
            if not track_id:
                continue
            
            affinity = self.calculate_track_affinity(user_id, track_id)
            
            # Add exploration bonus for unplayed tracks
            if affinity == 0:
                affinity = exploration_bonus
            
            scored_tracks.append((track, affinity))
        
        # Sort by affinity descending
        scored_tracks.sort(key=lambda x: x[1], reverse=True)
        
        return [t[0] for t in scored_tracks]


# Singleton instance
_affinity_engine: Optional[AffinityEngine] = None


def get_affinity_engine() -> AffinityEngine:
    """Get the singleton affinity engine instance."""
    global _affinity_engine
    if _affinity_engine is None:
        _affinity_engine = AffinityEngine()
    return _affinity_engine

