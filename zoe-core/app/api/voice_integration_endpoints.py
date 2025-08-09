import os
from fastapi import APIRouter, UploadFile, File, Response
import httpx

WHISPER_HOST = os.getenv("WHISPER_HOST", "whisper:9000")
TTS_HOST = os.getenv("TTS_HOST", "coqui-tts:5002")

router = APIRouter(prefix="/api/voice", tags=["voice"])

@router.post("/stt")
async def speech_to_text(file: UploadFile = File(...)):
    url = f"http://{WHISPER_HOST}/asr"
    async with httpx.AsyncClient(timeout=None) as client:
        files = {"audio_file": (file.filename, await file.read(), file.content_type)}
        r = await client.post(url, files=files)
        return r.json()

@router.post("/tts")
async def text_to_speech(payload: dict):
    text = payload.get("text", "")
    url = f"http://{TTS_HOST}/api/tts"
    async with httpx.AsyncClient(timeout=None) as client:
        r = await client.post(url, json={"text": text})
        return Response(content=r.content, media_type="audio/wav")
