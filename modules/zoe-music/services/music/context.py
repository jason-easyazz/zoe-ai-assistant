"""
Music Context Provider
======================

Provides music context for Zoe's user context system.
Integrates playback state, listening history, and preferences
into the main chat context.
"""

import sqlite3
import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


async def get_music_context(user_id: str) -> Dict[str, Any]:
    """
    Get comprehensive music context for user.
    
    Returns:
        Dict with:
        - current_track: Currently playing track (if any)
        - is_playing: Whether music is currently playing
        - recent_tracks: Last 5 tracks played
        - top_artists: Top 3 artists by affinity score
        - listening_stats: Basic stats (total tracks, hours, etc.)
    """
    context = {
        "current_track": None,
        "is_playing": False,
        "recent_tracks": [],
        "top_artists": [],
        "listening_stats": {}
    }
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get current playback state
        cursor.execute("""
            SELECT track_id, track_title, artist, album, album_art_url,
                   position_ms, duration_ms, is_playing, updated_at
            FROM music_playback_state
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
        """, (user_id,))
        
        row = cursor.fetchone()
        if row:
            context["is_playing"] = bool(row["is_playing"])
            if row["track_title"]:
                context["current_track"] = {
                    "id": row["track_id"],
                    "title": row["track_title"],
                    "artist": row["artist"],
                    "album": row["album"],
                    "album_art": row["album_art_url"],
                    "position_ms": row["position_ms"],
                    "duration_ms": row["duration_ms"]
                }
        
        # Get recent tracks (last 5 unique tracks)
        cursor.execute("""
            SELECT DISTINCT 
                me.track_id,
                mps.track_title,
                mps.artist,
                me.timestamp
            FROM music_events me
            LEFT JOIN music_playback_state mps ON me.track_id = mps.track_id
            WHERE me.user_id = ? AND me.event_type IN ('play_start', 'play_end')
            ORDER BY me.timestamp DESC
            LIMIT 5
        """, (user_id,))
        
        context["recent_tracks"] = [
            {
                "id": row["track_id"],
                "title": row["track_title"] or "Unknown",
                "artist": row["artist"] or "Unknown"
            }
            for row in cursor.fetchall()
        ]
        
        # Get top artists (by event count with positive weighting)
        cursor.execute("""
            SELECT mps.artist, COUNT(*) as play_count
            FROM music_events me
            JOIN music_playback_state mps ON me.track_id = mps.track_id
            WHERE me.user_id = ? 
              AND me.event_type IN ('play_end', 'like', 'queue_add')
              AND mps.artist IS NOT NULL
              AND mps.artist != ''
            GROUP BY mps.artist
            ORDER BY play_count DESC
            LIMIT 3
        """, (user_id,))
        
        context["top_artists"] = [
            {"name": row["artist"], "play_count": row["play_count"]}
            for row in cursor.fetchall()
        ]
        
        # Get listening stats
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT track_id) as unique_tracks,
                COUNT(*) as total_events,
                SUM(CASE WHEN event_type = 'play_end' THEN 1 ELSE 0 END) as completed_plays,
                SUM(CASE WHEN event_type = 'skip' THEN 1 ELSE 0 END) as skips
            FROM music_events
            WHERE user_id = ?
        """, (user_id,))
        
        stats_row = cursor.fetchone()
        if stats_row:
            context["listening_stats"] = {
                "unique_tracks": stats_row["unique_tracks"] or 0,
                "completed_plays": stats_row["completed_plays"] or 0,
                "skips": stats_row["skips"] or 0
            }
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Failed to get music context for {user_id}: {e}")
    
    return context


def format_music_for_prompt(music_context: Dict[str, Any]) -> str:
    """
    Format music context for inclusion in system prompt.
    
    Returns a concise string describing current music state.
    """
    if not music_context:
        return ""
    
    parts = []
    
    # Current track
    current = music_context.get("current_track")
    is_playing = music_context.get("is_playing", False)
    
    if current and is_playing:
        parts.append(f"🎵 Currently playing: '{current['title']}' by {current['artist']}")
    elif current:
        parts.append(f"🎵 Last played: '{current['title']}' by {current['artist']} (paused)")
    
    # Top artists (if no current track, mention preferences)
    top_artists = music_context.get("top_artists", [])
    if top_artists and not current:
        artist_names = ", ".join(a["name"] for a in top_artists[:3])
        parts.append(f"🎧 Favorite artists: {artist_names}")
    
    if not parts:
        return ""
    
    return "\n".join(parts)

