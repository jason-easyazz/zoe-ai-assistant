import whisper
import tempfile
import os
import subprocess
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Zoe Whisper STT")

# Try to load base model for better accuracy (fallback to tiny if memory issues)
print("Loading Whisper model...")
try:
    model = whisper.load_model("base")
    model_name = "base"
    print("Base model loaded (better accuracy)")
except:
    try:
        model = whisper.load_model("tiny.en")  # English-only tiny model
        model_name = "tiny.en"
        print("Tiny English model loaded")
    except:
        model = whisper.load_model("tiny")
        model_name = "tiny"
        print("Tiny model loaded")

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "model": f"whisper-{model_name}",
        "note": "Force English for better accuracy"
    }

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribe uploaded audio file"""
    if not model:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    temp_input = None
    temp_converted = None
    
    try:
        # Save uploaded file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            content = await file.read()
            tmp.write(content)
            temp_input = tmp.name
        
        # Convert to 16kHz mono for best results
        temp_converted = tempfile.mktemp(suffix=".wav")
        convert_cmd = [
            "ffmpeg", "-i", temp_input,
            "-ar", "16000",      # 16kHz sample rate
            "-ac", "1",          # Mono
            "-af", "volume=2",   # Boost volume
            "-y",                # Overwrite
            temp_converted
        ]
        
        subprocess.run(convert_cmd, capture_output=True)
        
        # Transcribe with English forced
        result = model.transcribe(
            temp_converted if os.path.exists(temp_converted) else temp_input,
            language="en",  # Force English
            fp16=False      # Don't use FP16 on Pi
        )
        
        return {
            "text": result["text"].strip(),
            "language": "en",
            "model": model_name
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Transcription failed: {str(e)}"}
        )
    finally:
        # Clean up
        for f in [temp_input, temp_converted]:
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                except:
                    pass
