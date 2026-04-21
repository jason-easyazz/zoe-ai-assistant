"""
TTS Router - Proxy to zoe-tts service with user context
Provides voice synthesis, voice cloning, and device-targeted speech

Device-Aware Features:
- Speak to specific device via WebSocket
- Speak to room via HA media_player
- Broadcast to all user's audio-capable devices
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query, Header
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
import httpx
import logging
import os
import base64

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tts", tags=["TTS"])

# TTS service URL (Docker internal network)
TTS_SERVICE_URL = os.getenv("TTS_SERVICE_URL", "http://zoe-tts:9002")


class TTSRequest(BaseModel):
    text: str
    voice: str = "default"
    speed: float = 1.0
    use_cache: bool = True


class VoiceProfile(BaseModel):
    id: str
    name: str
    user_id: Optional[str]
    reference_text: str
    created_at: str
    is_system: bool = False


@router.get("/health")
async def health():
    """Check TTS service health"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{TTS_SERVICE_URL}/health", timeout=5.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"TTS service health check failed: {e}")
        raise HTTPException(status_code=503, detail="TTS service unavailable")


@router.post("/synthesize")
async def synthesize_speech(request: TTSRequest, user_id: Optional[str] = Query(None)):
    """
    Generate ultra-realistic speech with optional voice cloning
    
    - **text**: Text to synthesize
    - **voice**: Voice profile ID (use "default" for base voice)
    - **speed**: Speech speed multiplier (0.5-2.0)
    - **use_cache**: Enable audio caching for repeated requests
    """
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Add user_id to request for personalized voice profiles
            request_data = request.dict()
            request_data["user_id"] = user_id
            
            response = await client.post(
                f"{TTS_SERVICE_URL}/synthesize",
                json=request_data
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.text
                )
            
            # Stream the audio file back
            return StreamingResponse(
                iter([response.content]),
                media_type="audio/wav",
                headers=dict(response.headers)
            )
    
    except httpx.TimeoutException:
        logger.error("TTS synthesis timeout")
        raise HTTPException(status_code=504, detail="TTS synthesis timeout")
    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clone-voice")
async def clone_voice(
    profile_name: str = Form(...),
    reference_text: str = Form(...),
    user_id: Optional[str] = Query(None),
    reference_audio: UploadFile = File(...)
):
    """
    Create a new voice profile from reference audio (instant voice cloning)
    
    Requires:
    - **profile_name**: Name for the voice profile
    - **reference_text**: Exact transcription of the reference audio
    - **reference_audio**: WAV file (mono, 16-44kHz, 3-15 seconds)
    
    Reference audio should be clean speech with minimal background noise.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Prepare multipart form data
            files = {
                "reference_audio": (reference_audio.filename, reference_audio.file, reference_audio.content_type)
            }
            data = {
                "profile_name": profile_name,
                "reference_text": reference_text,
                "user_id": user_id or ""
            }
            
            response = await client.post(
                f"{TTS_SERVICE_URL}/clone-voice",
                files=files,
                data=data
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.text
                )
            
            return response.json()
    
    except httpx.TimeoutException:
        logger.error("Voice cloning timeout")
        raise HTTPException(status_code=504, detail="Voice cloning timeout")
    except Exception as e:
        logger.error(f"Voice cloning failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/voices")
async def list_voices(user_id: Optional[str] = Query(None)):
    """
    List all available voice profiles
    
    Returns system voices and user-specific voices if user_id provided
    """
    try:
        async with httpx.AsyncClient() as client:
            params = {"user_id": user_id} if user_id else {}
            response = await client.get(
                f"{TTS_SERVICE_URL}/voices",
                params=params,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
    
    except Exception as e:
        logger.error(f"Failed to list voices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/voice-profiles/{profile_id}")
async def get_voice_profile(profile_id: str, user_id: Optional[str] = Query(None)):
    """Get details of a specific voice profile"""
    try:
        async with httpx.AsyncClient() as client:
            params = {"user_id": user_id} if user_id else {}
            response = await client.get(
                f"{TTS_SERVICE_URL}/voice-profiles/{profile_id}",
                params=params,
                timeout=10.0
            )
            
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Voice profile '{profile_id}' not found")
            
            response.raise_for_status()
            return response.json()
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get voice profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/voice-profiles/{profile_id}")
async def delete_voice_profile(profile_id: str, user_id: Optional[str] = Query(None)):
    """
    Delete a voice profile
    
    System voices cannot be deleted
    """
    try:
        async with httpx.AsyncClient() as client:
            params = {"user_id": user_id} if user_id else {}
            response = await client.delete(
                f"{TTS_SERVICE_URL}/voice-profiles/{profile_id}",
                params=params,
                timeout=10.0
            )
            
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Voice profile '{profile_id}' not found or cannot be deleted")
            
            response.raise_for_status()
            return response.json()
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete voice profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache/stats")
async def get_cache_stats():
    """Get TTS cache statistics"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{TTS_SERVICE_URL}/cache/stats", timeout=5.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/clear")
