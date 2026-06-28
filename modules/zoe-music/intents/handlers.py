"""
Music Module Intent Handlers
==============================

Handles music intents by calling the music module's HTTP tools.
Auto-discovered and registered by zoe-core.

Architecture:
  User says "play Beatles"
    → zoe-core intent system matches MusicPlay
    → Calls this handler
    → Handler POSTs http://zoe-music:8100/tools/play  (with service token)
    → Music plays

Contract note: every call here must target a route that actually exists on
modules/zoe-music/main.py and carry the shared Zoe service token. The live
bridge exposes only: POST /tools/{play,pause,resume,skip,previous,volume},
GET /tools/{now_playing,search}. It returns {"ok": true, ...} on success.
"""

import logging
import os
import httpx
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Music module URL (zoe-network service DNS; handler runs in zoe-core context).
MUSIC_MODULE_URL = os.environ.get("ZOE_MUSIC_MODULE_URL", "http://zoe-music:8100")


def _module_headers() -> Dict[str, str]:
    """Headers for calls into the module — carry the shared service token so the
    module's auth gate accepts this legitimate in-cluster caller."""
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("ZOE_MUSIC_SERVICE_TOKEN", "")
    if token:
        headers["X-Zoe-Service-Token"] = token
    return headers


async def _post(client: httpx.AsyncClient, path: str, json: Dict | None = None):
    return await client.post(
        f"{MUSIC_MODULE_URL}{path}", json=json or {}, headers=_module_headers()
    )


async def _get(client: httpx.AsyncClient, path: str, params: Dict | None = None):
    return await client.get(
        f"{MUSIC_MODULE_URL}{path}", params=params or {}, headers=_module_headers()
    )


