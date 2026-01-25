#!/usr/bin/env python3
"""
Zoe Music Module
================

Music playback and control module for Zoe AI Assistant.
Provides tools for AI control across multiple music services and devices.

Supported providers:
- YouTube Music (primary)
- Spotify (optional)
- Apple Music (optional)

Supported outputs:
- Direct playback
- Chromecast
- AirPlay
- Home Assistant media players
"""

from fastapi import FastAPI, HTTPException, Query, Depends, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import os
import sys
from pathlib import Path

# Add services to path
sys.path.insert(0, str(Path(__file__).parent))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zoe Music Module",
    description="Music playback and control for Zoe AI Assistant",
    version="1.0.0"
)

# Mount static files for widget UI
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    logger.info(f"📁 Serving static files from {static_dir}")
else:
    logger.warning(f"⚠️  Static directory not found: {static_dir}")

# Configuration
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://zoe-mcp-server:8003")
DATABASE_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
PLATFORM = os.getenv("PLATFORM", "unknown")

# Initialize music services (lazy loaded)
_music_services = None

def get_music_services():
    """Lazy load music services."""
    global _music_services
    
    if _music_services is None:
        try:
            from services.music import (
                get_youtube_music,
                get_media_controller,
                get_auth_manager,
                get_event_tracker,
                get_affinity_engine,
                get_recommendation_engine
            )
            
            _music_services = {
                "youtube": get_youtube_music(),
                "controller": get_media_controller(),
                "auth": get_auth_manager(),
                "events": get_event_tracker(),
                "affinity": get_affinity_engine(),
                "recommendations": get_recommendation_engine()
            }
            
            logger.info("✅ Music services initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize music services: {e}")
            return None
    
    return _music_services


# ============================================================
# Pydantic Models
# ============================================================

class SearchRequest(BaseModel):
    """Search for music."""
    query: str
    filter_type: str = "songs"  # songs, albums, artists, playlists
    limit: int = 10
    user_id: Optional[str] = None


class PlayRequest(BaseModel):
    """Play a song/album/playlist."""
    query: Optional[str] = None
    track_id: Optional[str] = None
    source: str = "youtube"  # youtube, spotify, apple
    zone: Optional[str] = None
    user_id: Optional[str] = None


class VolumeRequest(BaseModel):
    """Set volume."""
    volume: int  # 0-100
    zone: Optional[str] = None


class QueueRequest(BaseModel):
    """Add to queue."""
    track_id: str
    title: Optional[str] = None
    artist: Optional[str] = None
    user_id: Optional[str] = None


class ContextRequest(BaseModel):
    """Get music context for chat."""
    user_id: str


# ============================================================
# Health & Status
# ============================================================

