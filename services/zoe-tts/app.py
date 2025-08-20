from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import subprocess
import tempfile
import os

app = FastAPI(title="Zoe TTS Service")

class TTSRequest(BaseModel):
    text: str
    voice: str = "default"

@app.get("/health")
async def health():
    return {"status": "healthy", "engine": "espeak"}

@app.post("/synthesize")
async def synthesize_speech(request: TTSRequest):
    """Simple TTS using espeak"""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            output_path = tmp.name
        
        # Use espeak to generate speech
        subprocess.run([
            "espeak", 
            "-w", output_path,
            request.text
        ], check=True)
        
        return {"audio_file": output_path, "status": "generated"}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
