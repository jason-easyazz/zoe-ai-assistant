"""
Zoe Voice - TTS Service
High-quality text-to-speech for Zoe AI Assistant
"""

import asyncio
import base64
import io
import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

# Try to import TTS
try:
    from TTS.api import TTS
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    logging.warning("TTS library not available - running in fallback mode")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Voice TTS Service", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global TTS model
tts_model = None
MODEL_NAME = "tts_models/en/ljspeech/tacotron2-DDC"

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    voice: Optional[str] = None
    speed: Optional[float] = Field(1.0, ge=0.5, le=2.0)
    language: Optional[str] = "en"

class VoiceStreamer:
    def __init__(self):
        self.active_connections = {}
        
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"TTS client {client_id} connected")
        
    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"TTS client {client_id} disconnected")

voice_streamer = VoiceStreamer()

@app.on_event("startup")
async def load_tts_model():
    global tts_model
    
    if not TTS_AVAILABLE:
        logger.warning("TTS not available - service will run in limited mode")
        return
    
    try:
        logger.info(f"Loading TTS model: {MODEL_NAME}")
        tts_model = TTS(MODEL_NAME)
        logger.info("✅ TTS model loaded successfully")
    except Exception as e:
        logger.error(f"❌ Failed to load TTS model: {e}")

@app.post("/api/synthesize")
async def synthesize_speech(request: TTSRequest):
    """Convert text to speech and return audio file"""
    
    if not TTS_AVAILABLE or not tts_model:
        raise HTTPException(status_code=503, detail="TTS service not available")
    
    try:
        # Create temporary output file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
            output_path = tmp_file.name
        
        # Generate speech
        logger.info(f"Synthesizing: {request.text[:50]}...")
        tts_model.tts_to_file(
            text=request.text,
            file_path=output_path,
            speaker=request.voice,
            language=request.language
        )
        
        # Read generated audio
        with open(output_path, "rb") as audio_file:
            audio_data = audio_file.read()
        
        # Clean up
        Path(output_path).unlink()
        
        return Response(
            content=audio_data,
            media_type="audio/wav",
            headers={
                "Content-Disposition": f"attachment; filename=zoe_speech_{datetime.now().strftime('%H%M%S')}.wav"
            }
        )
        
    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {str(e)}")

@app.post("/api/synthesize/stream")
async def synthesize_speech_stream(request: TTSRequest):
    """Convert text to speech and return base64 encoded audio"""
    
    if not TTS_AVAILABLE or not tts_model:
        # Fallback response for when TTS isn't available
        return {
            "success": False,
            "error": "TTS service not available",
            "fallback_text": request.text,
            "timestamp": datetime.now().isoformat()
        }
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
            output_path = tmp_file.name
        
        tts_model.tts_to_file(text=request.text, file_path=output_path)
        
        with open(output_path, "rb") as audio_file:
            audio_data = base64.b64encode(audio_file.read()).decode()
        
        Path(output_path).unlink()
        
        return {
            "success": True,
            "audio_data": audio_data,
            "format": "wav",
            "text": request.text,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Streaming TTS failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "fallback_text": request.text
        }

@app.websocket("/ws/tts/{client_id}")
async def websocket_tts(websocket: WebSocket, client_id: str):
    """Real-time TTS via WebSocket"""
    
    await voice_streamer.connect(websocket, client_id)
    
    try:
        while True:
            # Receive TTS request
            data = await websocket.receive_json()
            text = data.get("text", "")
            
            if not text:
                await websocket.send_json({
                    "type": "error",
                    "message": "No text provided"
                })
                continue
            
            if not TTS_AVAILABLE or not tts_model:
                await websocket.send_json({
                    "type": "fallback",
                    "text": text,
                    "message": "TTS not available - use browser speech synthesis"
                })
                continue
            
            try:
                # Generate speech
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                    output_path = tmp_file.name
                
                tts_model.tts_to_file(text=text, file_path=output_path)
                
                with open(output_path, "rb") as audio_file:
                    audio_data = base64.b64encode(audio_file.read()).decode()
                
                Path(output_path).unlink()
                
                await websocket.send_json({
                    "type": "audio",
                    "data": audio_data,
                    "format": "wav",
                    "text": text
                })
                
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": f"TTS error: {str(e)}"
                })
                
    except Exception as e:
        logger.error(f"WebSocket TTS error for {client_id}: {e}")
    finally:
        voice_streamer.disconnect(client_id)

@app.get("/voices")
async def list_voices():
    """List available voices"""
    
    if not TTS_AVAILABLE or not tts_model:
        return {
            "available_voices": [],
            "error": "TTS service not available"
        }
    
    try:
        voices = getattr(tts_model, "speakers", []) or []
        return {
            "available_voices": voices,
            "current_model": MODEL_NAME,
            "default_voice": voices[0] if voices else None
        }
    except Exception as e:
        return {
            "available_voices": [],
            "error": str(e)
        }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy" if (TTS_AVAILABLE and tts_model) else "limited",
        "tts_available": TTS_AVAILABLE,
        "model_loaded": tts_model is not None,
        "model": MODEL_NAME,
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9002)
