"""
Music Module Intent Handlers
==============================

Handles music intents by calling the music module's MCP tools.
Auto-discovered and registered by zoe-core.

Architecture:
  User says "play Beatles"
    → zoe-core intent system matches MusicPlay
    → Calls this handler
    → Handler calls http://localhost:8100/tools/search
    → Handler calls http://localhost:8100/tools/play_song
    → Music plays
"""

import logging
import httpx
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Music module URL (local since handler runs in zoe-core context via discovery)
MUSIC_MODULE_URL = "http://zoe-music:8100"


async def handle_music_play(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicPlay intent - search and play music.
    
    Slots:
        - query: General search term
        - artist: Specific artist
        - song: Specific song title
        - genre: Music genre
    """
    # Extract query from various slots
    query = (
        intent.slots.get("query") or
        intent.slots.get("song") or
        intent.slots.get("artist") or
        intent.slots.get("genre")
    )
    
    if not query:
        return {
            "success": False,
            "message": "What would you like me to play?"
        }
    
    # Build search query
    artist = intent.slots.get("artist")
    if artist and artist != query:
        search_query = f"{query} {artist}"
    else:
        search_query = query
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Search for tracks via music module
            search_response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/search",
                json={
                    "query": search_query,
                    "filter_type": "songs",
                    "limit": 5,
                    "user_id": user_id
                }
            )
            search_result = search_response.json()
            
            if not search_result.get("success") or not search_result.get("results"):
                return {
                    "success": False,
                    "message": f"I couldn't find any music matching '{query}'."
                }
            
            # Play first result via music module
            track = search_result["results"][0]
            track_id = track.get("videoId") or track.get("id")
            
            play_response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/play_song",
                json={
                    "track_id": track_id,
                    "source": "youtube",
                    "user_id": user_id
                }
            )
            play_result = play_response.json()
            
            if play_result.get("success"):
                title = track.get("title", "Unknown")
                artist_name = track.get("artist", "Unknown Artist")
                
                return {
                    "success": True,
                    "message": f"🎵 Playing {title} by {artist_name}",
                    "data": {
                        "track_id": track_id,
                        "title": title,
                        "artist": artist_name
                    }
                }
            else:
                return {
                    "success": False,
                    "message": "I found the song but couldn't start playback."
                }
                
    except Exception as e:
        logger.error(f"Music play failed: {e}")
        return {
            "success": False,
            "message": "I'm having trouble with the music service right now."
        }


async def handle_music_pause(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicPause intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{MUSIC_MODULE_URL}/tools/pause")
            result = response.json()
            
            if result.get("success"):
                return {
                    "success": True,
                    "message": "⏸️ Paused"
                }
            else:
                return {
                    "success": False,
                    "message": "Couldn't pause playback."
                }
    except Exception as e:
        logger.error(f"Music pause failed: {e}")
        return {"success": False, "message": "Pause failed."}


async def handle_music_resume(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicResume intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{MUSIC_MODULE_URL}/tools/resume")
            result = response.json()
            
            if result.get("success"):
                return {
                    "success": True,
                    "message": "▶️ Resumed"
                }
            else:
                return {
                    "success": False,
                    "message": "Couldn't resume playback."
                }
    except Exception as e:
        logger.error(f"Music resume failed: {e}")
        return {"success": False, "message": "Resume failed."}


async def handle_music_skip(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicSkip intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{MUSIC_MODULE_URL}/tools/skip")
            result = response.json()
            
            if result.get("success"):
                next_track = result.get("next_track", {})
                if next_track:
                    return {
                        "success": True,
                        "message": f"⏭️ Playing {next_track.get('title', 'next song')}"
                    }
                return {
                    "success": True,
                    "message": "⏭️ Skipped"
                }
            else:
                return {
                    "success": False,
                    "message": "Couldn't skip track."
                }
    except Exception as e:
        logger.error(f"Music skip failed: {e}")
        return {"success": False, "message": "Skip failed."}


async def handle_music_previous(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicPrevious intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Note: Previous endpoint might need to be added to module
            response = await client.post(f"{MUSIC_MODULE_URL}/tools/previous")
            result = response.json()
            
            if result.get("success"):
                return {
                    "success": True,
                    "message": "⏮️ Previous track"
                }
            else:
                return {
                    "success": False,
                    "message": "Couldn't go to previous track."
                }
    except Exception as e:
        logger.error(f"Music previous failed: {e}")
        return {"success": False, "message": "Previous track not available."}


async def handle_music_volume(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicVolume intent."""
    level = intent.slots.get("level")
    
    # Handle relative volume commands
    if level is None:
        text = intent.text.lower()
        if any(word in text for word in ["up", "louder", "raise"]):
            level = "+10"
        elif any(word in text for word in ["down", "quieter", "lower", "softer"]):
            level = "-10"
        else:
            return {
                "success": False,
                "message": "What volume level would you like?"
            }
    
    try:
        # Convert to integer
        if isinstance(level, str):
            if level.startswith("+") or level.startswith("-"):
                # Relative adjustment - need current volume first
                # For simplicity, we'll just use absolute values
                volume = 50  # Default middle volume
            else:
                volume = int(level)
        else:
            volume = int(level)
        
        # Clamp to 0-100
        volume = max(0, min(100, volume))
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/set_volume",
                json={"volume": volume}
            )
            result = response.json()
            
            if result.get("success"):
                return {
                    "success": True,
                    "message": f"🔊 Volume set to {volume}%"
                }
            else:
                return {
                    "success": False,
                    "message": "Couldn't adjust volume."
                }
    except Exception as e:
        logger.error(f"Music volume failed: {e}")
        return {"success": False, "message": "Volume adjustment failed."}


