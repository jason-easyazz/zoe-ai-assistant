"""
Zoe Voice - Whisper STT Service
Real-time speech-to-text for Zoe AI Assistant
"""

import asyncio
import io
import json
import logging
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import torch
import uvicorn
import whisper
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Voice STT Service", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global whisper model
whisper_model = None
MODEL_NAME = "base"  # Good balance of speed/accuracy for Pi 5

class VoiceManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"Client {client_id} connected for voice streaming")
        
    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"Client {client_id} disconnected")

voice_manager = VoiceManager()

@app.on_event("startup")
async def load_whisper_model():
    global whisper_model
    try:
        logger.info(f"Loading Whisper model: {MODEL_NAME}")
        whisper_model = whisper.load_model(MODEL_NAME)
        logger.info("✅ Whisper model loaded successfully")
    except Exception as e:
        logger.error(f"❌ Failed to load Whisper model: {e}")
        raise

@app.post("/api/transcribe")
async def transcribe_audio(audio_file: UploadFile = File(...)):
    """Transcribe uploaded audio file to text"""
    
    if not whisper_model:
        raise HTTPException(status_code=503, detail="Whisper model not loaded")
    
    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
            content = await audio_file.read()
            tmp_file.write(content)
            tmp_file_path = tmp_file.name
        
        # Transcribe audio
        logger.info(f"Transcribing audio: {audio_file.filename}")
        result = whisper_model.transcribe(tmp_file_path, fp16=False)
        
        # Clean up
        Path(tmp_file_path).unlink()
        
        transcription = {
            "text": result["text"].strip(),
            "language": result.get("language", "unknown"),
            "confidence": getattr(result, "confidence", None),
            "duration": getattr(result, "duration", None),
            "timestamp": datetime.now().isoformat()
        }
        
        logger.info(f"Transcription successful: {len(transcription['text'])} chars")
        return transcription
        
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

@app.websocket("/ws/transcribe/{client_id}")
async def websocket_transcribe(websocket: WebSocket, client_id: str):
    """Real-time voice transcription via WebSocket"""
    
    await voice_manager.connect(websocket, client_id)
    
    try:
        while True:
            # Receive audio data
            data = await websocket.receive_bytes()
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                tmp_file.write(data)
                tmp_file_path = tmp_file.name
            
            try:
                # Quick transcription for real-time
                result = whisper_model.transcribe(tmp_file_path, fp16=False)
                
                # Send result back
                await websocket.send_json({
                    "type": "transcription",
                    "text": result["text"].strip(),
                    "language": result.get("language", "en"),
                    "timestamp": datetime.now().isoformat()
                })
                
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Transcription error: {str(e)}"
                })
            finally:
                # Clean up
                Path(tmp_file_path).unlink()
                
    except Exception as e:
        logger.error(f"WebSocket error for {client_id}: {e}")
    finally:
        voice_manager.disconnect(client_id)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy" if whisper_model else "loading",
        "model": MODEL_NAME,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/models")
async def list_models():
    """List available Whisper models"""
    return {
        "available_models": ["tiny", "base", "small", "medium", "large"],
        "current_model": MODEL_NAME,
        "recommended_for_pi5": "base"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9001)
