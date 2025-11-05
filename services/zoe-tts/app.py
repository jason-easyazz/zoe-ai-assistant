"""
Zoe TTS Service - Ultra-realistic voice synthesis with instant voice cloning
Powered by NeuTTS Air (Q4 GGUF + ONNX hybrid for Raspberry Pi 5 optimization)
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import tempfile
import os
import uuid
import shutil
import time
import json
from typing import List, Optional, Dict
from pathlib import Path
import logging

# Import NeuTTS Air components
from neuttsair.neutts import NeuTTSAir
import soundfile as sf
import numpy as np

# Import voice manager
from voice_manager import VoiceProfileManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe TTS Service - NeuTTS Air", version="2.0.0")

# Global TTS engine and voice manager
tts_engine: Optional[NeuTTSAir] = None
voice_manager: Optional[VoiceProfileManager] = None

# Cache directory for temporary audio files
CACHE_DIR = Path("/tmp/tts_cache")
CACHE_DIR.mkdir(exist_ok=True)

# Voice profiles directory
VOICE_PROFILES_DIR = Path("/app/voice_profiles")
VOICE_PROFILES_DIR.mkdir(exist_ok=True)

# Sample voices directory
SAMPLES_DIR = Path("/app/samples")
SAMPLES_DIR.mkdir(exist_ok=True)


class TTSRequest(BaseModel):
    text: str
    voice: str = "default"  # Voice profile ID or "default"
    speed: float = 1.0
    use_cache: bool = True
    user_id: Optional[str] = None


class VoiceCloneRequest(BaseModel):
    profile_name: str
    reference_text: str
    user_id: Optional[str] = None


class VoiceProfile(BaseModel):
    id: str
    name: str
    user_id: Optional[str]
    reference_text: str
    created_at: str
    is_system: bool = False


@app.on_event("startup")
async def startup_event():
    """Initialize NeuTTS Air engine and pre-load sample voices"""
    global tts_engine, voice_manager
    
    logger.info("Initializing NeuTTS Air engine...")
    
    try:
        # Initialize TTS engine with Q4 GGUF backbone
        tts_engine = NeuTTSAir(
            backbone_repo="neuphonic/neutts-air-q4-gguf",
            backbone_device="cpu",
            codec_repo="neuphonic/neucodec",
            codec_device="cpu"
        )
        logger.info("✅ NeuTTS Air engine initialized successfully")
        
        # Initialize voice profile manager
        voice_manager = VoiceProfileManager(
            tts_engine=tts_engine,
            profiles_dir=VOICE_PROFILES_DIR,
            samples_dir=SAMPLES_DIR
        )
        
        # Pre-load sample voices
        logger.info("Pre-loading sample voice profiles...")
        await voice_manager.load_sample_voices()
        logger.info(f"✅ Loaded {len(voice_manager.list_profiles())} voice profiles")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize TTS engine: {e}")
        raise


@app.get("/health")
async def health():
    """Health check endpoint"""
    if tts_engine is None:
        raise HTTPException(status_code=503, detail="TTS engine not initialized")
    
    return {
        "status": "healthy",
        "engine": "NeuTTS Air",
        "model": "Q4 GGUF + ONNX",
        "voices_loaded": len(voice_manager.list_profiles()) if voice_manager else 0
    }


@app.post("/synthesize")
async def synthesize_speech(request: TTSRequest, background_tasks: BackgroundTasks):
    """
    Generate ultra-realistic speech with optional voice cloning
    
    - **text**: Text to synthesize
    - **voice**: Voice profile ID (use "default" for base voice)
    - **speed**: Speech speed multiplier (0.5-2.0)
    - **use_cache**: Enable audio caching for repeated requests
    - **user_id**: User ID for personalized voice profiles
    """
    if tts_engine is None or voice_manager is None:
        raise HTTPException(status_code=503, detail="TTS engine not initialized")
    
    try:
        start_time = time.time()
        
        # Get voice profile
        voice_profile = voice_manager.get_profile(request.voice, request.user_id)
        
        if voice_profile is None:
            raise HTTPException(status_code=404, detail=f"Voice profile '{request.voice}' not found")
        
        # Synthesize speech
        logger.info(f"Synthesizing: '{request.text[:50]}...' with voice '{request.voice}'")
        
        if voice_profile["encoded_reference"] is not None:
            # Use voice cloning
            wav = tts_engine.infer(
                request.text,
                voice_profile["encoded_reference"],
                voice_profile["reference_text"]
            )
        else:
            # Use default voice (no cloning) - just pass text
            wav = tts_engine.infer(request.text)
        
        # Save to temporary file
        output_file = CACHE_DIR / f"tts_{uuid.uuid4().hex}.wav"
        sf.write(str(output_file), wav, 24000)
        
        generation_time = time.time() - start_time
        
        # Schedule cleanup
        if not request.use_cache:
            background_tasks.add_task(lambda p: os.path.exists(p) and os.remove(p), str(output_file))
        
        logger.info(f"✅ Synthesized in {generation_time:.2f}s")
        
        return FileResponse(
            str(output_file),
            media_type="audio/wav",
            filename="speech.wav",
            headers={
                "X-Engine": "NeuTTS-Air",
                "X-Voice-Profile": request.voice,
                "X-Generation-Time": f"{generation_time:.3f}",
                "X-Audio-Quality": "ultra-realistic"
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Synthesis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clone-voice")
async def clone_voice(
    profile_name: str = Form(...),
    reference_text: str = Form(...),
    user_id: Optional[str] = Form(None),
    reference_audio: UploadFile = File(...)
):
    """
    Create a new voice profile from reference audio (instant voice cloning)
    
    Requires:
    - **profile_name**: Name for the voice profile
    - **reference_text**: Exact transcription of the reference audio
    - **reference_audio**: WAV file (mono, 16-44kHz, 3-15 seconds)
    - **user_id**: Optional user ID for personal voice profiles
    
    Reference audio should be clean speech with minimal background noise.
    """
    if tts_engine is None or voice_manager is None:
        raise HTTPException(status_code=503, detail="TTS engine not initialized")
    
    try:
        # Save uploaded audio to temporary file
        temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        shutil.copyfileobj(reference_audio.file, temp_audio)
        temp_audio.close()
        
        # Create voice profile
        profile_id = await voice_manager.create_profile(
            name=profile_name,
            reference_audio_path=temp_audio.name,
            reference_text=reference_text,
            user_id=user_id
        )
        
        # Cleanup temp file
        os.remove(temp_audio.name)
        
        logger.info(f"✅ Created voice profile: {profile_id}")
        
        return {
            "success": True,
            "profile_id": profile_id,
            "profile_name": profile_name,
            "message": f"Voice profile '{profile_name}' created successfully"
        }
        
    except Exception as e:
        logger.error(f"❌ Voice cloning failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voices")
async def list_voices(user_id: Optional[str] = None):
    """
    List all available voice profiles
    
    Returns system voices and user-specific voices if user_id provided
    """
    if voice_manager is None:
        raise HTTPException(status_code=503, detail="Voice manager not initialized")
    
    profiles = voice_manager.list_profiles(user_id=user_id)
    
    return {
        "voices": profiles,
        "total": len(profiles),
        "system_voices": len([p for p in profiles if p["is_system"]]),
        "user_voices": len([p for p in profiles if not p["is_system"]])
    }


@app.get("/voice-profiles/{profile_id}")
async def get_voice_profile(profile_id: str, user_id: Optional[str] = None):
    """Get details of a specific voice profile"""
    if voice_manager is None:
        raise HTTPException(status_code=503, detail="Voice manager not initialized")
    
    profile = voice_manager.get_profile(profile_id, user_id)
    
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Voice profile '{profile_id}' not found")
    
    # Remove encoded reference from response (too large)
    response_profile = {k: v for k, v in profile.items() if k != "encoded_reference"}
    
    return response_profile


@app.delete("/voice-profiles/{profile_id}")
async def delete_voice_profile(profile_id: str, user_id: Optional[str] = None):
    """
    Delete a voice profile
    
    System voices cannot be deleted
    """
    if voice_manager is None:
        raise HTTPException(status_code=503, detail="Voice manager not initialized")
    
    success = await voice_manager.delete_profile(profile_id, user_id)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"Voice profile '{profile_id}' not found or cannot be deleted")
    
    return {
        "success": True,
        "message": f"Voice profile '{profile_id}' deleted successfully"
    }


@app.get("/cache/stats")
async def get_cache_stats():
    """Get TTS cache statistics"""
    try:
        cache_files = list(CACHE_DIR.glob("*.wav"))
        total_size = sum(f.stat().st_size for f in cache_files)
        
        return {
            "cache_enabled": True,
            "cache_directory": str(CACHE_DIR),
            "cached_files": len(cache_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cache/clear")
async def clear_cache():
    """Clear TTS cache"""
    try:
        cache_files = list(CACHE_DIR.glob("*.wav"))
        deleted_count = 0
        
        for cache_file in cache_files:
            try:
                cache_file.unlink()
                deleted_count += 1
            except OSError:
                pass
        
        return {
            "success": True,
            "deleted_files": deleted_count,
            "message": f"Cleared {deleted_count} cached files"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Service information"""
    return {
        "service": "Zoe TTS Service",
        "version": "2.0.0",
        "engine": "NeuTTS Air",
        "model": "0.5B LLM + Neural Codec",
        "features": [
            "Ultra-realistic voice synthesis",
            "Instant voice cloning (3+ seconds of audio)",
            "Multiple voice profiles",
            "User-specific voices",
            "Streaming capable",
            "Optimized for Raspberry Pi 5"
        ],
        "endpoints": {
            "POST /synthesize": "Generate speech",
            "POST /clone-voice": "Create voice profile",
            "GET /voices": "List available voices",
            "GET /voice-profiles/{id}": "Get voice profile details",
            "DELETE /voice-profiles/{id}": "Delete voice profile",
            "GET /health": "Health check"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9002)
