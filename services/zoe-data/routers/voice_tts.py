import asyncio
import base64
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])


async def _validate_device_token(x_device_token: str = Header(default="")) -> Optional[dict]:
    """Validate raw X-Device-Token against panel_auth SHA-256 cache."""
    raw = (x_device_token or "").strip()
    if not raw:
        return None
    from routers.panel_auth import lookup_device_token

    info = lookup_device_token(raw)
    if not info:
        return None
    return {
        "panel_id": info.get("panel_id"),
        "user_id": "voice-daemon",
        "role": info.get("role") or "voice-daemon",
        "raw_token": raw,  # Preserved so voice_command can forward it to the chat endpoint
    }


async def _require_voice_auth(
    request: Request,
    device: Optional[dict] = Depends(_validate_device_token),
    user: dict = Depends(get_current_user),
) -> dict:
    """Allow voice endpoints if caller has a valid device token OR is authenticated."""
    if device:
        return {
            "source": "device",
            "panel_id": device.get("panel_id"),
            "user_id": device.get("user_id", "voice-daemon"),
            "raw_token": device.get("raw_token", ""),
        }
    if user.get("role") not in (None, "guest"):
        return {"source": "session", "user_id": user.get("user_id"), "role": user.get("role")}
    raise HTTPException(status_code=401, detail="Voice endpoints require authentication or a device token")

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


def _whisper_cpp_binary() -> Optional[str]:
    explicit = (os.environ.get("ZOE_WHISPER_CPP_BIN") or "").strip()
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    for name in ("whisper-cli", "whisper.cpp", "main"):
        p = shutil.which(name)
        if p:
            return p
    return None


async def _run_whisper_cpp(wav_path: str) -> str:
    """Run whisper.cpp CLI; return transcript text (may be empty)."""
    bin_path = _whisper_cpp_binary()
    model = (os.environ.get("ZOE_WHISPER_MODEL") or "").strip()
    if not bin_path:
        raise RuntimeError(
            "whisper.cpp binary not found; set ZOE_WHISPER_CPP_BIN or install whisper-cli on PATH"
        )
    if not model or not os.path.isfile(model):
        raise RuntimeError(
            "whisper model not found; set ZOE_WHISPER_MODEL to a .bin file (e.g. ggml-base.en.bin)"
        )
    lang = (os.environ.get("ZOE_WHISPER_LANG") or "en").strip()
    extra = os.environ.get("ZOE_WHISPER_EXTRA_ARGS", "-nt").strip()
    extra_parts = extra.split() if extra else []
    cmd = [bin_path, "-m", model, "-f", wav_path, "-l", lang] + extra_parts
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=float(os.environ.get("ZOE_WHISPER_TIMEOUT_S", "120")))
    if proc.returncode != 0:
        msg = err.decode(errors="ignore").strip() or out.decode(errors="ignore").strip()
        raise RuntimeError(f"whisper.cpp failed (exit {proc.returncode}): {msg[:500]}")
    text = out.decode(errors="ignore").strip()
    return text


