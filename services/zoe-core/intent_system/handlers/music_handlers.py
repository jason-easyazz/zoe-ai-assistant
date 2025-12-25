"""
Music Intent Handlers
=====================

Handles voice commands for music playback:
- MusicPlay: Search and play music
- MusicPause/Resume: Control playback
- MusicSkip/Previous: Navigate tracks
- MusicVolume: Adjust volume
- MusicQueue: View/manage queue
- MusicNowPlaying: What's currently playing
"""

import logging
from typing import Dict, Any

from intent_system.classifiers import ZoeIntent

logger = logging.getLogger(__name__)


def _get_services():
    """Lazy load music services."""
    try:
        from services.music import get_youtube_music, get_media_controller
        return get_youtube_music(), get_media_controller()
    except Exception as e:
        logger.error(f"Failed to load music services: {e}")
        return None, None


async def handle_music_play(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicPlay intent - search and play music.
    
    Slots:
        - query: General search term
        - artist: Specific artist
        - song: Specific song title
        - genre: Music genre
    """
    youtube, controller = _get_services()
    
    if not controller:
        return {
            "success": False,
            "message": "Music service is not available right now."
        }
    
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
    
    device_id = context.get("device_id")
    
    try:
        # Search for tracks
        results = await youtube.search(search_query, user_id, "songs", limit=5)
        
        if not results:
            return {
                "success": False,
                "message": f"I couldn't find any music matching '{query}'."
            }
        
        # Play the first result
        track = results[0]
        track_id = track.get("videoId") or track.get("id")
        
        result = await controller.play(track_id, device_id, user_id, track)
        
        if result.get("success"):
            title = track.get("title", "Unknown")
            artist_name = track.get("artist", "Unknown Artist")
            
            return {
                "success": True,
                "message": f"🎵 Playing {title} by {artist_name}",
                "data": {
                    "track_id": track_id,
                    "title": title,
                    "artist": artist_name,
                    "played_on": result.get("played_on")
                }
            }
        else:
            return {
                "success": False,
                "message": "Sorry, I couldn't start playback. Please try again."
            }
            
    except Exception as e:
        logger.error(f"Music play failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Something went wrong trying to play music."
        }


async def handle_music_pause(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicPause intent - pause playback."""
    _, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        result = await controller.pause(user_id, device_id)
        return {
            "success": True,
            "message": "⏸️ Paused."
        }
    except Exception as e:
        logger.error(f"Pause failed: {e}")
        return {"success": False, "message": "Couldn't pause playback."}


async def handle_music_resume(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicResume intent - resume playback."""
    _, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        result = await controller.resume(user_id, device_id)
        return {
            "success": True,
            "message": "▶️ Resuming playback."
        }
    except Exception as e:
        logger.error(f"Resume failed: {e}")
        return {"success": False, "message": "Couldn't resume playback."}


async def handle_music_skip(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicSkip intent - skip to next track."""
    _, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        result = await controller.skip(user_id, device_id)
        
        if result.get("queue_empty"):
            return {
                "success": True,
                "message": "⏭️ Skipped. No more tracks in queue."
            }
        
        return {
            "success": True,
            "message": "⏭️ Skipping to next track."
        }
    except Exception as e:
        logger.error(f"Skip failed: {e}")
        return {"success": False, "message": "Couldn't skip track."}


async def handle_music_previous(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicPrevious intent - play previous track."""
    _, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        result = await controller.previous(user_id, device_id)
        
        if not result.get("success"):
            return {
                "success": False,
                "message": "No previous track to play."
            }
        
        return {
            "success": True,
            "message": "⏮️ Playing previous track."
        }
    except Exception as e:
        logger.error(f"Previous failed: {e}")
        return {"success": False, "message": "Couldn't go to previous track."}


async def handle_music_volume(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicVolume intent - adjust volume.
    
    Slots:
        - level: Numeric volume (0-100) or relative (up, down, louder, quieter)
    """
    _, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    level = intent.slots.get("level")
    
    # Handle relative volume
    if level is None:
        # Check raw text for relative commands
        raw = intent.raw_text.lower() if intent.raw_text else ""
        
        if any(word in raw for word in ["up", "louder", "raise"]):
            # Get current volume and increase
            state = await controller.get_state(user_id, device_id)
            current = state.get("volume", 50) if state else 50
            level = min(100, current + 20)
        elif any(word in raw for word in ["down", "quieter", "lower", "softer"]):
            state = await controller.get_state(user_id, device_id)
            current = state.get("volume", 50) if state else 50
            level = max(0, current - 20)
        else:
            return {
                "success": False,
                "message": "What volume level? (0-100, or say 'louder' / 'quieter')"
            }
    else:
        try:
            level = int(level)
            level = max(0, min(100, level))
        except (ValueError, TypeError):
            return {
                "success": False,
                "message": f"I didn't understand the volume level '{level}'."
            }
    
    try:
        result = await controller.set_volume(level, device_id, user_id)
        return {
            "success": True,
            "message": f"🔊 Volume set to {level}%"
        }
    except Exception as e:
        logger.error(f"Volume failed: {e}")
        return {"success": False, "message": "Couldn't adjust volume."}


async def handle_music_queue(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicQueue intent - show current queue."""
    _, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        queue = await controller.get_queue(user_id, device_id)
        
        if not queue:
            return {
                "success": True,
                "message": "📋 The queue is empty."
            }
        
        # Format queue list
        queue_text = "📋 Up next:\n"
        for i, track in enumerate(queue[:5], 1):
            title = track.get("track_title", "Unknown")
            artist = track.get("artist", "")
            queue_text += f"{i}. {title}"
            if artist:
                queue_text += f" - {artist}"
            queue_text += "\n"
        
        if len(queue) > 5:
            queue_text += f"... and {len(queue) - 5} more"
        
        return {
            "success": True,
            "message": queue_text.strip(),
            "data": {"queue": queue}
        }
    except Exception as e:
        logger.error(f"Get queue failed: {e}")
        return {"success": False, "message": "Couldn't get the queue."}


async def handle_music_queue_add(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicQueueAdd intent - add track to queue."""
    youtube, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    query = intent.slots.get("query")
    
    if not query:
        return {
            "success": False,
            "message": "What would you like to add to the queue?"
        }
    
    device_id = context.get("device_id")
    
    try:
        # Search for the track
        results = await youtube.search(query, user_id, "songs", limit=1)
        
        if not results:
            return {
                "success": False,
                "message": f"I couldn't find '{query}'."
            }
        
        track = results[0]
        track_id = track.get("videoId") or track.get("id")
        
        result = await controller.add_to_queue(user_id, track_id, track, device_id)
        
        if result.get("success"):
            title = track.get("title", "Unknown")
            return {
                "success": True,
                "message": f"➕ Added {title} to the queue."
            }
        else:
            return {
                "success": False,
                "message": "Couldn't add to queue."
            }
            
    except Exception as e:
        logger.error(f"Queue add failed: {e}")
        return {"success": False, "message": "Couldn't add to queue."}


async def handle_music_now_playing(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicNowPlaying intent - what's currently playing."""
    _, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        state = await controller.get_state(user_id, device_id)
        
        if not state or not state.get("track_id"):
            return {
                "success": True,
                "message": "Nothing is playing right now."
            }
        
        title = state.get("track_title", "Unknown")
        artist = state.get("artist", "Unknown Artist")
        album = state.get("album", "")
        is_playing = state.get("is_playing", False)
        
        status = "🎵 Now playing" if is_playing else "⏸️ Paused"
        message = f"{status}: {title} by {artist}"
        if album:
            message += f" ({album})"
        
        return {
            "success": True,
            "message": message,
            "data": state
        }
    except Exception as e:
        logger.error(f"Now playing failed: {e}")
        return {"success": False, "message": "Couldn't get current track info."}


async def handle_music_search(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicSearch intent - search without playing."""
    youtube, _ = _get_services()
    
    if not youtube:
        return {"success": False, "message": "Music service unavailable."}
    
    query = intent.slots.get("query")
    
    if not query:
        return {
            "success": False,
            "message": "What would you like me to search for?"
        }
    
    try:
        results = await youtube.search(query, user_id, "songs", limit=5)
        
        if not results:
            return {
                "success": True,
                "message": f"No results found for '{query}'."
            }
        
        # Format results
        message = f"🔍 Results for '{query}':\n"
        for i, track in enumerate(results, 1):
            title = track.get("title", "Unknown")
            artist = track.get("artist", "")
            message += f"{i}. {title}"
            if artist:
                message += f" - {artist}"
            message += "\n"
        
        return {
            "success": True,
            "message": message.strip(),
            "data": {"results": results}
        }
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return {"success": False, "message": "Search failed."}


# ============================================================
# Recommendation Handlers
# ============================================================

def _get_recommendation_engine():
    """Lazy load recommendation engine."""
    try:
        from services.music.recommendation_engine import get_recommendation_engine
        return get_recommendation_engine()
    except Exception as e:
        logger.error(f"Failed to load recommendation engine: {e}")
        return None


async def handle_music_similar(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicSimilar intent - play tracks similar to current track.
    """
    _, controller = _get_services()
    engine = _get_recommendation_engine()
    
    if not controller or not engine:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        # Get current track
        state = await controller.get_state(user_id, device_id)
        
        if not state or not state.get("track_id"):
            return {
                "success": False,
                "message": "Play something first, then I can find similar tracks."
            }
        
        current_track_id = state["track_id"]
        current_title = state.get("track_title", "this")
        
        # Get similar tracks
        similar = await engine.get_similar(current_track_id, user_id, limit=20)
        
        if not similar:
            return {
                "success": False,
                "message": f"I couldn't find tracks similar to {current_title}."
            }
        
        # Add all to queue and play first
        first_track = similar[0]
        first_id = first_track.get("videoId") or first_track.get("id")
        
        # Play the first similar track
        result = await controller.play(first_id, device_id, user_id, first_track)
        
        # Queue the rest
        for track in similar[1:10]:
            track_id = track.get("videoId") or track.get("id")
            await controller.add_to_queue(user_id, track_id, track, device_id)
        
        if result.get("success"):
            return {
                "success": True,
                "message": f"🎵 Playing similar tracks to {current_title}. {len(similar)} tracks queued.",
                "data": {"similar_count": len(similar)}
            }
        else:
            return {
                "success": False,
                "message": "Found similar tracks but couldn't start playback."
            }
            
    except Exception as e:
        logger.error(f"Music similar failed: {e}", exc_info=True)
        return {"success": False, "message": "Couldn't find similar tracks."}


async def handle_music_radio(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicRadio intent - generate personal radio.
    """
    _, controller = _get_services()
    engine = _get_recommendation_engine()
    
    if not controller or not engine:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        # Get current track as optional seed
        state = await controller.get_state(user_id, device_id)
        seed_track = state.get("track_id") if state else None
        
        # Generate radio
        tracks = await engine.get_radio(user_id, seed_track, limit=25)
        
        if not tracks:
            return {
                "success": False,
                "message": "I couldn't generate your radio. Try listening to some music first!"
            }
        
        # Play first track
        first_track = tracks[0]
        first_id = first_track.get("videoId") or first_track.get("id")
        
        result = await controller.play(first_id, device_id, user_id, first_track)
        
        # Queue the rest
        for track in tracks[1:]:
            track_id = track.get("videoId") or track.get("id")
            await controller.add_to_queue(user_id, track_id, track, device_id)
        
        if result.get("success"):
            return {
                "success": True,
                "message": f"📻 Starting your personal radio! {len(tracks)} tracks queued based on your taste.",
                "data": {"track_count": len(tracks)}
            }
        else:
            return {
                "success": False,
                "message": "Radio generated but couldn't start playback."
            }
            
    except Exception as e:
        logger.error(f"Music radio failed: {e}", exc_info=True)
        return {"success": False, "message": "Couldn't start radio."}


async def handle_music_discover(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicDiscover intent - find new music matching taste.
    """
    _, controller = _get_services()
    engine = _get_recommendation_engine()
    
    if not controller or not engine:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        # Get discovery mix
        tracks = await engine.get_discover(user_id, limit=20)
        
        if not tracks:
            return {
                "success": False,
                "message": "I need to learn your taste first. Listen to some music and I'll find new stuff for you!"
            }
        
        # Play first track
        first_track = tracks[0]
        first_id = first_track.get("videoId") or first_track.get("id")
        
        result = await controller.play(first_id, device_id, user_id, first_track)
        
        # Queue the rest
        for track in tracks[1:]:
            track_id = track.get("videoId") or track.get("id")
            await controller.add_to_queue(user_id, track_id, track, device_id)
        
        if result.get("success"):
            return {
                "success": True,
                "message": f"🎧 Discovering new music for you! {len(tracks)} fresh tracks queued.",
                "data": {"track_count": len(tracks)}
            }
        else:
            return {
                "success": False,
                "message": "Found new music but couldn't start playback."
            }
            
    except Exception as e:
        logger.error(f"Music discover failed: {e}", exc_info=True)
        return {"success": False, "message": "Couldn't find new music."}


async def handle_music_mood(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicMood intent - match current listening mood.
    """
    _, controller = _get_services()
    engine = _get_recommendation_engine()
    
    if not controller or not engine:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        # Get mood-matched tracks
        tracks = await engine.get_mood_match(user_id, limit=20)
        
        if not tracks:
            return {
                "success": False,
                "message": "Play something first so I can match your mood."
            }
        
        # Play first track
        first_track = tracks[0]
        first_id = first_track.get("videoId") or first_track.get("id")
        
        result = await controller.play(first_id, device_id, user_id, first_track)
        
        # Queue the rest
        for track in tracks[1:]:
            track_id = track.get("videoId") or track.get("id")
            await controller.add_to_queue(user_id, track_id, track, device_id)
        
        if result.get("success"):
            return {
                "success": True,
                "message": f"🎭 Matching your vibe! {len(tracks)} tracks that fit the mood.",
                "data": {"track_count": len(tracks)}
            }
        else:
            return {
                "success": False,
                "message": "Found mood matches but couldn't start playback."
            }
            
    except Exception as e:
        logger.error(f"Music mood failed: {e}", exc_info=True)
        return {"success": False, "message": "Couldn't match your mood."}


async def handle_music_like(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicLike intent - like current track.
    """
    _, controller = _get_services()
    
    if not controller:
        return {"success": False, "message": "Music service unavailable."}
    
    device_id = context.get("device_id")
    
    try:
        # Get current track
        state = await controller.get_state(user_id, device_id)
        
        if not state or not state.get("track_id"):
            return {
                "success": False,
                "message": "Nothing is playing to like."
            }
        
        track_id = state["track_id"]
        title = state.get("track_title", "this track")
        
        # Track the like event
        from services.music.event_tracker import get_event_tracker
        tracker = get_event_tracker()
        await tracker.track_like(user_id, track_id, device_id)
        
        return {
            "success": True,
            "message": f"❤️ Liked {title}! I'll remember your taste."
        }
        
    except Exception as e:
        logger.error(f"Music like failed: {e}", exc_info=True)
        return {"success": False, "message": "Couldn't like the track."}


async def handle_music_stats(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle MusicStats intent - show listening statistics.
    """
    try:
        from services.music.affinity_engine import get_affinity_engine
        
        affinity = get_affinity_engine()
        stats = affinity.get_listening_stats(user_id)
        
        if stats.get("total_plays", 0) == 0:
            return {
                "success": True,
                "message": "You haven't listened to much yet. Start playing some music!"
            }
        
        # Get top artists
        top_artists = affinity.get_top_artists(user_id, limit=3)
        
        # Build message
        message = f"📊 Your Music Stats:\n"
        message += f"• {stats['total_plays']} songs played\n"
        message += f"• {stats['total_listening_hours']} hours of listening\n"
        message += f"• {stats['unique_artists']} different artists\n"
        message += f"• {stats['skip_rate']}% skip rate\n"
        
        if top_artists:
            message += "\n🎤 Top Artists:\n"
            for i, (artist, score) in enumerate(top_artists, 1):
                message += f"  {i}. {artist}\n"
        
        return {
            "success": True,
            "message": message.strip(),
            "data": stats
        }
        
    except Exception as e:
        logger.error(f"Music stats failed: {e}", exc_info=True)
        return {"success": False, "message": "Couldn't get your stats."}