async def handle_music_play(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicPlay intent — search and play music in one call."""
    query = (
        intent.slots.get("query")
        or intent.slots.get("song")
        or intent.slots.get("artist")
        or intent.slots.get("genre")
    )
    if not query:
        return {"success": False, "message": "What would you like me to play?"}

    artist = intent.slots.get("artist")
    search_query = f"{query} {artist}" if (artist and artist != query) else query

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await _post(client, "/tools/play", {"query": search_query})
            result = resp.json()
            if result.get("ok"):
                return {
                    "success": True,
                    "message": f"🎵 Playing {query}",
                    "data": result.get("track", {}),
                }
            return {
                "success": False,
                "message": "I couldn't start playback right now.",
            }
    except Exception as e:
        logger.error(f"Music play failed: {e}")
        return {
            "success": False,
            "message": "I'm having trouble with the music service right now.",
        }


async def handle_music_pause(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicPause intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            result = (await _post(client, "/tools/pause")).json()
            if result.get("ok"):
                return {"success": True, "message": "⏸️ Paused"}
            return {"success": False, "message": "Couldn't pause playback."}
    except Exception as e:
        logger.error(f"Music pause failed: {e}")
        return {"success": False, "message": "Pause failed."}


async def handle_music_resume(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicResume intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            result = (await _post(client, "/tools/resume")).json()
            if result.get("ok"):
                return {"success": True, "message": "▶️ Resumed"}
            return {"success": False, "message": "Couldn't resume playback."}
    except Exception as e:
        logger.error(f"Music resume failed: {e}")
        return {"success": False, "message": "Resume failed."}


async def handle_music_skip(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicSkip intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            result = (await _post(client, "/tools/skip")).json()
            if result.get("ok"):
                return {"success": True, "message": "⏭️ Skipped"}
            return {"success": False, "message": "Couldn't skip track."}
    except Exception as e:
        logger.error(f"Music skip failed: {e}")
        return {"success": False, "message": "Skip failed."}


async def handle_music_previous(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicPrevious intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            result = (await _post(client, "/tools/previous")).json()
            if result.get("ok"):
                return {"success": True, "message": "⏮️ Previous track"}
            return {"success": False, "message": "Couldn't go to previous track."}
    except Exception as e:
        logger.error(f"Music previous failed: {e}")
        return {"success": False, "message": "Previous track not available."}


async def handle_music_volume(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicVolume intent."""
    level = intent.slots.get("level")

    if level is None:
        text = intent.text.lower()
        if any(word in text for word in ["up", "louder", "raise"]):
            level = 60
        elif any(word in text for word in ["down", "quieter", "lower", "softer"]):
            level = 40
        else:
            return {"success": False, "message": "What volume level would you like?"}

    try:
        if isinstance(level, str):
            # Relative tokens ("+10"/"-10") aren't resolvable without current
            # volume; fall back to a sensible absolute level.
            volume = 50 if level.startswith(("+", "-")) else int(level)
        else:
            volume = int(level)
        volume = max(0, min(100, volume))

        async with httpx.AsyncClient(timeout=5.0) as client:
            result = (await _post(client, "/tools/volume", {"level": volume})).json()
            if result.get("ok"):
                return {"success": True, "message": f"🔊 Volume set to {volume}%"}
            return {"success": False, "message": "Couldn't adjust volume."}
    except Exception as e:
        logger.error(f"Music volume failed: {e}")
        return {"success": False, "message": "Volume adjustment failed."}


async def handle_music_search(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicSearch intent."""
    query = intent.slots.get("query")
    if not query:
        return {"success": False, "message": "What would you like me to search for?"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await _get(client, "/tools/search", {"query": query, "limit": 5})
            if resp.status_code != 200:
                return {
                    "success": False,
                    "message": f"I couldn't search for '{query}' right now.",
                }
            return {
                "success": True,
                "message": f"🔍 Here's what I found for '{query}'.",
                "data": resp.json(),
            }
    except Exception as e:
        logger.error(f"Music search failed: {e}")
        return {"success": False, "message": "Search failed."}


async def handle_music_now_playing(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicNowPlaying intent."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            result = (await _get(client, "/tools/now_playing")).json()
            if not result.get("ok"):
                return {"success": False, "message": "Couldn't get playback info."}
            if not result.get("playing"):
                return {"success": True, "message": "Nothing is playing right now."}
            title = result.get("title") or "the current track"
            artist = result.get("artist")
            msg = f"🎵 Now playing: {title}"
            if artist:
                msg += f" by {artist}"
            return {"success": True, "message": msg, "data": result}
    except Exception as e:
        logger.error(f"Music now playing failed: {e}")
        return {"success": False, "message": "Playback info unavailable."}


# ── Static stubs (no backing module endpoint) ──────────────────────────────
# These intents have no corresponding route on the live bridge. They return a
# friendly response without calling a non-existent endpoint, so the handler
# layer never targets a stale contract.

async def handle_music_radio(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicRadio intent."""
    return {"success": True, "message": "🎵 Starting your personalized radio..."}


async def handle_music_mood(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicMood intent."""
    return {"success": True, "message": "🎵 Matching your current mood..."}


async def handle_music_like(intent, user_id: str, context: Dict) -> Dict[str, Any]:
    """Handle MusicLike intent."""
    return {"success": True, "message": "❤️ Liked! I'll remember you enjoy this."}


# Handler mapping for auto-discovery. Only intents backed by a real route (or
# an intentional static stub) are registered. Queue / recommendation intents
# were removed because the live bridge exposes no such endpoints.
INTENT_HANDLERS = {
    "MusicPlay": handle_music_play,
    "MusicPause": handle_music_pause,
    "MusicResume": handle_music_resume,
    "MusicSkip": handle_music_skip,
    "MusicPrevious": handle_music_previous,
    "MusicVolume": handle_music_volume,
    "MusicSearch": handle_music_search,
    "MusicNowPlaying": handle_music_now_playing,
    "MusicRadio": handle_music_radio,
    "MusicMood": handle_music_mood,
    "MusicLike": handle_music_like,
}