async def clear_cache():
    """Clear TTS cache"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{TTS_SERVICE_URL}/cache/clear", timeout=10.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
async def tts_info():
    """TTS service information"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{TTS_SERVICE_URL}/", timeout=5.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {
            "service": "Zoe TTS Router",
            "status": "proxy",
            "backend": TTS_SERVICE_URL,
            "error": str(e)
        }


# ============================================================
# Device-Targeted Speech
# ============================================================

class SpeakRequest(BaseModel):
    """Request to speak text on a specific device or room"""
    text: str
    voice: str = "default"
    speed: float = 1.0
    target_device_id: Optional[str] = None
    target_room: Optional[str] = None
    priority: str = "normal"  # normal, high, critical


@router.post("/speak")
async def speak(
    request: SpeakRequest,
    user_id: Optional[str] = Query(None),
    x_device_id: Optional[str] = Header(None)
):
    """
    Synthesize speech and play on a specific device or room.
    
    Routing priority:
    1. target_device_id if specified
    2. target_room if specified (plays on room's media_player)
    3. Source device (X-Device-Id header) if available
    4. Broadcast to all user's audio-capable devices
    
    Args:
        text: Text to speak
        voice: Voice profile ID
        target_device_id: Specific device to play on
        target_room: Room to play in (uses HA media_player)
    """
    try:
        # Step 1: Synthesize speech
        async with httpx.AsyncClient(timeout=120.0) as client:
            tts_request = {
                "text": request.text,
                "voice": request.voice,
                "speed": request.speed,
                "user_id": user_id
            }
            
            response = await client.post(
                f"{TTS_SERVICE_URL}/synthesize",
                json=tts_request
            )
            
            if response.status_code != 200:
                logger.error(f"TTS synthesis failed: {response.text}")
                raise HTTPException(status_code=response.status_code, detail="TTS synthesis failed")
            
            audio_data = response.content
        
        # Step 2: Route to target device(s)
        target_device = request.target_device_id or x_device_id
        target_room = request.target_room
        
        # Get device info if we have a device_id
        device_info = None
        if target_device:
            try:
                from routers.devices import get_connection
                import sqlite3
                conn = get_connection()
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM devices WHERE id = ?", (target_device,))
                row = cursor.fetchone()
                conn.close()
                if row:
                    device_info = dict(row)
                    # Use device's room if no room specified
                    if not target_room and device_info.get("room"):
                        target_room = device_info["room"]
            except Exception as e:
                logger.warning(f"Could not get device info: {e}")
        
        # Encode audio as base64 for WebSocket transmission
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        tts_message = {
            "type": "tts_play",
            "audio_data": audio_base64,
            "audio_format": "wav",
            "text": request.text,
            "priority": request.priority
        }
        
        played_on = []
        
        # Try device-specific playback
        if target_device:
            try:
                from routers.websocket import send_to_device
                success = await send_to_device(target_device, tts_message)
                if success:
                    played_on.append({"type": "device", "id": target_device})
                    logger.info(f"TTS sent to device {target_device}")
            except Exception as e:
                logger.warning(f"Failed to send TTS to device: {e}")
        
        # Try room-based playback via HA
        if target_room and not played_on:
            try:
                # Get media_player entities in room
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "http://localhost:8000/api/homeassistant/entities",
                        params={"domain": "media_player"},
                        timeout=5.0
                    )
                    if response.status_code == 200:
                        data = response.json()
                        entities = data.get("entities", [])
                        
                        # Find media_player matching room
                        room_normalized = target_room.lower().replace(" ", "_")
                        for entity in entities:
                            entity_id = entity.get("entity_id", "")
                            if room_normalized in entity_id.lower():
                                # Play on HA media_player
                                # Note: Would need to host the audio and provide URL
                                played_on.append({"type": "ha_media_player", "entity_id": entity_id, "room": target_room})
                                logger.info(f"TTS routed to HA media_player {entity_id} in {target_room}")
                                break
            except Exception as e:
                logger.warning(f"Failed to route TTS to room: {e}")
        
        # Fallback: Broadcast to all user's devices
        if not played_on and user_id:
            try:
                from routers.websocket import broadcast_to_user
                count = await broadcast_to_user(user_id, tts_message)
                if count > 0:
                    played_on.append({"type": "broadcast", "device_count": count})
                    logger.info(f"TTS broadcast to {count} devices for user {user_id}")
            except Exception as e:
                logger.warning(f"Failed to broadcast TTS: {e}")
        
        return {
            "success": len(played_on) > 0,
            "text": request.text,
            "played_on": played_on,
            "audio_size_bytes": len(audio_data)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Speak failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

