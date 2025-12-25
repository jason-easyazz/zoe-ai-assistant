"""
Music Router
============

API endpoints for music playback, search, and authentication.
Integrates with YouTube Music and routes playback to devices.

Endpoints:
- Search and browse
- Playback control
- Queue management
- Authentication (OAuth)
"""

from fastapi import APIRouter, HTTPException, Query, Header, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import os

from auth_integration import validate_session, AuthenticatedSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music", tags=["music"])


# ============================================================
# Pydantic Models
# ============================================================

class PlayRequest(BaseModel):
    """Request to play a track"""
    track_id: str
    target_device_id: Optional[str] = None
    add_to_queue: bool = False
    mode: str = "audio"  # "audio" or "video"
    force_direct: bool = False  # Force direct stream URL (for fallback when embed fails)


class QueueAddRequest(BaseModel):
    """Request to add track to queue"""
    track_id: str
    title: Optional[str] = None
    artist: Optional[str] = None


class VolumeRequest(BaseModel):
    """Request to set volume"""
    volume: int  # 0-100


class SeekRequest(BaseModel):
    """Request to seek position"""
    position_ms: int


# ============================================================
# Service Initialization
# ============================================================

def get_services():
    """Lazy load music services."""
    try:
        from services.music import get_youtube_music, get_media_controller, get_auth_manager
        return get_youtube_music(), get_media_controller(), get_auth_manager()
    except Exception as e:
        logger.error(f"Failed to initialize music services: {e}")
        return None, None, None


# ============================================================
# Search & Browse
# ============================================================