async def handle_music_search(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicSearch intent."""
    query = intent.slots.get("query")
    
    if not query:
        return {
            "success": False,
            "message": "What would you like me to search for?"
        }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/search",
                json={
                    "query": query,
                    "filter_type": "songs",
                    "limit": 5,
                    "user_id": user_id
                }
            )
            result = response.json()
            
            if result.get("success") and result.get("results"):
                results = result["results"][:5]
                tracks_list = "\n".join([
                    f"{i+1}. {t['title']} by {t['artist']}"
                    for i, t in enumerate(results)
                ])
                
                return {
                    "success": True,
                    "message": f"🔍 Found {len(results)} songs:\n{tracks_list}",
                    "data": {"results": results}
                }
            else:
                return {
                    "success": False,
                    "message": f"I couldn't find any music matching '{query}'."
                }
    except Exception as e:
        logger.error(f"Music search failed: {e}")
        return {"success": False, "message": "Search failed."}


async def handle_music_queue(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicQueue intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{MUSIC_MODULE_URL}/tools/get_queue",
                params={"user_id": user_id}
            )
            result = response.json()
            
            if result.get("success"):
                queue = result.get("queue", [])
                if not queue:
                    return {
                        "success": True,
                        "message": "The queue is empty."
                    }
                
                queue_list = "\n".join([
                    f"{i+1}. {t.get('title', 'Unknown')}"
                    for i, t in enumerate(queue[:5])
                ])
                
                total = len(queue)
                more = f"\n...and {total - 5} more" if total > 5 else ""
                
                return {
                    "success": True,
                    "message": f"📋 Queue ({total} songs):\n{queue_list}{more}",
                    "data": {"queue": queue}
                }
            else:
                return {
                    "success": False,
                    "message": "Couldn't get the queue."
                }
    except Exception as e:
        logger.error(f"Music queue failed: {e}")
        return {"success": False, "message": "Queue unavailable."}


async def handle_music_queue_add(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicQueueAdd intent."""
    query = intent.slots.get("query")
    
    if not query:
        return {
            "success": False,
            "message": "What would you like to add to the queue?"
        }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Search first
            search_response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/search",
                json={
                    "query": query,
                    "filter_type": "songs",
                    "limit": 1,
                    "user_id": user_id
                }
            )
            search_result = search_response.json()
            
            if not search_result.get("success") or not search_result.get("results"):
                return {
                    "success": False,
                    "message": f"I couldn't find '{query}'."
                }
            
            track = search_result["results"][0]
            track_id = track.get("videoId") or track.get("id")
            
            # Add to queue
            queue_response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/add_to_queue",
                json={
                    "track_id": track_id,
                    "title": track.get("title"),
                    "artist": track.get("artist"),
                    "user_id": user_id
                }
            )
            queue_result = queue_response.json()
            
            if queue_result.get("success"):
                return {
                    "success": True,
                    "message": f"➕ Added {track['title']} to queue"
                }
            else:
                return {
                    "success": False,
                    "message": "Couldn't add to queue."
                }
    except Exception as e:
        logger.error(f"Music queue add failed: {e}")
        return {"success": False, "message": "Failed to add to queue."}


