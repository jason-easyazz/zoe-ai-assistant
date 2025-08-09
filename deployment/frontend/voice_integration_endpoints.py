# Add these voice endpoints to your services/zoe-core/main.py

import base64
import tempfile
from fastapi import UploadFile, File
from pathlib import Path

# Voice Models
class VoiceTranscription(BaseModel):
    audio_data: str  # Base64 encoded audio
    format: str = Field(default="webm")
    language: str = Field(default="en")

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    voice: str = Field(default="female")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)

# Voice Endpoints

@app.post("/api/voice/transcribe")
async def transcribe_audio(transcription: VoiceTranscription):
    """Transcribe audio using Whisper service"""
    try:
        # Decode base64 audio
        audio_bytes = base64.b64decode(transcription.audio_data)
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(suffix=f".{transcription.format}", delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_file_path = temp_file.name
        
        try:
            # Send to Whisper service
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(temp_file_path, 'rb') as audio_file:
                    files = {"audio": (f"audio.{transcription.format}", audio_file, f"audio/{transcription.format}")}
                    data = {"language": transcription.language}
                    
                    response = await client.post(
                        f"{CONFIG['whisper_url']}/transcribe",
                        files=files,
                        data=data
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        return {
                            "text": result.get("text", ""),
                            "confidence": result.get("confidence", 0.0),
                            "duration": result.get("duration", 0.0)
                        }
                    else:
                        logger.error(f"Whisper service error: {response.status_code}")
                        return {"error": "Speech recognition failed"}
                        
        finally:
            # Clean up temp file
            Path(temp_file_path).unlink(missing_ok=True)
            
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return {"error": f"Transcription failed: {str(e)}"}

@app.post("/api/voice/transcribe/upload")
async def transcribe_upload(audio: UploadFile = File(...)):
    """Transcribe uploaded audio file"""
    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            content = await audio.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        try:
            # Send to Whisper service
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(temp_file_path, 'rb') as audio_file:
                    files = {"audio": (audio.filename, audio_file, audio.content_type)}
                    
                    response = await client.post(
                        f"{CONFIG['whisper_url']}/transcribe",
                        files=files
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        return {
                            "text": result.get("text", ""),
                            "confidence": result.get("confidence", 0.0),
                            "duration": result.get("duration", 0.0),
                            "filename": audio.filename
                        }
                    else:
                        raise HTTPException(status_code=500, detail="Speech recognition service error")
                        
        finally:
            # Clean up temp file
            Path(temp_file_path).unlink(missing_ok=True)
            
    except Exception as e:
        logger.error(f"Upload transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

@app.post("/api/voice/synthesize")
async def synthesize_speech(tts_request: TTSRequest):
    """Convert text to speech using TTS service"""
    try:
        # Send to TTS service
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{CONFIG['tts_url']}/synthesize",
                json={
                    "text": tts_request.text,
                    "voice": tts_request.voice,
                    "speed": tts_request.speed,
                    "format": "wav"
                }
            )
            
            if response.status_code == 200:
                # Return audio as base64
                audio_data = base64.b64encode(response.content).decode('utf-8')
                return {
                    "audio_data": audio_data,
                    "format": "wav",
                    "text": tts_request.text,
                    "duration": len(tts_request.text) * 0.1  # Rough estimate
                }
            else:
                logger.error(f"TTS service error: {response.status_code}")
                raise HTTPException(status_code=500, detail="Speech synthesis failed")
                
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {str(e)}")

@app.get("/api/voice/status")
async def voice_service_status():
    """Check status of voice services"""
    status = {
        "whisper": {"status": "unknown", "url": CONFIG['whisper_url']},
        "tts": {"status": "unknown", "url": CONFIG['tts_url']}
    }
    
    # Check Whisper service
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CONFIG['whisper_url']}/health")
            status["whisper"]["status"] = "healthy" if response.status_code == 200 else "error"
    except Exception:
        status["whisper"]["status"] = "unreachable"
    
    # Check TTS service
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CONFIG['tts_url']}/health")
            status["tts"]["status"] = "healthy" if response.status_code == 200 else "error"
    except Exception:
        status["tts"]["status"] = "unreachable"
    
    return status

@app.websocket("/ws/voice/{client_id}")
async def websocket_voice(websocket: WebSocket, client_id: str):
    """WebSocket for real-time voice streaming"""
    await websocket.accept()
    logger.info(f"Voice WebSocket connected: {client_id}")
    
    try:
        while True:
            # Receive audio chunk
            data = await websocket.receive_bytes()
            
            # Process audio chunk (simplified - in production you'd buffer and process)
            try:
                # Send chunk to Whisper for real-time transcription
                async with httpx.AsyncClient(timeout=10.0) as client:
                    files = {"audio": ("chunk.webm", data, "audio/webm")}
                    response = await client.post(
                        f"{CONFIG['whisper_url']}/transcribe/stream",
                        files=files
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        # Send partial transcription back
                        await websocket.send_json({
                            "type": "transcription",
                            "text": result.get("text", ""),
                            "is_final": result.get("is_final", False)
                        })
            
            except Exception as e:
                logger.error(f"Voice streaming error: {e}")
                await websocket.send_json({
                    "type": "error",
                    "message": "Voice processing error"
                })
                
    except WebSocketDisconnect:
        logger.info(f"Voice WebSocket disconnected: {client_id}")

# Enhanced voice chat workflow
@app.post("/api/voice/chat")
async def voice_chat(audio_data: VoiceTranscription):
    """Complete voice chat workflow: STT -> AI -> TTS"""
    try:
        # Step 1: Transcribe audio
        transcription_result = await transcribe_audio(audio_data)
        
        if "error" in transcription_result:
            return transcription_result
        
        user_text = transcription_result["text"]
        if not user_text.strip():
            return {"error": "No speech detected"}
        
        # Step 2: Get AI response
        chat_msg = ChatMessage(message=user_text, user_id="default")
        
        # Get streaming response and collect full text
        ai_response = ""
        async for chunk in stream_ai_response(user_text, None, "default"):
            ai_response += chunk
        
        # Step 3: Synthesize AI response
        tts_result = await synthesize_speech(TTSRequest(text=ai_response))
        
        if "error" not in tts_result:
            # Save conversation
            await save_conversation(user_text, ai_response, None, "default")
        
        return {
            "transcription": {
                "text": user_text,
                "confidence": transcription_result.get("confidence", 0.0)
            },
            "ai_response": {
                "text": ai_response,
                "audio_data": tts_result.get("audio_data", ""),
                "format": "wav"
            },
            "conversation_saved": True
        }
        
    except Exception as e:
        logger.error(f"Voice chat error: {e}")
        return {"error": f"Voice chat failed: {str(e)}"}

# Voice settings endpoints
@app.get("/api/voice/settings")
async def get_voice_settings():
    """Get voice configuration settings"""
    settings = {
        "stt_enabled": await get_setting("voice", "stt_enabled", "true") == "true",
        "tts_enabled": await get_setting("voice", "tts_enabled", "true") == "true",
        "voice_model": await get_setting("voice", "voice_model", "female"),
        "speech_speed": float(await get_setting("voice", "speech_speed", "1.0")),
        "auto_listen": await get_setting("voice", "auto_listen", "false") == "true",
        "wake_word": await get_setting("voice", "wake_word", "hey zoe")
    }
    return settings

@app.post("/api/voice/settings")
async def update_voice_settings(settings: dict):
    """Update voice configuration settings"""
    try:
        for key, value in settings.items():
            await set_setting("voice", key, str(value))
        
        return {"success": True, "message": "Voice settings updated"}
    except Exception as e:
        logger.error(f"Voice settings update error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update voice settings")