"""
TTS Router - Proxy to zoe-tts service with user context
Provides voice synthesis and voice cloning management
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
import httpx
import logging
import os

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

