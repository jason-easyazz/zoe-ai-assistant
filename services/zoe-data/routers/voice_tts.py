import asyncio
import base64
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/api/voice", tags=["voice"])

VOICE_PROFILES = {
    "zoe_au_natural_v1": {
        "edge_voice": "en-AU-NatashaNeural",
        "espeak_speed": 158,
        "espeak_pitch": 44,
        "espeak_volume": 150,
    }
}


def _has_espeak_ng() -> bool:
    return shutil.which("espeak-ng") is not None


async def _synthesize_espeak(text: str, speed: int, pitch: int, volume: int) -> bytes:
    if not _has_espeak_ng():
        raise RuntimeError("espeak-ng is not installed")

    with tempfile.TemporaryDirectory(prefix="zoe-tts-") as td:
        mono_path = Path(td) / "mono.wav"
        stereo_path = Path(td) / "stereo.wav"

        proc = await asyncio.create_subprocess_exec(
            "espeak-ng",
            "-v",
            "en-au",
            "-s",
            str(speed),
            "-p",
            str(pitch),
            "-a",
            str(volume),
            "-w",
            str(mono_path),
            text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"espeak-ng failed: {err.decode(errors='ignore').strip()}")

        # Duplicate mono samples to stereo for better USB speaker compatibility.
        import wave

        with wave.open(str(mono_path), "rb") as src:
            fr = src.getframerate()
            sw = src.getsampwidth()
            n = src.getnframes()
            data = src.readframes(n)

        out = bytearray()
        step = sw
        for i in range(0, len(data), step):
            s = data[i : i + step]
            if len(s) < step:
                break
            out.extend(s)
            out.extend(s)

        with wave.open(str(stereo_path), "wb") as dst:
            dst.setnchannels(2)
            dst.setsampwidth(sw)
            dst.setframerate(fr)
            dst.writeframes(bytes(out))

        return stereo_path.read_bytes()


async def _synthesize_edge_tts(text: str, voice: str) -> Optional[bytes]:
    try:
        import edge_tts
    except Exception:
        return None

    with tempfile.TemporaryDirectory(prefix="zoe-edge-tts-") as td:
        mp3_path = Path(td) / "speech.mp3"
        wav_path = Path(td) / "speech.wav"
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(str(mp3_path))

        # Convert mp3 to wav if ffmpeg exists, else return mp3 bytes.
        if shutil.which("ffmpeg") is None:
            return mp3_path.read_bytes()

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(mp3_path),
            "-ac",
            "2",
            "-ar",
            "22050",
            str(wav_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0:
            return mp3_path.read_bytes()
        return wav_path.read_bytes()


async def _synthesize_local_service(text: str, profile: str, base_url: str) -> Optional[bytes]:
    if not base_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/synthesize",
                json={"text": text, "profile": profile},
            )
            if r.status_code >= 400 or not r.content:
                return None
            return r.content
    except Exception:
        return None


@router.post("/synthesize")
async def synthesize(payload: dict):
    text = (payload or {}).get("text", "")
    if not text or not str(text).strip():
        raise HTTPException(status_code=400, detail="text is required")

    text = str(text).strip()[:1200]
    mode = os.getenv("ZOE_TTS_MODE", "hybrid").lower()
    profile = str((payload or {}).get("profile") or os.getenv("ZOE_VOICE_PROFILE", "zoe_au_natural_v1"))
    prof = VOICE_PROFILES.get(profile, VOICE_PROFILES["zoe_au_natural_v1"])
    edge_voice = str((payload or {}).get("edge_voice") or os.getenv("ZOE_EDGE_TTS_VOICE", prof["edge_voice"]))
    speed = int(os.getenv("ZOE_ESPEAK_SPEED", str(prof["espeak_speed"])))
    pitch = int(os.getenv("ZOE_ESPEAK_PITCH", str(prof["espeak_pitch"])))
    volume = int(os.getenv("ZOE_ESPEAK_VOLUME", str(prof["espeak_volume"])))
    local_tts_url = os.getenv("ZOE_LOCAL_TTS_URL", "")

    audio_bytes = None
    content_type = "audio/wav"
    provider = "none"

    if mode in {"hybrid", "local"}:
        audio_bytes = await _synthesize_local_service(text, profile=profile, base_url=local_tts_url)
        if audio_bytes:
            provider = "local-tts"
            if not audio_bytes.startswith(b"RIFF"):
                content_type = "audio/mpeg"

    if audio_bytes is None and mode in {"hybrid", "cloud", "edge"}:
        try:
            audio_bytes = await _synthesize_edge_tts(text, edge_voice)
            if audio_bytes:
                provider = "edge-tts"
                if not audio_bytes.startswith(b"RIFF"):
                    content_type = "audio/mpeg"
        except Exception:
            audio_bytes = None

    if audio_bytes is None and mode != "cloud":
        try:
            audio_bytes = await _synthesize_espeak(text, speed=speed, pitch=pitch, volume=volume)
            provider = "espeak-ng"
            content_type = "audio/wav"
        except Exception:
            audio_bytes = None

    if audio_bytes is None:
        raise HTTPException(status_code=503, detail="No speech provider available")

    headers = {"X-Zoe-TTS-Provider": provider}
    return Response(content=audio_bytes, media_type=content_type, headers=headers)


@router.post("/speak")
async def speak(payload: dict):
    response = await synthesize(payload)
    b64 = base64.b64encode(response.body).decode("ascii")
    return {
        "ok": True,
        "provider": response.headers.get("X-Zoe-TTS-Provider", "unknown"),
        "content_type": response.media_type,
        "audio_base64": b64,
    }
