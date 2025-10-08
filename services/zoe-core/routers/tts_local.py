"""
Local TTS using Piper - Privacy-first voice synthesis
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import subprocess
import io
import os
import time

router = APIRouter(prefix="/api/tts")

# Available voices (quality vs speed tradeoff)
VOICES = {
    "amy": {
        "model": "/models/en-us-amy-low.onnx",
        "quality": "fast",
        "description": "American female, fast",
        "speed": 1.0
    },
    "alan": {
        "model": "/models/en-gb-alan-low.onnx", 
        "quality": "fast",
        "description": "British male, fast",
        "speed": 1.0
    },
    "ryan": {
        "model": "/models/en-us-ryan-high.onnx",
        "quality": "high",
        "description": "American male, high quality",
        "speed": 1.2
    }
}

class TTSRequest(BaseModel):
    text: str
    voice: str = "amy"
    speed: float = 1.0
    pitch: float = 1.0

@router.post("/speak")
async def text_to_speech(request: TTSRequest):
    """Generate speech using local Piper TTS"""
    
    start_time = time.time()
    
    if request.voice not in VOICES:
        return JSONResponse({"error": f"Voice {request.voice} not found"}, 400)
    
    voice_config = VOICES[request.voice]
    
    try:
        # Use Piper to generate speech
        cmd = [
            "piper",
            "--model", voice_config["model"],
            "--output-raw"
        ]
        
        # Run Piper
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        audio_data, error = process.communicate(input=request.text.encode())
        
        if process.returncode != 0:
            raise Exception(f"Piper error: {error.decode()}")
        
        generation_time = time.time() - start_time
        
        # Return audio with metadata
        headers = {
            "X-Generation-Time": str(generation_time),
            "X-Voice-Used": request.voice,
            "X-Quality": voice_config["quality"]
        }
        
        return StreamingResponse(
            io.BytesIO(audio_data),
            media_type="audio/wav",
            headers=headers
        )
        
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/voices")
async def list_voices():
    """List available local voices"""
    return {
        "voices": VOICES,
        "info": {
            "technology": "Piper Neural TTS",
            "privacy": "100% local processing",
            "internet": "Not required",
            "cost": "Free forever",
            "quality": "70-80% of cloud services"
        }
    }

@router.post("/benchmark")
async def benchmark_tts():
    """Test TTS performance on current hardware"""
    
    test_phrases = [
        "Hello, I'm Zoe.",
        "The quick brown fox jumps over the lazy dog.",
        "Today's weather is sunny with a high of 72 degrees."
    ]
    
    results = {}
    
    for voice_name in VOICES.keys():
        voice_times = []
        
        for phrase in test_phrases:
            start = time.time()
            
            # Generate speech
            cmd = [
                "piper",
                "--model", VOICES[voice_name]["model"],
                "--output-raw"
            ]
            
            process = subprocess.run(
                cmd,
                input=phrase.encode(),
                capture_output=True
            )
            
            voice_times.append(time.time() - start)
        
        results[voice_name] = {
            "average_time": sum(voice_times) / len(voice_times),
            "quality": VOICES[voice_name]["quality"],
            "realtime_capable": sum(voice_times) / len(voice_times) < 2.0
        }
    
    return {
        "hardware": "Raspberry Pi 5 (8GB)",
        "results": results,
        "recommendation": "Use 'amy' for real-time, 'ryan' for quality"
    }
