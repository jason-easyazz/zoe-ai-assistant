"""
Voice Router
=============

Phase 5: API endpoints for voice input/output.

Endpoints:
    POST /api/voice/transcribe    -- Upload audio, get transcription
    POST /api/voice/synthesize    -- Send text, get audio
    POST /api/voice/process       -- Full pipeline: audio -> text -> response -> audio
    GET  /api/voice/status        -- Voice service status
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from auth_integration import validate_session, AuthenticatedSession
from voice.orchestrator import voice_orchestrator
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice-pipeline", tags=["voice-pipeline"])


class SynthesizeRequest(BaseModel):
    text: str
    voice: str = None


@router.get("/status")
async def voice_status(session: AuthenticatedSession = Depends(validate_session)):
    """Get voice pipeline status and statistics."""
    services = await voice_orchestrator.check_services()
    stats = voice_orchestrator.get_stats()
    return {
        "services": services,
        "stats": stats,
        "ready": services.get("stt", False) and services.get("tts", False),
    }


@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    model: str = Query("tiny", description="Whisper model: tiny or base"),
    session: AuthenticatedSession = Depends(validate_session),
):
    """Upload audio and get transcription."""
    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="No audio data")

    text = await voice_orchestrator.transcribe(audio_data, model=model)
    if text is None:
        raise HTTPException(status_code=503, detail="STT service unavailable")

    return {"text": text, "model": model}


@router.post("/synthesize")
async def synthesize_text(
    request: SynthesizeRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Convert text to speech audio."""
    audio = await voice_orchestrator.synthesize(request.text, voice=request.voice)
    if audio is None:
        raise HTTPException(status_code=503, detail="TTS service unavailable")

    return Response(content=audio, media_type="audio/wav")


@router.post("/process")
async def process_voice(
    audio: UploadFile = File(...),
    session: AuthenticatedSession = Depends(validate_session),
):
    """Full voice pipeline: audio -> transcription -> response -> audio.

    Returns both the text response and audio bytes.
    """
    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="No audio data")

    result = await voice_orchestrator.process_voice_input(
        audio_data=audio_data,
        user_id=session.user_id,
    )

    return {
        "transcription": result["transcription"],
        "response_text": result["response_text"],
        "has_audio": result["audio"] is not None,
        "timings": result["timings"],
    }