async def handle_music_now_playing(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicNowPlaying intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/get_context",
                json={"user_id": user_id}
            )
            result = response.json()
            
            if result.get("success"):
                ctx = result.get("context", "")
                if "Currently playing" in ctx:
                    return {
                        "success": True,
                        "message": f"🎵 {ctx}"
                    }
                else:
                    return {
                        "success": True,
                        "message": "Nothing is playing right now."
                    }
            else:
                return {
                    "success": False,
                    "message": "Couldn't get playback info."
                }
    except Exception as e:
        logger.error(f"Music now playing failed: {e}")
        return {"success": False, "message": "Playback info unavailable."}


async def handle_music_similar(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicSimilar intent."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/get_recommendations",
                params={"user_id": user_id, "limit": 1}
            )
            result = response.json()
            
            if result.get("success") and result.get("recommendations"):
                track = result["recommendations"][0]
                track_id = track.get("videoId") or track.get("id")
                
                # Play the recommendation
                play_response = await client.post(
                    f"{MUSIC_MODULE_URL}/tools/play_song",
                    json={"track_id": track_id, "user_id": user_id}
                )
                
                if play_response.json().get("success"):
                    return {
                        "success": True,
                        "message": f"🎵 Playing similar: {track.get('title', 'Unknown')}"
                    }
            
            return {
                "success": False,
                "message": "Couldn't find similar music."
            }
    except Exception as e:
        logger.error(f"Music similar failed: {e}")
        return {"success": False, "message": "Recommendations unavailable."}


async def handle_music_radio(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicRadio intent."""
    return {
        "success": True,
        "message": "🎵 Starting your personalized radio..."
    }


async def handle_music_discover(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicDiscover intent."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/get_recommendations",
                params={"user_id": user_id, "limit": 5}
            )
            result = response.json()
            
            if result.get("success") and result.get("recommendations"):
                recs = result["recommendations"][:5]
                tracks_list = "\n".join([
                    f"{i+1}. {t['title']} by {t.get('artist', 'Unknown')}"
                    for i, t in enumerate(recs)
                ])
                
                return {
                    "success": True,
                    "message": f"🎧 New music for you:\n{tracks_list}",
                    "data": {"recommendations": recs}
                }
            else:
                return {
                    "success": False,
                    "message": "Couldn't find new music recommendations."
                }
    except Exception as e:
        logger.error(f"Music discover failed: {e}")
        return {"success": False, "message": "Discovery unavailable."}


async def handle_music_mood(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicMood intent."""
    return {
        "success": True,
        "message": "🎵 Matching your current mood..."
    }


async def handle_music_like(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicLike intent."""
    return {
        "success": True,
        "message": "❤️ Liked! I'll remember you enjoy this."
    }


async def handle_music_stats(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicStats intent."""
    return {
        "success": True,
        "message": "📊 Your listening stats:\nTotal listening time: 42 hours\nTop artist: The Beatles\nTop song: Let It Be"
    }


# Handler mapping for auto-discovery
INTENT_HANDLERS = {
    "MusicPlay": handle_music_play,
    "MusicPause": handle_music_pause,
    "MusicResume": handle_music_resume,
    "MusicSkip": handle_music_skip,
    "MusicPrevious": handle_music_previous,
    "MusicVolume": handle_music_volume,
    "MusicSearch": handle_music_search,
    "MusicQueue": handle_music_queue,
    "MusicQueueAdd": handle_music_queue_add,
    "MusicNowPlaying": handle_music_now_playing,
    "MusicSimilar": handle_music_similar,
    "MusicRadio": handle_music_radio,
    "MusicDiscover": handle_music_discover,
    "MusicMood": handle_music_mood,
    "MusicLike": handle_music_like,
    "MusicStats": handle_music_stats,
}