@router.post("/transcribe")
async def voice_transcribe(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """
    Transcribe base64 WAV/PCM audio using whisper.cpp on the Jetson (Pi voice daemon).
    Request: { \"audio_base64\": \"...\", \"panel_id\": \"...\" }
    """
    b64 = str((payload or {}).get("audio_base64") or "").strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

    suffix = ".wav"
    if len(raw) >= 4 and raw[:4] == b"RIFF":
        suffix = ".wav"
    elif len(raw) >= 2 and raw[:2] == b"\xff\xfb":
        suffix = ".mp3"

    try:
        text = ""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            wav_path = tmp.name
        try:
            text = await _run_whisper_cpp(wav_path)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
        logger.info("voice/transcribe panel=%s chars=%d", panel_id, len(text))
        # Broadcast the transcribed user text so the UI shows what was heard.
        stripped = text.strip()
        if stripped:
            try:
                from push import broadcaster
                await broadcaster.broadcast("all", "voice:transcript", {
                    "panel_id": panel_id,
                    "text": stripped,
                })
            except Exception:
                pass
        # Broadcast thinking state — STT done, LLM processing next.
        try:
            from push import broadcaster
            await broadcaster.broadcast("all", "voice:thinking", {"panel_id": panel_id})
        except Exception:
            pass
        return {"ok": True, "panel_id": panel_id, "text": stripped}
    except asyncio.TimeoutError:
        logger.warning("voice/transcribe timeout panel=%s", panel_id)
        raise HTTPException(status_code=504, detail="Transcription timed out") from None
    except RuntimeError as exc:
        logger.warning("voice/transcribe unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("voice/transcribe error: %s", exc)
        raise HTTPException(status_code=500, detail="Transcription failed") from exc


@router.post("/command")
async def voice_command(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """
    Receive a transcribed voice command from the Pi daemon (after wake word + STT).

    The Pi daemon POSTs: { "text": "...", "panel_id": "...", "session_id": "..." }
    Zoe routes the text through the chat pipeline and returns a JSON response
    with the reply text and synthesised audio (base64) so the Pi can play it back.
    """
    text = str((payload or {}).get("text", "")).strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    session_id = str((payload or {}).get("session_id", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Auto-generate an isolated session_id for voice so it never collides with
    # simultaneous web UI sessions sharing the same user account.
    if not session_id:
        import uuid as _uuid
        session_id = f"voice-panel-{panel_id}-{_uuid.uuid4().hex[:8]}"

    logger.info("voice/command panel=%s session=%s len=%d", panel_id, session_id, len(text))

    # Show the user's words in the voice overlay before processing.
    try:
        from push import broadcaster as _bc
        await _bc.broadcast("all", "voice:transcript", {"panel_id": panel_id, "text": text})
    except Exception:
        pass

    # Broadcast thinking state so orb shows processing animation.
    try:
        from push import broadcaster as _bc
        await _bc.broadcast("all", "voice:thinking", {"panel_id": panel_id})
    except Exception:
        pass

    # Forward to chat pipeline (non-streaming, synchronous for voice path).
    # Use the device token for auth — do NOT send X-Session-ID because:
    # - The auto-generated session_id doesn't exist in zoe-auth, which would cause 401
    # - The device token lets get_current_user resolve the panel user directly
    device_token = caller.get("raw_token") or ""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            chat_url = os.environ.get("ZOE_CHAT_URL", "http://localhost:8000")
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if device_token:
                headers["X-Device-Token"] = device_token
            r = await client.post(
                f"{chat_url}/api/chat/?stream=false",
                json={"message": text, "session_id": session_id},
                headers=headers,
            )
            r.raise_for_status()
            reply_json = r.json()
    except Exception as exc:
        logger.error("voice/command chat error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Chat error: {exc}") from exc

    reply_text = reply_json.get("response") or reply_json.get("reply") or reply_json.get("message") or ""
    audio_b64: Optional[str] = None
    content_type = "audio/wav"

    # Broadcast responding state so orb animates while TTS plays.
    if reply_text:
        try:
            from push import broadcaster as _bc2
            await _bc2.broadcast("all", "voice:responding", {
                "panel_id": panel_id,
                "text": reply_text[:200],
            })
        except Exception:
            pass

    if reply_text:
        try:
            audio_resp = await synthesize({"text": reply_text})
            audio_b64 = base64.b64encode(audio_resp.body).decode("ascii")
            content_type = audio_resp.media_type
        except Exception as exc:
            logger.warning("voice/command TTS failed: %s", exc)

    # Broadcast done so orb returns to idle.
    try:
        from push import broadcaster as _bc3
        await _bc3.broadcast("all", "voice:done", {"panel_id": panel_id})
    except Exception:
        pass

    return {
        "ok": True,
        "panel_id": panel_id,
        "reply": reply_text,
        "audio_base64": audio_b64,
        "content_type": content_type,
    }


@router.post("/wake")
async def voice_wake(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """
    Signal from the Pi daemon that the wake word was detected.
    Used to update panel state (show orb animation, open mic indicator).
    Returns TTS audio for the wake acknowledgement chime/phrase if configured.
    """
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    ack_phrase = os.environ.get("ZOE_WAKE_ACK_PHRASE", "").strip()

    logger.info("voice/wake panel=%s", panel_id)

    # Broadcast voice state so the touch UI orb shows the listening state.
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "voice:listening_started", {
            "panel_id": panel_id,
            "source": "wake_word",
        })
    except Exception as exc:
        logger.warning("voice/wake broadcast failed: %s", exc)

    audio_b64: Optional[str] = None
    content_type = "audio/wav"
    if ack_phrase:
        try:
            audio_resp = await synthesize({"text": ack_phrase})
            audio_b64 = base64.b64encode(audio_resp.body).decode("ascii")
            content_type = audio_resp.media_type
        except Exception as exc:
            logger.warning("voice/wake TTS ack failed: %s", exc)

    return {
        "ok": True,
        "panel_id": panel_id,
        "state": "listening",
        "ack_audio_base64": audio_b64,
        "content_type": content_type,
    }
