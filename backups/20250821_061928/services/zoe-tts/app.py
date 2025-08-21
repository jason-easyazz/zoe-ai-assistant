from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import subprocess
import tempfile
import os
import uuid

app = FastAPI(title="Zoe TTS Service")

class TTSRequest(BaseModel):
    text: str
    voice: str = "default"
    speed: int = 140  # Slower, clearer speech

@app.get("/health")
async def health():
    return {"status": "healthy", "engine": "espeak-optimized"}

@app.post("/synthesize")
async def synthesize_speech(request: TTSRequest):
    """Generate clearer speech optimized for Whisper"""
    try:
        # Create unique filename
        filename = f"/tmp/tts_{uuid.uuid4().hex}.wav"
        
        # Use espeak with optimized settings for clarity
        # -s: speed (140 is slower and clearer)
        # -a: amplitude/volume (150 out of 200)
        # -p: pitch (50 is normal)
        # -g: word gap (10ms between words)
        result = subprocess.run([
            "espeak",
            "-w", filename,
            "-s", str(request.speed),  # Slower speed for clarity
            "-a", "150",               # Good volume
            "-p", "50",                # Normal pitch
            "-g", "10",                # Small gap between words
            request.text
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"TTS failed: {result.stderr}")
        
        # Post-process audio for better quality
        processed_filename = f"/tmp/tts_processed_{uuid.uuid4().hex}.wav"
        
        # Use ffmpeg to normalize and enhance audio
        enhance_cmd = [
            "ffmpeg", "-i", filename,
            "-af", "highpass=f=200,lowpass=f=3000,volume=1.5",  # Filter and boost
            "-ar", "16000",  # 16kHz (what Whisper expects)
            "-ac", "1",      # Mono
            "-y",            # Overwrite
            processed_filename
        ]
        
        enhance_result = subprocess.run(enhance_cmd, capture_output=True, text=True)
        
        # Use processed file if enhancement worked, otherwise original
        final_file = processed_filename if enhance_result.returncode == 0 else filename
        
        # Return the audio file
        return FileResponse(
            final_file,
            media_type="audio/wav",
            filename="speech.wav",
            headers={"X-Audio-Quality": "optimized-for-stt"}
        )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/voices")
async def list_voices():
    return {
        "voices": [
            {"id": "default", "name": "Clear Voice", "speed": 140},
            {"id": "fast", "name": "Fast Voice", "speed": 175},
            {"id": "slow", "name": "Slow Voice", "speed": 120}
        ]
    }
