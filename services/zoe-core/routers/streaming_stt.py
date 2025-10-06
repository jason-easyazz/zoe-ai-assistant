from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, AsyncGenerator
import asyncio
import json
import logging
import whisper
import numpy as np
import io
import wave
import threading
import queue
from datetime import datetime
import sqlite3
import httpx

router = APIRouter(prefix="/api/streaming-stt", tags=["streaming-stt"])

logger = logging.getLogger(__name__)

class STTConfig(BaseModel):
    model_config = {"protected_namespaces": ()}
    
    model_size: str = "base"
    language: Optional[str] = None
    task: str = "transcribe"
    temperature: float = 0.0
    chunk_length: int = 30  # seconds
    overlap: int = 2  # seconds

class TranscriptionResult(BaseModel):
    text: str
    confidence: float
    timestamp: str
    is_final: bool = False
    language: Optional[str] = None

class VoiceCommand(BaseModel):
    command: str
    intent: str
    parameters: Dict[str, Any]
    confidence: float
    timestamp: str

class StreamingSTTEngine:
    def __init__(self):
        self.model = None
        self.config = STTConfig()
        self.conversation_context = []
        self.max_context_length = 10
        self.audio_queue = queue.Queue()
        self.is_processing = False
        self.processing_thread = None
        
        # Load Whisper model
        self.load_model()
    
    def load_model(self):
        """Load Whisper model for transcription"""
        try:
            logger.info(f"🔄 Loading Whisper model: {self.config.model_size}")
            self.model = whisper.load_model(self.config.model_size)
            logger.info("✅ Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"❌ Failed to load Whisper model: {e}")
            self.model = None
    
    def add_to_context(self, text: str, is_final: bool = True):
        """Add transcription to conversation context"""
        if is_final and text.strip():
            self.conversation_context.append({
                "text": text,
                "timestamp": datetime.now().isoformat(),
                "is_final": is_final
            })
            
            # Keep only recent context
            if len(self.conversation_context) > self.max_context_length:
                self.conversation_context = self.conversation_context[-self.max_context_length:]
    
    def get_context(self) -> str:
        """Get current conversation context"""
        return " ".join([item["text"] for item in self.conversation_context[-5:]])
    
    def process_audio_chunk(self, audio_data: bytes) -> TranscriptionResult:
        """Process a chunk of audio data"""
        try:
            if self.model is None:
                return TranscriptionResult(
                    text="",
                    confidence=0.0,
                    timestamp=datetime.now().isoformat(),
                    is_final=False
                )
            
            # Convert bytes to numpy array
            audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            
            # Transcribe with Whisper
            result = self.model.transcribe(
                audio_np,
                language=self.config.language,
                task=self.config.task,
                temperature=self.config.temperature,
                fp16=False  # Use fp32 for better compatibility
            )
            
            # Extract text and confidence
            text = result["text"].strip()
            segments = result.get("segments", [])
            confidence = 0.0
            
            if segments:
                # Calculate average confidence from segments
                confidences = [seg.get("no_speech_prob", 0.0) for seg in segments]
                confidence = 1.0 - np.mean(confidences) if confidences else 0.0
            
            return TranscriptionResult(
                text=text,
                confidence=float(confidence),
                timestamp=datetime.now().isoformat(),
                is_final=True,
                language=result.get("language")
            )
            
        except Exception as e:
            logger.error(f"❌ Error processing audio chunk: {e}")
            return TranscriptionResult(
                text="",
                confidence=0.0,
                timestamp=datetime.now().isoformat(),
                is_final=False
            )
    
    def process_voice_command(self, text: str) -> Optional[VoiceCommand]:
        """Process transcribed text for voice commands"""
        try:
            text_lower = text.lower().strip()
            
            # Task creation commands
            if any(phrase in text_lower for phrase in ["create task", "add task", "new task", "remind me"]):
                return VoiceCommand(
                    command=text,
                    intent="create_task",
                    parameters={"description": text},
                    confidence=0.8,
                    timestamp=datetime.now().isoformat()
                )
            
            # Calendar commands
            elif any(phrase in text_lower for phrase in ["schedule", "meeting", "appointment", "calendar"]):
                return VoiceCommand(
                    command=text,
                    intent="calendar",
                    parameters={"description": text},
                    confidence=0.8,
                    timestamp=datetime.now().isoformat()
                )
            
            # List commands
            elif any(phrase in text_lower for phrase in ["add to list", "shopping", "bucket list"]):
                return VoiceCommand(
                    command=text,
                    intent="add_to_list",
                    parameters={"description": text},
                    confidence=0.8,
                    timestamp=datetime.now().isoformat()
                )
            
            # General question
            elif text_lower.endswith("?") or any(phrase in text_lower for phrase in ["what", "how", "when", "where", "why"]):
                return VoiceCommand(
                    command=text,
                    intent="question",
                    parameters={"question": text},
                    confidence=0.7,
                    timestamp=datetime.now().isoformat()
                )
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error processing voice command: {e}")
            return None
    
    async def execute_voice_command(self, command: VoiceCommand):
        """Execute a voice command"""
        try:
            if command.intent == "create_task":
                # Create task via lists API
                async with httpx.AsyncClient() as client:
                    await client.post(
                        "http://localhost:8000/api/lists/tasks",
                        json={
                            "text": command.parameters["description"],
                            "category": "personal",
                            "priority": "medium"
                        }
                    )
                logger.info(f"✅ Created task: {command.parameters['description']}")
            
            elif command.intent == "calendar":
                # Add to calendar
                async with httpx.AsyncClient() as client:
                    await client.post(
                        "http://localhost:8000/api/calendar/events",
                        json={
                            "title": command.parameters["description"],
                            "start_time": datetime.now().isoformat(),
                            "end_time": (datetime.now().timestamp() + 3600).isoformat(),
                            "description": "Created via voice command"
                        }
                    )
                logger.info(f"✅ Added to calendar: {command.parameters['description']}")
            
            elif command.intent == "add_to_list":
                # Add to shopping list
                async with httpx.AsyncClient() as client:
                    await client.post(
                        "http://localhost:8000/api/lists/shopping",
                        json={
                            "text": command.parameters["description"],
                            "category": "personal"
                        }
                    )
                logger.info(f"✅ Added to list: {command.parameters['description']}")
            
            elif command.intent == "question":
                # Process question with AI
                logger.info(f"🤔 Question received: {command.parameters['question']}")
                # This would integrate with the AI system
            
        except Exception as e:
            logger.error(f"❌ Error executing voice command: {e}")

# Initialize STT engine
stt_engine = StreamingSTTEngine()

@router.get("/config")
async def get_stt_config():
    """Get current STT configuration"""
    return {
        "config": stt_engine.config.dict(),
        "model_loaded": stt_engine.model is not None,
        "context_length": len(stt_engine.conversation_context)
    }

@router.post("/config")
async def update_stt_config(config: STTConfig):
    """Update STT configuration"""
    try:
        stt_engine.config = config
        # Reload model if size changed
        if stt_engine.model is None or config.model_size != stt_engine.config.model_size:
            stt_engine.load_model()
        
        return {"message": "Configuration updated", "config": config.dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {str(e)}")

@router.websocket("/stream")
async def stream_transcription(websocket: WebSocket):
    """WebSocket endpoint for real-time transcription"""
    await websocket.accept()
    
    try:
        while True:
            # Receive audio data
            data = await websocket.receive_bytes()
            
            # Process audio chunk
            result = stt_engine.process_audio_chunk(data)
            
            # Add to context
            stt_engine.add_to_context(result.text, result.is_final)
            
            # Send transcription result
            await websocket.send_json(result.dict())
            
            # Process voice commands if final
            if result.is_final and result.text.strip():
                command = stt_engine.process_voice_command(result.text)
                if command:
                    # Send command info
                    await websocket.send_json({
                        "type": "voice_command",
                        "command": command.dict()
                    })
                    
                    # Execute command
                    await stt_engine.execute_voice_command(command)
            
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()

@router.post("/transcribe")
async def transcribe_audio(audio_data: bytes):
    """Transcribe a single audio file"""
    try:
        result = stt_engine.process_audio_chunk(audio_data)
        stt_engine.add_to_context(result.text, result.is_final)
        
        return {
            "transcription": result.dict(),
            "context": stt_engine.get_context()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

@router.get("/context")
async def get_conversation_context():
    """Get current conversation context"""
    return {
        "context": stt_engine.conversation_context,
        "summary": stt_engine.get_context()
    }

@router.delete("/context")
async def clear_conversation_context():
    """Clear conversation context"""
    stt_engine.conversation_context = []
    return {"message": "Context cleared"}

@router.post("/voice-command")
async def process_voice_command(text: str):
    """Process a voice command from text"""
    try:
        command = stt_engine.process_voice_command(text)
        if command:
            await stt_engine.execute_voice_command(command)
            return {"command": command.dict(), "executed": True}
        else:
            return {"message": "No voice command detected", "executed": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Voice command processing failed: {str(e)}")

@router.get("/status")
async def get_stt_status():
    """Get STT engine status"""
    return {
        "model_loaded": stt_engine.model is not None,
        "model_size": stt_engine.config.model_size,
        "is_processing": stt_engine.is_processing,
        "context_length": len(stt_engine.conversation_context),
        "last_activity": datetime.now().isoformat()
    }

@router.post("/test")
async def test_transcription():
    """Test transcription with sample audio"""
    try:
        # Create a simple test audio (sine wave)
        sample_rate = 16000
        duration = 2  # seconds
        frequency = 440  # Hz
        
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        audio = np.sin(frequency * 2 * np.pi * t) * 0.5
        audio_int16 = (audio * 32767).astype(np.int16)
        
        # Convert to bytes
        audio_bytes = audio_int16.tobytes()
        
        # Transcribe
        result = stt_engine.process_audio_chunk(audio_bytes)
        
        return {
            "test_result": result.dict(),
            "message": "Test completed"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Test failed: {str(e)}")
