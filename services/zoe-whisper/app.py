import whisper
import tempfile
import os
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse

app = FastAPI(title="Zoe Whisper STT")

# Load tiny model for Raspberry Pi
print("Loading Whisper model (this may take a moment)...")
model = whisper.load_model("tiny")
print("Model loaded!")

@app.get("/health")
async def health():
    return {"status": "healthy", "model": "whisper-tiny", "note": "optimized for Pi"}

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribe uploaded audio file"""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        result = model.transcribe(tmp_path)
        os.unlink(tmp_path)
        
        return {
            "text": result["text"],
            "language": result.get("language", "en")
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