@router.get("/search")
async def search(
    q: str = Query(..., description="Search query"),
    filter_type: str = Query("songs", description="Filter: songs, albums, artists, playlists"),
    limit: int = Query(10, ge=1, le=50),
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Search for music on YouTube Music.
    
    Returns matching tracks, albums, or playlists.
    """
    youtube, _, _ = get_services()
    
    if not youtube:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        results = await youtube.search(q, session.user_id, filter_type, limit)
        return {
            "query": q,
            "filter": filter_type,
            "results": results,
            "count": len(results)
        }
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/track/{track_id}")
async def get_track(
    track_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get detailed track information."""
    youtube, _, _ = get_services()
    
    if not youtube:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        track = await youtube.get_track(track_id, session.user_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        return track
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get track failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/playlists")
async def get_playlists(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get user's playlists from YouTube Music."""
    youtube, _, _ = get_services()
    
    if not youtube:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        playlists = await youtube.get_user_playlists(session.user_id)
        return {"playlists": playlists}
    except Exception as e:
        logger.error(f"Get playlists failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/playlist/{playlist_id}")
async def get_playlist(
    playlist_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get playlist with tracks."""
    youtube, _, _ = get_services()
    
    if not youtube:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        playlist = await youtube.get_playlist(playlist_id, session.user_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        return playlist
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get playlist failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recommendations")
async def get_recommendations(
    limit: int = Query(20, ge=1, le=50),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get personalized recommendations."""
    youtube, _, _ = get_services()
    
    if not youtube:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        tracks = await youtube.get_recommendations(session.user_id, limit)
        return {"recommendations": tracks}
    except Exception as e:
        logger.error(f"Get recommendations failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Playback Control
# ============================================================

@router.post("/play")
async def play(
    request: PlayRequest,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """
    Start playing a track.
    
    Target device can be specified in request or via X-Device-Id header.
    """
    youtube, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        target_device = request.target_device_id or x_device_id
        
        # Try to get track info, but don't fail if OAuth doesn't work
        track_info = None
        if youtube:
            try:
                track_info = await youtube.get_track(request.track_id, session.user_id)
            except Exception as e:
                logger.warning(f"Could not get track info (continuing anyway): {e}")
                # Create minimal track info from the request
                track_info = {"id": request.track_id, "videoId": request.track_id}
        
        result = await controller.play(
            request.track_id,
            target_device,
            session.user_id,
            track_info,
            mode=request.mode,
            force_direct=request.force_direct
        )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Playback failed"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Play failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/video-id/{track_id}")
async def get_video_id(
    track_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Get the official music video ID for a track.
    
    Searches YouTube for the official music video matching the track.
    Returns video_id that can be used with YouTube embed player.
    """
    youtube, _, _ = get_services()
    
    if not youtube:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        # Try to get track info first to get title/artist
        track_info = None
        try:
            track_info = await youtube.get_track(track_id, session.user_id)
        except Exception:
            pass
        
        # Get the music video ID
        video_data = await youtube.get_stream_url_video(track_id, track_info)
        
        if video_data:
            return {
                "video_id": video_data.get('video_id', track_id),
                "is_music_video": video_data.get('is_music_video', False),
                "title": video_data.get('title'),
                "original_track_id": track_id
            }
        else:
            # Fall back to using the track_id directly
            return {
                "video_id": track_id,
                "is_music_video": False,
                "original_track_id": track_id
            }
            
    except Exception as e:
        logger.error(f"Get video ID failed: {e}")
        # Return track_id as fallback
        return {
            "video_id": track_id,
            "is_music_video": False,
            "error": str(e)
        }


@router.post("/pause")
async def pause(
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Pause playback."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.pause(session.user_id, x_device_id)
        return result
    except Exception as e:
        logger.error(f"Pause failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resume")
async def resume(
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Resume playback."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.resume(session.user_id, x_device_id)
        return result
    except Exception as e:
        logger.error(f"Resume failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skip")
async def skip(
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Skip to next track in queue."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.skip(session.user_id, x_device_id)
        return result
    except Exception as e:
        logger.error(f"Skip failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/previous")
async def previous(
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Play previous track."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.previous(session.user_id, x_device_id)
        return result
    except Exception as e:
        logger.error(f"Previous failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seek")
async def seek(
    request: SeekRequest,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Seek to position in current track."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.seek(session.user_id, request.position_ms, x_device_id)
        return result
    except Exception as e:
        logger.error(f"Seek failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/volume")
async def set_volume(
    request: VolumeRequest,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Set playback volume (0-100)."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.set_volume(request.volume, x_device_id, session.user_id)
        return result
    except Exception as e:
        logger.error(f"Volume failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class QueueRequest(BaseModel):
    track_id: str
    track_info: Optional[Dict[str, Any]] = None


@router.post("/queue")
async def add_to_queue(
    request: QueueRequest,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Add a track to the queue."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.add_to_queue(
            session.user_id,
            request.track_id,
            request.track_info or {},
            x_device_id
        )
        return result
    except Exception as e:
        logger.error(f"Add to queue failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/next")
async def add_to_queue_next(
    request: QueueRequest,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Add a track to play next (insert at front of queue)."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.add_to_queue(
            session.user_id,
            request.track_id,
            request.track_info or {},
            x_device_id,
            position=0  # Insert at front
        )
        return result
    except Exception as e:
        logger.error(f"Add to queue next failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class EventRequest(BaseModel):
    track_id: str
    event_type: str
    position_ms: Optional[int] = None
    duration_ms: Optional[int] = None


@router.post("/event")
async def track_event(
    request: EventRequest,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Report a music playback event for learning."""
    try:
        from services.music.event_tracker import get_event_tracker
        tracker = get_event_tracker()
        
        success = await tracker.track_event(
            user_id=session.user_id,
            track_id=request.track_id,
            event_type=request.event_type,
            device_id=x_device_id,
            position_ms=request.position_ms,
            duration_ms=request.duration_ms
        )
        
        return {"success": success}
    except Exception as e:
        logger.error(f"Track event failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/like/{track_id}")
async def like_track(
    track_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Like a track."""
    try:
        from services.music.event_tracker import get_event_tracker
        tracker = get_event_tracker()
        
        success = await tracker.track_like(session.user_id, track_id)
        
        # Also store in likes table for quick access
        import sqlite3
        import os
        db_path = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO music_likes (user_id, track_id, provider)
            VALUES (?, ?, 'youtube_music')
        """, (session.user_id, track_id))
        conn.commit()
        conn.close()
        
        return {"success": True}
    except Exception as e:
        logger.error(f"Like track failed: {e}")
        return {"success": False, "error": str(e)}


@router.get("/state")
async def get_state(
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Get current playback state."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        state = await controller.get_state(session.user_id, x_device_id)
        return state or {"is_playing": False, "track_id": None}
    except Exception as e:
        logger.error(f"Get state failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Queue Management
# ============================================================

@router.get("/queue")
async def get_queue(
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Get current queue."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        queue = await controller.get_queue(session.user_id, x_device_id)
        return {"queue": queue}
    except Exception as e:
        logger.error(f"Get queue failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/queue/{track_id}")
async def remove_from_queue(
    track_id: str,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Remove a track from the queue."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.remove_from_queue(session.user_id, track_id, x_device_id)
        return result
    except Exception as e:
        logger.error(f"Remove from queue failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class QueueReorderRequest(BaseModel):
    track_id: str
    new_position: int


@router.post("/queue/reorder")
async def reorder_queue(
    request: QueueReorderRequest,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Reorder a track in the queue."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        result = await controller.reorder_queue(
            session.user_id, 
            request.track_id, 
            request.new_position,
            x_device_id
        )
        return result
    except Exception as e:
        logger.error(f"Reorder queue failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/add")
async def add_to_queue(
    request: QueueAddRequest,
    session: AuthenticatedSession = Depends(validate_session),
    x_device_id: Optional[str] = Header(None)
):
    """Add track to queue."""
    _, controller, _ = get_services()
    
    if not controller:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        track_info = {
            "title": request.title,
            "artist": request.artist
        }
        result = await controller.add_to_queue(
            session.user_id,
            request.track_id,
            track_info,
            x_device_id
        )
        return result
    except Exception as e:
        logger.error(f"Add to queue failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Authentication
# ============================================================

@router.get("/auth/status")
async def auth_status(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Check YouTube Music authentication status."""
    youtube, _, auth_manager = get_services()
    
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        status = await auth_manager.check_auth_status(session.user_id, "youtube_music")
        
        # Search works without auth using public API, so we don't need to test OAuth client
        # Just check if we have stored credentials
        if status.get("authenticated"):
            status["api_working"] = True  # Search always works via public API
            status["client_working"] = True
        
        return status
    except Exception as e:
        logger.error(f"Auth status check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/auth/youtube")
async def youtube_auth_revoke(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Revoke YouTube Music authentication to allow re-authentication."""
    youtube, _, auth_manager = get_services()
    
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        # Clear from cache
        if session.user_id in youtube._clients:
            del youtube._clients[session.user_id]
        
        # Delete from storage
        deleted = await auth_manager.delete_auth(session.user_id, "youtube_music")
        
        return {
            "success": True,
            "message": "Authentication revoked. You can now re-authenticate."
        }
    except Exception as e:
        logger.error(f"Auth revoke failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/youtube/start")
async def youtube_auth_start(
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Start YouTube Music OAuth using Google's Device Flow.
    
    Returns a verification URL and user code. User goes to the URL
    on any device and enters the code to authenticate.
    """
    import requests
    import sqlite3
    
    # YouTube Music OAuth client credentials (used by ytmusicapi - these are public)
    client_id = "REDACTED_CLIENT_ID"
    client_secret = "REDACTED_CLIENT_SECRET"
    
    try:
        # Start device authorization flow using YouTube's OAuth endpoint
        # (different from generic Google OAuth)
        response = requests.post(
            "https://www.youtube.com/o/oauth2/device/code",
            data={
                "client_id": client_id,
                "scope": "https://www.googleapis.com/auth/youtube"
            },
            timeout=10
        )
        
        if response.status_code != 200:
            logger.error(f"Device code request failed: {response.text}")
            raise HTTPException(status_code=500, detail="Failed to start authentication")
        
        data = response.json()
        
        # Store device_code with user_id for polling
        device_code = data.get("device_code")
        try:
            conn = sqlite3.connect(os.getenv("DATABASE_PATH", "/app/data/zoe.db"))
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS oauth_device_codes (
                    user_id TEXT PRIMARY KEY,
                    device_code TEXT NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    interval INTEGER DEFAULT 5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            expires_in = data.get("expires_in", 1800)
            from datetime import datetime, timedelta
            expires_at = (datetime.now() + timedelta(seconds=expires_in)).isoformat()
            
            cursor.execute("""
                INSERT OR REPLACE INTO oauth_device_codes 
                (user_id, device_code, expires_at, interval) VALUES (?, ?, ?, ?)
            """, (session.user_id, device_code, expires_at, data.get("interval", 5)))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to store device code: {e}")
        
        return {
            "verification_url": data.get("verification_url", "https://www.google.com/device"),
            "user_code": data.get("user_code"),
            "expires_in": data.get("expires_in", 1800),
            "interval": data.get("interval", 5),
            "method": "device_flow"
        }
        
    except requests.RequestException as e:
        logger.error(f"Device auth request failed: {e}")
        raise HTTPException(status_code=500, detail="Network error during authentication")


@router.post("/auth/youtube/poll")
async def youtube_auth_poll(
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Poll for device flow completion.
    
    Call this after user has entered the code at the verification URL.
    """
    import requests
    import sqlite3
    from datetime import datetime
    
    _, _, auth_manager = get_services()
    
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    # Get stored device code
    try:
        conn = sqlite3.connect(os.getenv("DATABASE_PATH", "/app/data/zoe.db"))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT device_code, expires_at FROM oauth_device_codes WHERE user_id = ?
        """, (session.user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {"status": "error", "message": "No pending authentication. Please start again."}
        
        device_code, expires_at = row
        
        if datetime.fromisoformat(expires_at) < datetime.now():
            return {"status": "expired", "message": "Authentication expired. Please try again."}
            
    except Exception as e:
        logger.error(f"Failed to get device code: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    
    # Poll for token
    client_id = "REDACTED_CLIENT_ID"
    client_secret = "REDACTED_CLIENT_SECRET"
    
    try:
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
            },
            timeout=10
        )
        
        data = response.json()
        
        if response.status_code == 200:
            # Success! Store tokens
            # ytmusicapi expects expires_at (Unix timestamp), not expires_in
            import time
            expires_in = data.get("expires_in", 3600)
            expires_at = int(time.time()) + expires_in
            
            auth_data = {
                "access_token": data.get("access_token"),
                "refresh_token": data.get("refresh_token"),
                "token_type": data.get("token_type", "Bearer"),
                "expires_at": expires_at,
                "scope": data.get("scope", "https://www.googleapis.com/auth/youtube")
            }
            
            await auth_manager.store_auth(
                session.user_id, "youtube_music", auth_data, auth_type="oauth"
            )
            
            # Clean up device code
            try:
                conn = sqlite3.connect(os.getenv("DATABASE_PATH", "/app/data/zoe.db"))
                cursor = conn.cursor()
                cursor.execute("DELETE FROM oauth_device_codes WHERE user_id = ?", (session.user_id,))
                conn.commit()
                conn.close()
            except:
                pass
            
            return {"status": "success", "message": "YouTube Music connected!"}
        
        elif data.get("error") == "authorization_pending":
            return {"status": "pending", "message": "Waiting for you to complete sign-in..."}
        
        elif data.get("error") == "slow_down":
            return {"status": "pending", "message": "Please wait..."}
        
        elif data.get("error") == "access_denied":
            return {"status": "denied", "message": "Access was denied. Please try again."}
        
        else:
            return {"status": "error", "message": data.get("error_description", "Unknown error")}
            
    except requests.RequestException as e:
        logger.error(f"Token poll failed: {e}")
        return {"status": "error", "message": "Network error"}


@router.get("/auth/youtube/callback")
async def youtube_auth_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="State for CSRF verification")
):
    """
    OAuth callback endpoint.
    
    Google redirects here after user signs in. Exchanges code for tokens
    and redirects user back to music page.
    """
    import sqlite3
    import requests
    
    # Verify state and get user_id
    try:
        conn = sqlite3.connect(os.getenv("DATABASE_PATH", "/app/data/zoe.db"))
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM oauth_states WHERE state = ?", (state,))
        row = cursor.fetchone()
        
        if not row:
            # Return error page
            return HTMLResponse(content="""
                <html><head><title>Auth Error</title></head>
                <body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f1f3f6;">
                    <div style="text-align:center;padding:40px;background:white;border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,0.1);">
                        <h1 style="color:#dc2626;">❌ Authentication Failed</h1>
                        <p>Invalid or expired session. Please try again.</p>
                        <a href="/music.html" style="color:#7B61FF;">Return to Music</a>
                    </div>
                </body></html>
            """, status_code=400)
        
        user_id = row[0]
        
        # Delete used state
        cursor.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"State verification failed: {e}")
        return HTMLResponse(content=f"<html><body>Error: {e}</body></html>", status_code=500)
    
    # Exchange code for tokens
    try:
        client_id = "REDACTED_CLIENT_ID"
        client_secret = "REDACTED_CLIENT_SECRET"
        
        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"  # Must match what was used
            },
            timeout=10
        )
        
        if token_response.status_code != 200:
            error = token_response.json().get("error_description", "Token exchange failed")
            return HTMLResponse(content=f"""
                <html><head><title>Auth Error</title></head>
                <body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f1f3f6;">
                    <div style="text-align:center;padding:40px;background:white;border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,0.1);">
                        <h1 style="color:#dc2626;">❌ Authentication Failed</h1>
                        <p>{error}</p>
                        <a href="/music.html" style="color:#7B61FF;">Try Again</a>
                    </div>
                </body></html>
            """, status_code=400)
        
        tokens = token_response.json()
        
        # Store credentials
        _, _, auth_manager = get_services()
        if auth_manager:
            import time
            expires_in = tokens.get("expires_in", 3600)
            expires_at = int(time.time()) + expires_in
            
            auth_data = {
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "token_type": tokens.get("token_type", "Bearer"),
                "expires_at": expires_at,
                "scope": tokens.get("scope", "https://www.googleapis.com/auth/youtube")
            }
            await auth_manager.store_auth(user_id, "youtube_music", auth_data, auth_type="oauth")
        
        # Success! Redirect to music page
        return HTMLResponse(content="""
            <html>
            <head>
                <title>Connected!</title>
                <meta http-equiv="refresh" content="2;url=/music.html">
            </head>
            <body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:linear-gradient(135deg, #fafbfc 0%, #f1f3f6 100%);">
                <div style="text-align:center;padding:40px;background:white;border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,0.1);">
                    <h1 style="background:linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;">✓ Connected!</h1>
                    <p style="color:#666;">YouTube Music is now linked to Zoe.</p>
                    <p style="color:#999;font-size:14px;">Redirecting to music...</p>
                </div>
            </body>
            </html>
        """)
        
    except Exception as e:
        logger.error(f"Token exchange failed: {e}")
        return HTMLResponse(content=f"""
            <html><body style="font-family:system-ui;text-align:center;padding:40px;">
                <h1>Error</h1><p>{str(e)}</p>
                <a href="/music.html">Return to Music</a>
            </body></html>
        """, status_code=500)


@router.get("/auth/youtube/setup")
async def youtube_setup(session: AuthenticatedSession = Depends(validate_session)):
    """
    Get YouTube Music OAuth URL for browser-based login.
    """
    # Just redirect to the start endpoint
    result = await youtube_auth_start(session)
    return result


@router.post("/auth/youtube/code")
async def youtube_auth_with_code(
    code: str = Query(..., description="Authorization code from Google"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Complete YouTube Music OAuth using authorization code (manual flow).
    """
    _, _, auth_manager = get_services()
    
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        import requests
        
        client_id = "REDACTED_CLIENT_ID"
        client_secret = "REDACTED_CLIENT_SECRET"
        
        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
            },
            timeout=10
        )
        
        if token_response.status_code != 200:
            error_detail = token_response.json().get("error_description", "Failed to exchange code")
            raise HTTPException(status_code=400, detail=error_detail)
        
        tokens = token_response.json()
        
        import time
        expires_in = tokens.get("expires_in", 3600)
        expires_at = int(time.time()) + expires_in
        
        auth_data = {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "token_type": tokens.get("token_type", "Bearer"),
            "expires_at": expires_at,
            "scope": tokens.get("scope", "https://www.googleapis.com/auth/youtube")
        }
        
        success = await auth_manager.store_auth(
            session.user_id, "youtube_music", auth_data, auth_type="oauth"
        )
        
        if success:
            return {"success": True, "message": "YouTube Music connected!"}
        else:
            raise HTTPException(status_code=500, detail="Failed to store credentials")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"YouTube OAuth failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/youtube/complete")
async def youtube_complete(
    auth_data: Dict[str, Any],
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Complete YouTube Music authentication.
    
    Accepts the OAuth JSON from ytmusicapi oauth command.
    """
    _, _, auth_manager = get_services()
    
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        success = await auth_manager.store_auth(
            session.user_id,
            "youtube_music",
            auth_data,
            auth_type="oauth"
        )
        
        if success:
            return {
                "success": True,
                "message": "YouTube Music authenticated successfully"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to store authentication")
            
    except Exception as e:
        logger.error(f"YouTube auth failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/auth/youtube")
async def youtube_logout(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Remove YouTube Music authentication."""
    _, _, auth_manager = get_services()
    
    if not auth_manager:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    try:
        deleted = await auth_manager.delete_auth(session.user_id, "youtube_music")
        return {
            "success": deleted,
            "message": "Logged out from YouTube Music" if deleted else "Not authenticated"
        }
    except Exception as e:
        logger.error(f"YouTube logout failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Recommendations & Intelligence
# ============================================================

@router.get("/capabilities")
async def get_capabilities():
    """
    Get music system capabilities for this platform.
    
    Returns what features are available (ML vs metadata recommendations).
    """
    try:
        from model_config import detect_hardware
        platform = detect_hardware()
    except:
        platform = "unknown"
    
    # Check ML availability
    ml_enabled = False
    memory_available_gb = 0
    
    try:
        import psutil
        memory_available_gb = round(psutil.virtual_memory().available / (1024**3), 1)
        
        if platform == "jetson" and memory_available_gb >= 3.0:
            # Try to import ML components
            try:
                from services.music.recommendation_engine import MLRecommendationEngine
                ml_enabled = True
            except:
                pass
    except:
        pass
    
    return {
        "platform": platform,
        "ml_enabled": ml_enabled,
        "recommendation_quality": "ml" if ml_enabled else "metadata",
        "memory_available_gb": memory_available_gb,
        "features": {
            "event_tracking": True,
            "affinity_scoring": True,
            "metadata_recommendations": True,
            "audio_analysis": ml_enabled,
            "embedding_similarity": ml_enabled,
            "mood_detection": ml_enabled,
        }
    }


@router.get("/stats")
async def get_listening_stats(
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Get user's listening statistics.
    
    Returns play counts, skip rates, top tracks, and top artists.
    """
    try:
        from services.music.affinity_engine import get_affinity_engine
        
        affinity = get_affinity_engine()
        stats = affinity.get_listening_stats(session.user_id)
        
        # Add top tracks and artists
        top_tracks = affinity.get_top_tracks(session.user_id, limit=10)
        top_artists = affinity.get_top_artists(session.user_id, limit=5)
        
        stats["top_tracks"] = [
            {"track_id": t[0], "score": round(t[1], 2)} 
            for t in top_tracks
        ]
        stats["top_artists"] = [
            {"artist": a[0], "score": round(a[1], 2)} 
            for a in top_artists
        ]
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get listening stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/similar/{track_id}")
async def get_similar_tracks(
    track_id: str,
    limit: int = Query(10, ge=1, le=50),
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Get tracks similar to the given track.
    
    Uses ML embeddings on Jetson, metadata search on Pi5.
    """
    try:
        from services.music.recommendation_engine import get_recommendation_engine
        
        engine = get_recommendation_engine()
        tracks = await engine.get_similar(track_id, session.user_id, limit)
        
        return {"tracks": tracks, "count": len(tracks)}
        
    except Exception as e:
        logger.error(f"Failed to get similar tracks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/radio")
async def get_personal_radio(
    seed_track: Optional[str] = Query(None, description="Optional seed track ID"),
    limit: int = Query(20, ge=1, le=100),
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Generate a personal radio queue.
    
    If seed_track provided, builds radio around that track.
    Otherwise uses user's listening history and preferences.
    """
    try:
        from services.music.recommendation_engine import get_recommendation_engine
        
        engine = get_recommendation_engine()
        tracks = await engine.get_radio(session.user_id, seed_track, limit)
        
        return {"tracks": tracks, "count": len(tracks), "seed_track": seed_track}
        
    except Exception as e:
        logger.error(f"Failed to generate radio: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/discover")
async def get_discovery_mix(
    limit: int = Query(20, ge=1, le=100),
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Get discovery mix - new music matching user's taste.
    
    Returns tracks the user hasn't played that match their preferences.
    """
    try:
        from services.music.recommendation_engine import get_recommendation_engine
        
        engine = get_recommendation_engine()
        tracks = await engine.get_discover(session.user_id, limit)
        
        return {"tracks": tracks, "count": len(tracks)}
        
    except Exception as e:
        logger.error(f"Failed to get discovery mix: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mood")
async def get_mood_match(
    limit: int = Query(20, ge=1, le=100),
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Get tracks matching current listening mood.
    
    Uses average embedding of recent tracks (Jetson ML) or recent play
    context (Pi5 metadata).
    """
    try:
        from services.music.recommendation_engine import get_recommendation_engine
        
        engine = get_recommendation_engine()
        tracks = await engine.get_mood_match(session.user_id, limit)
        
        return {"tracks": tracks, "count": len(tracks)}
        
    except Exception as e:
        logger.error(f"Failed to get mood match: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/like/{track_id}")
async def like_track(
    track_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Like a track (explicit positive signal).
    
    Updates both local affinity and YouTube Music library if authenticated.
    """
    try:
        from services.music.event_tracker import get_event_tracker
        
        tracker = get_event_tracker()
        await tracker.track_like(session.user_id, track_id)
        
        # TODO: Optionally sync to YouTube Music library
        
        return {"success": True, "message": "Track liked"}
        
    except Exception as e:
        logger.error(f"Failed to like track: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Cache Management
# ============================================================

@router.get("/cache/stats")
async def cache_stats():
    """Get stream URL cache statistics."""
    youtube, _, _ = get_services()
    
    if not youtube:
        return {"error": "Music service unavailable"}
    
    return youtube.get_cache_stats()


@router.post("/cache/clear")
async def cache_clear():
    """Clear the stream URL cache."""
    youtube, _, _ = get_services()
    
    if not youtube:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    youtube.clear_cache()
    return {"message": "Cache cleared"}


# ============================================================
# Zone Management
# ============================================================

class ZoneCreateRequest(BaseModel):
    """Request to create a new zone"""
    name: str
    room_id: Optional[str] = None
    icon: str = "🎵"


class ZoneDeviceRequest(BaseModel):
    """Request to add device to zone"""
    device_id: str
    device_type: str  # browser, chromecast, airplay
    device_name: str
    role: str = "player"  # player, controller, both


class ZoneCommandRequest(BaseModel):
    """Request to send command to zone"""
    command: str  # play, pause, resume, skip, previous, seek, volume
    track_id: Optional[str] = None
    track_info: Optional[Dict] = None
    position_ms: Optional[int] = None
    volume: Optional[int] = None


def get_zone_manager():
    """Lazy load zone manager."""
    try:
        from services.music.zone_manager import zone_manager
        return zone_manager
    except Exception as e:
        logger.error(f"Failed to load zone manager: {e}")
        return None


def get_cast_service():
    """Lazy load cast service."""
    try:
        from services.music.cast_service import cast_service
        return cast_service
    except Exception as e:
        logger.error(f"Failed to load cast service: {e}")
        return None


def get_airplay_service():
    """Lazy load airplay service."""
    try:
        from services.music.airplay_service import airplay_service
        return airplay_service
    except Exception as e:
        logger.error(f"Failed to load airplay service: {e}")
        return None


@router.get("/zones")
async def list_zones(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all zones for the current user."""
    zm = get_zone_manager()
    if not zm:
        return {"zones": []}
    
    zones = await zm.get_zones_for_user(session.user_id)
    return {
        "zones": [
            {
                "id": z.id,
                "name": z.name,
                "icon": z.icon,
                "room_id": z.room_id,
                "is_default": z.is_default,
                "device_count": len(z.devices),
                "devices": [
                    {
                        "id": d.device_id,
                        "name": d.device_name,
                        "type": d.device_type,
                        "role": d.role,
                        "connected": d.is_connected
                    } for d in z.devices
                ]
            } for z in zones
        ]
    }


@router.post("/zones")
async def create_zone(
    request: ZoneCreateRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new music zone."""
    zm = get_zone_manager()
    if not zm:
        raise HTTPException(status_code=503, detail="Zone manager unavailable")
    
    zone = await zm.create_zone(
        user_id=session.user_id,
        name=request.name,
        room_id=request.room_id,
        icon=request.icon
    )
    
    return {
        "success": True,
        "zone": {
            "id": zone.id,
            "name": zone.name,
            "icon": zone.icon,
            "room_id": zone.room_id
        }
    }


@router.get("/zones/{zone_id}")
async def get_zone(
    zone_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get a specific zone."""
    zm = get_zone_manager()
    if not zm:
        raise HTTPException(status_code=503, detail="Zone manager unavailable")
    
    zone = await zm.get_zone(zone_id)
    if not zone or zone.user_id != session.user_id:
        raise HTTPException(status_code=404, detail="Zone not found")
    
    return {
        "id": zone.id,
        "name": zone.name,
        "icon": zone.icon,
        "room_id": zone.room_id,
        "is_default": zone.is_default,
        "devices": [
            {
                "id": d.device_id,
                "name": d.device_name,
                "type": d.device_type,
                "role": d.role,
                "connected": d.is_connected,
                "supports_video": d.supports_video
            } for d in zone.devices
        ]
    }


@router.delete("/zones/{zone_id}")
async def delete_zone(
    zone_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a zone."""
    zm = get_zone_manager()
    if not zm:
        raise HTTPException(status_code=503, detail="Zone manager unavailable")
    
    success = await zm.delete_zone(zone_id, session.user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Zone not found or not authorized")
    
    return {"success": True}


@router.get("/zones/{zone_id}/state")
async def get_zone_state(
    zone_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get current playback state for a zone."""
    zm = get_zone_manager()
    if not zm:
        raise HTTPException(status_code=503, detail="Zone manager unavailable")
    
    state = await zm.get_zone_state(zone_id)
    if not state:
        raise HTTPException(status_code=404, detail="Zone not found")
    
    return {
        "zone_id": state.zone_id,
        "is_playing": state.is_playing,
        "track_id": state.current_track_id,
        "track_info": state.track_info,
        "position_ms": state.position_ms,
        "volume": state.volume,
        "shuffle": state.shuffle,
        "repeat_mode": state.repeat_mode,
        "queue": state.queue,
        "queue_index": state.queue_index
    }


@router.post("/zones/{zone_id}/command")
async def zone_command(
    zone_id: str,
    request: ZoneCommandRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Send a command to a zone (play, pause, skip, etc.)."""
    zm = get_zone_manager()
    if not zm:
        raise HTTPException(status_code=503, detail="Zone manager unavailable")
    
    command = request.command.lower()
    
    if command == "play" and request.track_id:
        success = await zm.play(zone_id, request.track_id, request.track_info or {})
    elif command == "pause":
        success = await zm.pause(zone_id)
    elif command == "resume":
        success = await zm.resume(zone_id)
    elif command == "skip":
        result = await zm.skip(zone_id)
        return {"success": result is not None, "next_track": result}
    elif command == "previous":
        result = await zm.previous(zone_id)
        return {"success": result is not None, "previous_track": result}
    elif command == "seek" and request.position_ms is not None:
        success = await zm.seek(zone_id, request.position_ms)
    elif command == "volume" and request.volume is not None:
        success = await zm.set_volume(zone_id, request.volume)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown command: {command}")
    
    return {"success": success}


@router.post("/zones/{zone_id}/devices")
async def add_device_to_zone(
    zone_id: str,
    request: ZoneDeviceRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Add a device to a zone."""
    zm = get_zone_manager()
    if not zm:
        raise HTTPException(status_code=503, detail="Zone manager unavailable")
    
    success = await zm.add_device_to_zone(
        zone_id=zone_id,
        device_id=request.device_id,
        device_type=request.device_type,
        device_name=request.device_name,
        role=request.role
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="Zone not found")
    
    return {"success": True}


@router.delete("/zones/{zone_id}/devices/{device_id}")
async def remove_device_from_zone(
    zone_id: str,
    device_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Remove a device from a zone."""
    zm = get_zone_manager()
    if not zm:
        raise HTTPException(status_code=503, detail="Zone manager unavailable")
    
    success = await zm.remove_device_from_zone(zone_id, device_id)
    return {"success": success}


# ============================================================
# Device Discovery
# ============================================================

@router.get("/devices")
async def list_devices(
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Get all available playback devices.
    Includes browser sessions, Chromecast, and AirPlay devices.
    """
    devices = []
    
    # Get Chromecast devices
    cast_svc = get_cast_service()
    if cast_svc and cast_svc._initialized:
        try:
            cast_devices = await cast_svc.discover_devices()
            for d in cast_devices:
                devices.append({
                    "id": d.id,
                    "name": d.friendly_name,
                    "type": "chromecast",
                    "model": d.model_name,
                    "ip_address": d.ip_address,
                    "supports_video": d.supports_video,
                    "is_available": d.is_available
                })
        except Exception as e:
            logger.warning(f"Failed to get Chromecast devices: {e}")
    
    # Get AirPlay devices
    airplay_svc = get_airplay_service()
    if airplay_svc and airplay_svc._initialized:
        try:
            airplay_devices = await airplay_svc.discover_devices()
            for d in airplay_devices:
                devices.append({
                    "id": d.id,
                    "name": d.name,
                    "type": "airplay",
                    "model": d.model,
                    "ip_address": d.ip_address,
                    "device_type": d.device_type,
                    "supports_video": d.supports_video,
                    "requires_pairing": d.requires_pairing,
                    "is_paired": d.is_paired,
                    "is_available": d.is_available
                })
        except Exception as e:
            logger.warning(f"Failed to get AirPlay devices: {e}")
    
    # Get connected browser devices from WebSocket manager
    try:
        from routers.websocket import websocket_manager
        for device_id in websocket_manager.device_connections.keys():
            if device_id.startswith("browser-"):
                devices.append({
                    "id": device_id,
                    "name": device_id.replace("browser-", "Browser "),
                    "type": "browser",
                    "supports_video": True,
                    "is_available": True
                })
    except Exception as e:
        logger.warning(f"Failed to get browser devices: {e}")
    
    return devices


@router.post("/devices/{device_id}/play")
async def play_on_device(
    device_id: str,
    request: PlayRequest,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Play a track on a specific device."""
    youtube, controller, _ = get_services()
    
    if not youtube:
        raise HTTPException(status_code=503, detail="Music service unavailable")
    
    # Get stream URL
    if request.mode == "video":
        stream_data = await youtube.get_stream_url_video(
            request.track_id, 
            None, 
            request.force_direct
        )
        stream_url = stream_data.get('url') if stream_data else None
    else:
        stream_url = await youtube.get_stream_url(request.track_id)
    
    if not stream_url:
        raise HTTPException(status_code=500, detail="Could not get stream URL")
    
    # Get track info
    track_info = {}
    try:
        track_info = await youtube.get_track(request.track_id, session.user_id)
    except Exception:
        track_info = {"id": request.track_id, "title": "Unknown"}
    
    # Route to appropriate service
    success = False
    
    if device_id.startswith("chrome-") or "-" in device_id and len(device_id) == 36:
        # Chromecast device
        cast_svc = get_cast_service()
        if cast_svc:
            success = await cast_svc.play_on_device(
                device_id, 
                stream_url, 
                track_info,
                "video/mp4" if request.mode == "video" else "audio/mp4"
            )
    
    elif device_id.startswith("apple-") or device_id.startswith("airplay-"):
        # AirPlay device
        airplay_svc = get_airplay_service()
        if airplay_svc:
            success = await airplay_svc.play_on_device(device_id, stream_url, track_info)
    
    elif device_id.startswith("browser-"):
        # Browser device - send via WebSocket
        from routers.websocket import send_to_device
        success = await send_to_device(device_id, {
            "type": "media_play",
            "url": stream_url,
            "track_info": track_info
        })
    
    else:
        raise HTTPException(status_code=400, detail=f"Unknown device type: {device_id}")
    
    return {"success": success, "device_id": device_id}


@router.post("/devices/{device_id}/control")
async def control_device(
    device_id: str,
    command: str = Query(..., description="Command: play, pause, stop, seek, volume"),
    position_ms: Optional[int] = Query(None),
    volume: Optional[int] = Query(None),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Send a control command to a specific device."""
    success = False
    
    if device_id.startswith("chrome-") or "-" in device_id and len(device_id) == 36:
        cast_svc = get_cast_service()
        if cast_svc:
            kwargs = {}
            if position_ms is not None:
                kwargs["position_ms"] = position_ms
            if volume is not None:
                kwargs["volume"] = volume
            success = await cast_svc.control(device_id, command, **kwargs)
    
    elif device_id.startswith("apple-") or device_id.startswith("airplay-"):
        airplay_svc = get_airplay_service()
        if airplay_svc:
            kwargs = {}
            if position_ms is not None:
                kwargs["position_ms"] = position_ms
            if volume is not None:
                kwargs["volume"] = volume
            success = await airplay_svc.control(device_id, command, **kwargs)
    
    elif device_id.startswith("browser-"):
        from routers.websocket import send_to_device
        msg = {"type": f"media_{command}"}
        if position_ms is not None:
            msg["position_ms"] = position_ms
        if volume is not None:
            msg["volume"] = volume
        success = await send_to_device(device_id, msg)
    
    return {"success": success}


@router.get("/devices/{device_id}/state")
async def get_device_state(
    device_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get current playback state from a device."""
    state = None
    
    if device_id.startswith("chrome-") or "-" in device_id and len(device_id) == 36:
        cast_svc = get_cast_service()
        if cast_svc:
            state = await cast_svc.get_state(device_id)
    
    elif device_id.startswith("apple-") or device_id.startswith("airplay-"):
        airplay_svc = get_airplay_service()
        if airplay_svc:
            state = await airplay_svc.get_state(device_id)
    
    if state is None:
        return {"is_playing": False, "device_id": device_id}
    
    return {"device_id": device_id, **state}


@router.post("/devices/refresh")
async def refresh_devices(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Refresh device discovery."""
    results = {"chromecast": False, "airplay": False}
    
    cast_svc = get_cast_service()
    if cast_svc:
        try:
            await cast_svc._start_discovery()
            results["chromecast"] = True
        except Exception as e:
            logger.warning(f"Chromecast refresh failed: {e}")
    
    airplay_svc = get_airplay_service()
    if airplay_svc:
        try:
            await airplay_svc.refresh_devices()
            results["airplay"] = True
        except Exception as e:
            logger.warning(f"AirPlay refresh failed: {e}")
    
    return {"success": True, "refreshed": results}