@app.get("/")
async def root():
    """Service health check."""
    services = get_music_services()
    
    if not services:
        return {
            "service": "Zoe Music Module",
            "status": "unhealthy",
            "version": "1.0.0",
            "platform": PLATFORM,
            "services_initialized": False
        }
    
    return {
        "service": "Zoe Music Module",
        "status": "healthy",
        "version": "1.0.0",
        "platform": PLATFORM,
        "services_initialized": True,
        "tools": [
            "music.search",
            "music.play_song",
            "music.pause",
            "music.resume",
            "music.skip",
            "music.set_volume",
            "music.get_queue",
            "music.add_to_queue",
            "music.create_playlist",
            "music.get_recommendations",
            "music.list_zones",
            "music.get_context"
        ]
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    services = get_music_services()
    return {
        "status": "healthy" if services else "unhealthy",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/widget/manifest")
async def get_widget_manifest():
    """Get widget manifest for UI integration"""
    manifest_path = Path(__file__).parent / "static" / "manifest.json"
    if manifest_path.exists():
        return FileResponse(manifest_path, media_type="application/json")
    else:
        raise HTTPException(status_code=404, detail="Widget manifest not found")


# ============================================================
# MCP Tools - Music Control
# ============================================================

@app.post("/tools/search")
async def tool_search(request: SearchRequest):
    """
    MCP Tool: music.search
    
    Search for music (songs, albums, artists, playlists).
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    youtube = services["youtube"]
    
    try:
        results = await youtube.search(
            query=request.query,
            user_id=request.user_id or "default",
            filter_type=request.filter_type,
            limit=request.limit
        )
        
        return {
            "success": True,
            "query": request.query,
            "filter": request.filter_type,
            "results": results,
            "count": len(results)
        }
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tools/play_song")
async def tool_play_song(request: PlayRequest):
    """
    MCP Tool: music.play_song
    
    Play a song, album, or playlist by query or track ID.
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    youtube = services["youtube"]
    controller = services["controller"]
    
    try:
        # If query provided, search first
        if request.query and not request.track_id:
            results = await youtube.search(
                query=request.query,
                user_id=request.user_id or "default",
                filter_type="songs",
                limit=1
            )
            
            if not results:
                return {
                    "success": False,
                    "error": "No results found",
                    "query": request.query
                }
            
            track = results[0]
            track_id = track.get('videoId') or track.get('id')
        else:
            track_id = request.track_id
            track = {"videoId": track_id}
        
        # Play the track
        # Note: target_device_id can be None to use default device/browser
        result = await controller.play(
            track_id=track_id,
            target_device_id=None,  # Use default device/browser
            user_id=request.user_id or "default",
            track_info=track
        )
        
        return {
            "success": True,
            "status": "playing",
            "track_id": track_id,
            "track": track,
            "zone": request.zone
        }
    except Exception as e:
        logger.error(f"Play failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tools/pause")
async def tool_pause(zone: Optional[str] = None):
    """
    MCP Tool: music.pause
    
    Pause current playback.
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    controller = services["controller"]
    
    try:
        await controller.pause(zone=zone)
        return {
            "success": True,
            "status": "paused",
            "zone": zone
        }
    except Exception as e:
        logger.error(f"Pause failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tools/resume")
async def tool_resume(zone: Optional[str] = None):
    """
    MCP Tool: music.resume
    
    Resume paused playback.
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    controller = services["controller"]
    
    try:
        await controller.resume(zone=zone)
        return {
            "success": True,
            "status": "playing",
            "zone": zone
        }
    except Exception as e:
        logger.error(f"Resume failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tools/skip")
async def tool_skip(zone: Optional[str] = None):
    """
    MCP Tool: music.skip
    
    Skip to next track in queue.
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    controller = services["controller"]
    
    try:
        next_track = await controller.skip(zone=zone)
        return {
            "success": True,
            "status": "skipped",
            "next_track": next_track,
            "zone": zone
        }
    except Exception as e:
        logger.error(f"Skip failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tools/set_volume")
async def tool_set_volume(request: VolumeRequest):
    """
    MCP Tool: music.set_volume
    
    Set playback volume (0-100).
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    controller = services["controller"]
    
    if not 0 <= request.volume <= 100:
        raise HTTPException(status_code=400, detail="Volume must be 0-100")
    
    try:
        await controller.set_volume(
            volume=request.volume,
            zone=request.zone
        )
        return {
            "success": True,
            "volume": request.volume,
            "zone": request.zone
        }
    except Exception as e:
        logger.error(f"Set volume failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tools/get_queue")
async def tool_get_queue(zone: Optional[str] = None, user_id: Optional[str] = None):
    """
    MCP Tool: music.get_queue
    
    Get current playback queue.
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    controller = services["controller"]
    
    try:
        queue = await controller.get_queue(
            zone=zone,
            user_id=user_id or "default"
        )
        return {
            "success": True,
            "queue": queue,
            "count": len(queue) if queue else 0,
            "zone": zone
        }
    except Exception as e:
        logger.error(f"Get queue failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tools/add_to_queue")
async def tool_add_to_queue(request: QueueRequest):
    """
    MCP Tool: music.add_to_queue
    
    Add a track to the playback queue.
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    controller = services["controller"]
    
    try:
        await controller.add_to_queue(
            track_id=request.track_id,
            title=request.title,
            artist=request.artist,
            user_id=request.user_id or "default"
        )
        return {
            "success": True,
            "track_id": request.track_id,
            "message": "Added to queue"
        }
    except Exception as e:
        logger.error(f"Add to queue failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tools/get_recommendations")
async def tool_get_recommendations(
    user_id: str,
    context: Optional[str] = None,
    limit: int = 10
):
    """
    MCP Tool: music.get_recommendations
    
    Get personalized music recommendations based on listening history.
    """
    services = get_music_services()
    if not services:
        raise HTTPException(status_code=503, detail="Music services unavailable")
    
    recommendations = services["recommendations"]
    
    try:
        results = await recommendations.get_recommendations(
            user_id=user_id,
            context=context,
            limit=limit
        )
        return {
            "success": True,
            "recommendations": results,
            "count": len(results)
        }
    except Exception as e:
        logger.error(f"Get recommendations failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tools/get_context")
async def tool_get_context(request: ContextRequest):
    """
    MCP Tool: music.get_context
    
    Get music context for chat prompt (replaces chat.py import).
    Provides current playback state, recent history, preferences.
    """
    services = get_music_services()
    if not services:
        return {
            "success": False,
            "context": None,
            "error": "Music services unavailable"
        }
    
    try:
        from services.music.context import get_music_context, format_music_for_prompt
        
        context_data = await get_music_context(request.user_id)
        formatted = format_music_for_prompt(context_data)
        
        return {
            "success": True,
            "context": formatted,
            "raw_context": context_data
        }
    except Exception as e:
        logger.error(f"Get context failed: {e}")
        return {
            "success": False,
            "context": None,
            "error": str(e)
        }


# ============================================================
# Startup
# ============================================================

@app.on_event("startup")
async def startup():
    """Initialize services and register with MCP server."""
    logger.info("🎵 Starting Zoe Music Module")
    logger.info(f"Platform: {PLATFORM}")
    logger.info(f"Database: {DATABASE_PATH}")
    logger.info(f"MCP Server: {MCP_SERVER_URL}")
    
    # Initialize services
    services = get_music_services()
    if not services:
        logger.error("❌ Failed to initialize music services")
        return
    
    logger.info("✅ Music services initialized")
    
    # TODO: Register tools with zoe-mcp-server
    # This will be implemented in Phase 4
    logger.info("📝 Tool registration with MCP server - TODO")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    logger.info("👋 Shutting down Zoe Music Module")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
