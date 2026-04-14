import asyncio
import base64
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# ── Voice session continuity ───────────────────────────────────────────────
# Persist one session_id per panel so follow-up voice commands have context.
# Dict: panel_id → {"session_id": str, "last_at": float}
_VOICE_SESSIONS: dict[str, dict] = {}
_VOICE_SESSION_TTL_S = 5 * 60  # Reset after 5 min silence


def _get_or_create_voice_session(panel_id: str) -> str:
    """Return the existing session_id for this panel, or create a new one."""
    import uuid as _uuid
    now = time.monotonic()
    entry = _VOICE_SESSIONS.get(panel_id)
    if entry and (now - entry["last_at"]) < _VOICE_SESSION_TTL_S:
        entry["last_at"] = now
        return entry["session_id"]
    session_id = f"voice-panel-{panel_id}-{_uuid.uuid4().hex[:8]}"
    _VOICE_SESSIONS[panel_id] = {"session_id": session_id, "last_at": now}
    return session_id


# ── Voice text pre-processor ───────────────────────────────────────────────
# Cleans LLM output for TTS synthesis: strips markdown, converts units,
# expands abbreviations.  Run before ANY synthesis call on voice paths.

_ABBREV = {
    r"\bDr\.": "Doctor",
    r"\bMr\.": "Mister",
    r"\bMrs\.": "Missus",
    r"\bMs\.": "Miss",
    r"\bSt\.": "Saint",
    r"\be\.g\.": "for example",
    r"\bi\.e\.": "that is",
    r"\betc\.": "et cetera",
    r"\bvs\.": "versus",
    r"\bApprox\.": "approximately",
}

_UNIT_RE = [
    (re.compile(r"(\d+)\s*°C\b"), r"\1 degrees Celsius"),
    (re.compile(r"(\d+)\s*°F\b"), r"\1 degrees Fahrenheit"),
    (re.compile(r"\$(\d+(?:\.\d{1,2})?)"), r"\1 dollars"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*%"), r"\1 percent"),
    (re.compile(r"(\d{1,2})\s*(am|pm)\b", re.IGNORECASE), r"\1 \2"),
    (re.compile(r"(\d+)\s*km/h\b"), r"\1 kilometres per hour"),
    (re.compile(r"(\d+)\s*mph\b"), r"\1 miles per hour"),
    (re.compile(r"(\d+)\s*kg\b"), r"\1 kilograms"),
    (re.compile(r"(\d+)\s*lbs?\b"), r"\1 pounds"),
]


def _voice_preprocess(text: str) -> str:
    """Strip markdown and normalise text so TTS sounds natural when spoken aloud."""
    if not text:
        return text

    # Remove markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Remove bullet/numbered list markers (keep the content)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove blockquotes
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    # Remove inline code and code blocks (replace with just the content)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # Remove markdown links but keep link text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # Expand abbreviations
    for pattern, replacement in _ABBREV.items():
        text = re.sub(pattern, replacement, text)

    # Convert units and symbols
    for pattern, replacement in _UNIT_RE:
        text = pattern.sub(replacement, text)

    # Collapse multiple blank lines to single break
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text.strip()


# ── Kokoro ONNX model singleton ────────────────────────────────────────────
# Loaded once at module level to avoid ~500ms per-call initialisation.
_kokoro_instance = None
_kokoro_lock = asyncio.Lock()
_kokoro_model_path_loaded: str = ""


async def _get_kokoro_instance():
    """Return cached Kokoro instance, loading lazily on first call."""
    global _kokoro_instance, _kokoro_model_path_loaded
    model_path = os.environ.get("ZOE_KOKORO_MODEL", "").strip()
    if not model_path or not os.path.isfile(model_path):
        return None
    async with _kokoro_lock:
        if _kokoro_instance is not None and _kokoro_model_path_loaded == model_path:
            return _kokoro_instance
        try:
            from kokoro_onnx import Kokoro  # type: ignore
            voices_path = os.environ.get("ZOE_KOKORO_VOICES", "").strip() or None
            _kokoro_instance = Kokoro(model_path, voices_path=voices_path)
            _kokoro_model_path_loaded = model_path
            logger.info("Kokoro ONNX model loaded from %s", model_path)
            return _kokoro_instance
        except ImportError:
            logger.debug("kokoro-onnx not installed; Kokoro TTS unavailable")
            return None
        except Exception as exc:
            logger.warning("Kokoro ONNX model load failed: %s", exc)
            return None


_VOICE_SYSTEM_PROMPT_SUFFIX = (
    "\n\n[VOICE MODE] Respond in 1-2 natural spoken sentences only. "
    "No markdown, no bullet points, no lists, no headers, no code blocks. "
    "Numbers and measurements in spoken form (e.g. '24 degrees' not '24°C'). "
    "Conversational tone. If asked for a list of more than 3 items, give the "
    "first 3 and offer to continue."
)


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
        edge_timeout = float(os.environ.get("ZOE_EDGE_TTS_TIMEOUT_S", "5"))
        await asyncio.wait_for(communicate.save(str(mp3_path)), timeout=edge_timeout)

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


async def _synthesize_kokoro(text: str, voice: str = "af_sky") -> Optional[bytes]:
    """Synthesize using Kokoro ONNX (thewh1teagle/kokoro-onnx).

    ~82M param ONNX model — sub-100ms first chunk on Jetson CUDA, natural AU voice.
    Install: pip install kokoro-onnx
    Model: download from https://github.com/thewh1teagle/kokoro-onnx/releases
    Set ZOE_KOKORO_MODEL to the path of the kokoro-v1.0.onnx file.
    Set ZOE_KOKORO_VOICE to override the default voice (af_sky = AU female).
    Uses module-level cached instance to avoid ~500ms model load per call.
    """
    kokoro = await _get_kokoro_instance()
    if kokoro is None:
        return None
    voice = os.environ.get("ZOE_KOKORO_VOICE", voice).strip() or voice
    try:
        import numpy as np
        import wave
        import io

        def _kokoro_sync():
            samples, sample_rate = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")
            samples_int16 = (samples * 32767).astype(np.int16)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(samples_int16.tobytes())
            return buf.getvalue()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _kokoro_sync)
    except Exception as exc:
        logger.warning("Kokoro TTS failed: %s", exc)
        return None


def _split_sentences(text: str) -> list[str]:
    """Split text into spoken sentences for streaming TTS.

    Uses a regex that avoids splitting on common abbreviations like Dr., Mr., e.g.
    """
    # Protect common abbreviations from being treated as sentence ends.
    _ABBREVS_PROTECT = r"(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bSt)(?<!\be\.g)(?<!\bi\.e)(?<!\betc)"
    pattern = re.compile(_ABBREVS_PROTECT + r"(?<=[.!?])\s+(?=[A-Z])")
    raw = pattern.split(text.strip())
    # Filter empties, strip whitespace
    sentences = [s.strip() for s in raw if s.strip()]
    return sentences or [text.strip()]


@router.post("/synthesize")
async def synthesize(payload: dict, caller: dict = Depends(_require_voice_auth)):
    text = (payload or {}).get("text", "")
    if not text or not str(text).strip():
        raise HTTPException(status_code=400, detail="text is required")

    # Pre-process: strip markdown and normalise units so TTS sounds natural.
    text = _voice_preprocess(str(text).strip())[:1200]
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

    # ── TTS waterfall: local sidecar → Kokoro ONNX → Edge TTS → espeak-ng ──
    if mode in {"hybrid", "local"}:
        audio_bytes = await _synthesize_local_service(text, profile=profile, base_url=local_tts_url)
        if audio_bytes:
            provider = "local-tts"
            if not audio_bytes.startswith(b"RIFF"):
                content_type = "audio/mpeg"

    # Kokoro ONNX — best offline naturalness, sub-100ms on Jetson CUDA.
    if audio_bytes is None and mode != "cloud":
        audio_bytes = await _synthesize_kokoro(text)
        if audio_bytes:
            provider = "kokoro-onnx"
            content_type = "audio/wav"

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
async def speak(payload: dict, caller: dict = Depends(_require_voice_auth)):
    response = await synthesize(payload, caller=caller)
    b64 = base64.b64encode(response.body).decode("ascii")
    return {
        "ok": True,
        "provider": response.headers.get("X-Zoe-TTS-Provider", "unknown"),
        "content_type": response.media_type,
        "audio_base64": b64,
    }


_STREAM_TEXT_MAX = 2000  # character cap for streaming TTS to prevent runaway requests


@router.post("/stream")
async def voice_stream(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Streaming TTS endpoint: sentence-splits text and streams WAV chunks.

    Pi daemon plays each chunk as it arrives, so first audio starts within ~1.2s.
    Request: { "text": "...", "profile": "zoe_au_natural_v1" }
    Response: application/x-zoe-audio-stream — each chunk is a JSON header line
              followed by a base64-encoded WAV audio block.  Failed chunks yield
              an error JSON line (no audio block) so the client can skip silently.
    """
    from fastapi.responses import StreamingResponse as _StreamingResponse
    import json as _json

    raw_text = str((payload or {}).get("text", "")).strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="text is required")

    text = _voice_preprocess(raw_text)[:_STREAM_TEXT_MAX]

    mode = os.getenv("ZOE_TTS_MODE", "hybrid").lower()
    profile = str((payload or {}).get("profile") or os.getenv("ZOE_VOICE_PROFILE", "zoe_au_natural_v1"))
    prof = VOICE_PROFILES.get(profile, VOICE_PROFILES["zoe_au_natural_v1"])
    edge_voice = str((payload or {}).get("edge_voice") or os.getenv("ZOE_EDGE_TTS_VOICE", prof["edge_voice"]))
    local_tts_url = os.getenv("ZOE_LOCAL_TTS_URL", "")

    sentences = _split_sentences(text)

    async def _generate_chunks():
        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence:
                continue
            audio_bytes: Optional[bytes] = None
            provider = "none"
            error_msg: Optional[str] = None

            # Waterfall: local sidecar → Kokoro ONNX → Edge TTS → espeak
            if mode in {"hybrid", "local"} and local_tts_url:
                audio_bytes = await _synthesize_local_service(sentence, profile=profile, base_url=local_tts_url)
                if audio_bytes:
                    provider = "local-tts"

            if audio_bytes is None and mode != "cloud":
                audio_bytes = await _synthesize_kokoro(sentence)
                if audio_bytes:
                    provider = "kokoro-onnx"

            if audio_bytes is None and mode in {"hybrid", "cloud", "edge"}:
                try:
                    audio_bytes = await _synthesize_edge_tts(sentence, edge_voice)
                    if audio_bytes:
                        provider = "edge-tts"
                except Exception as exc:
                    error_msg = str(exc)
                    audio_bytes = None

            if audio_bytes is None and mode != "cloud":
                try:
                    audio_bytes = await _synthesize_espeak(sentence, prof["espeak_speed"], prof["espeak_pitch"], prof["espeak_volume"])
                    if audio_bytes:
                        provider = "espeak-ng"
                except Exception as exc:
                    error_msg = str(exc)
                    audio_bytes = None

            if audio_bytes:
                header = _json.dumps({
                    "chunk": i,
                    "total": len(sentences),
                    "text": sentence[:80],
                    "provider": provider,
                })
                yield (header + "\n").encode()
                yield base64.b64encode(audio_bytes) + b"\n"
            else:
                # Yield an error object so the client knows a chunk failed.
                err_line = _json.dumps({
                    "chunk": i,
                    "total": len(sentences),
                    "error": error_msg or "all providers failed",
                    "text": sentence[:80],
                })
                yield (err_line + "\n").encode()

    return _StreamingResponse(
        _generate_chunks(),
        media_type="application/x-zoe-audio-stream",
        headers={"X-Chunk-Count": str(len(sentences)), "Cache-Control": "no-cache"},
    )


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


# Cached faster-whisper model instance to avoid ~1s reload per call
_faster_whisper_model = None
_faster_whisper_model_name: str = ""
_faster_whisper_lock = asyncio.Lock()


async def _get_faster_whisper_model():
    """Lazy-load and cache a faster-whisper WhisperModel instance."""
    global _faster_whisper_model, _faster_whisper_model_name
    model_name = (os.environ.get("ZOE_WHISPER_MODEL") or "base.en").strip()
    async with _faster_whisper_lock:
        if _faster_whisper_model is not None and _faster_whisper_model_name == model_name:
            return _faster_whisper_model
        try:
            from faster_whisper import WhisperModel  # type: ignore
            device = "cuda" if os.environ.get("ZOE_WHISPER_DEVICE", "").lower() == "cuda" else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            logger.info("Loading faster-whisper model=%s device=%s", model_name, device)
            _faster_whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
            _faster_whisper_model_name = model_name
            logger.info("faster-whisper model loaded: %s", model_name)
            return _faster_whisper_model
        except ImportError:
            logger.debug("faster-whisper not installed")
            return None
        except Exception as exc:
            logger.warning("faster-whisper model load failed: %s", exc)
            return None


async def _run_faster_whisper(wav_path: str) -> str:
    """Transcribe using faster-whisper Python library (fallback when whisper.cpp unavailable)."""
    model = await _get_faster_whisper_model()
    if model is None:
        raise RuntimeError("faster-whisper not available; install with: pip install faster-whisper")
    lang = (os.environ.get("ZOE_WHISPER_LANG") or "en").strip()
    vad_threshold = float(os.environ.get("ZOE_WHISPER_VAD_THRESHOLD", "0.50"))
    min_speech_ms = int(os.environ.get("ZOE_WHISPER_MIN_SPEECH_MS", "120"))
    min_silence_ms = int(os.environ.get("ZOE_WHISPER_MIN_SILENCE_MS", "350"))
    speech_pad_ms = int(os.environ.get("ZOE_WHISPER_SPEECH_PAD_MS", "220"))
    timeout = float(os.environ.get("ZOE_WHISPER_TIMEOUT_S", "20"))

    def _transcribe_sync():
        segments, _info = model.transcribe(
            wav_path,
            language=lang,
            vad_filter=True,
            vad_parameters={
                "threshold": vad_threshold,
                "min_speech_duration_ms": min_speech_ms,
                "min_silence_duration_ms": min_silence_ms,
                "speech_pad_ms": speech_pad_ms,
            },
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    loop = asyncio.get_event_loop()
    text = await asyncio.wait_for(
        loop.run_in_executor(None, _transcribe_sync),
        timeout=timeout,
    )
    return text


async def _transcribe_audio(wav_path: str) -> str:
    """
    Transcription waterfall:
    1. whisper.cpp CLI (if binary + ggml model configured)
    2. faster-whisper Python (auto-downloaded model, GPU/CPU)
    """
    if _whisper_cpp_binary():
        model = (os.environ.get("ZOE_WHISPER_MODEL") or "").strip()
        if model and os.path.isfile(model):
            return await _run_whisper_cpp(wav_path)
    return await _run_faster_whisper(wav_path)


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
            text = await _transcribe_audio(wav_path)
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
    identified_user_id: Optional[str] = (payload or {}).get("identified_user_id") or None
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Session continuity: reuse the same session for follow-up commands within 5 min.
    # Caller can override with an explicit session_id (e.g. for HA bridge routing).
    explicit_session = str((payload or {}).get("session_id", "")).strip()
    session_id = explicit_session if explicit_session else _get_or_create_voice_session(panel_id)

    logger.info("voice/command panel=%s session=%s user=%s len=%d",
                panel_id, session_id, identified_user_id or "anon", len(text))

    # ── Voice confirmation: check if we're waiting for yes/no ─────────────
    pending = _PENDING_CONFIRMATIONS.get(panel_id)
    if pending:
        now = time.monotonic()
        if now > pending.get("expire_at", 0):
            del _PENDING_CONFIRMATIONS[panel_id]
            pending = None
        else:
            lc = text.lower().strip().rstrip(".!?")
            if any(kw in lc for kw in _CONFIRM_KEYWORDS):
                # User confirmed — execute the intent now.
                del _PENDING_CONFIRMATIONS[panel_id]
                from intent_router import execute_intent, Intent
                confirmed_intent = Intent(
                    name=pending["intent_name"],
                    slots=pending["slots"],
                    confidence=1.0,
                )
                try:
                    from auth import get_current_user as _gcu
                    _uid = identified_user_id or pending.get("user_id", "voice-daemon")
                    result = await execute_intent(confirmed_intent, _uid)
                    reply_text = result or "Done!"
                except Exception as exc:
                    logger.error("Confirmed intent execution failed: %s", exc)
                    reply_text = "Sorry, that failed. Please try again."
                try:
                    audio_resp = await synthesize({"text": reply_text}, caller=caller)
                    audio_b64_conf = base64.b64encode(audio_resp.body).decode("ascii")
                    ct_conf = audio_resp.media_type
                except Exception:
                    audio_b64_conf = None
                    ct_conf = "audio/wav"
                try:
                    from push import broadcaster as _bc_conf
                    await _bc_conf.broadcast("all", "voice:done", {"panel_id": panel_id})
                except Exception:
                    pass
                return {"ok": True, "panel_id": panel_id, "reply": reply_text,
                        "audio_base64": audio_b64_conf, "content_type": ct_conf}
            elif any(kw in lc for kw in _CANCEL_KEYWORDS):
                del _PENDING_CONFIRMATIONS[panel_id]
                reply_text = "Okay, cancelled."
                try:
                    audio_resp = await synthesize({"text": reply_text}, caller=caller)
                    audio_b64_can = base64.b64encode(audio_resp.body).decode("ascii")
                    ct_can = audio_resp.media_type
                except Exception:
                    audio_b64_can = None
                    ct_can = "audio/wav"
                try:
                    from push import broadcaster as _bc_can
                    await _bc_can.broadcast("all", "voice:done", {"panel_id": panel_id})
                except Exception:
                    pass
                return {"ok": True, "panel_id": panel_id, "reply": reply_text,
                        "audio_base64": audio_b64_can, "content_type": ct_can}
            # Not a clear yes/no — fall through to normal processing (treat as new command)

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

    # Fallback audio used when any part of the pipeline fails — panel never goes silent.
    _FALLBACK_PHRASE = "Sorry, something went wrong. Please try again."

    async def _make_fallback_audio() -> tuple[Optional[str], str]:
        try:
            fb_resp = await synthesize({"text": _FALLBACK_PHRASE}, caller=caller)
            return base64.b64encode(fb_resp.body).decode("ascii"), fb_resp.media_type
        except Exception:
            return None, "audio/wav"

    # ── Voice confirmation gate: intercept write intents before sending to LLM ──
    # Detect intent locally and if it's a write intent, speak back parsed data and
    # wait for 'yes/confirm' from the user before actually executing.
    try:
        from intent_router import detect_intent as _detect
        _quick_intent = _detect(text)
        if _quick_intent and _quick_intent.name in _CONFIRM_INTENTS:
            slots = _quick_intent.slots or {}
            # Build a human-readable confirmation phrase.
            _phrases = {
                "calendar_create": lambda s: f"Add a calendar event: {s.get('title', 'untitled')}"
                    + (f" on {s.get('date', '')}" if s.get("date") else "")
                    + (f" at {s.get('time', '')}" if s.get("time") else "")
                    + ". Shall I confirm?",
                "list_add": lambda s: f"Add '{s.get('item', 'item')}' to "
                    + f"{s.get('list_name', 'the list')}. Shall I confirm?",
                "reminder_create": lambda s: f"Set a reminder: {s.get('text', 'reminder')}. Shall I confirm?",
                "note_create": lambda s: "Create a new note. Shall I confirm?",
                "journal_create": lambda s: "Create a journal entry. Shall I confirm?",
                "transaction_create": lambda s: f"Record a transaction of {s.get('amount', '')}. Shall I confirm?",
                "people_create": lambda s: f"Add contact {s.get('name', '')}. Shall I confirm?",
            }
            _gen = _phrases.get(_quick_intent.name)
            confirm_phrase = _gen(slots) if _gen else f"Confirm this action: {_quick_intent.name}?"
            _PENDING_CONFIRMATIONS[panel_id] = {
                "intent_name": _quick_intent.name,
                "slots": slots,
                "expire_at": time.monotonic() + _CONFIRM_TIMEOUT_S,
                "session_id": session_id,
                "user_id": identified_user_id or "voice-daemon",
            }
            try:
                audio_resp = await synthesize({"text": confirm_phrase}, caller=caller)
                audio_b64_confirm = base64.b64encode(audio_resp.body).decode("ascii")
                ct_confirm = audio_resp.media_type
            except Exception:
                audio_b64_confirm = None
                ct_confirm = "audio/wav"
            try:
                from push import broadcaster as _bc_pend
                await _bc_pend.broadcast("all", "voice:responding", {
                    "panel_id": panel_id,
                    "text": confirm_phrase[:200],
                })
                await _bc_pend.broadcast("all", "voice:done", {"panel_id": panel_id})
            except Exception:
                pass
            return {"ok": True, "panel_id": panel_id, "reply": confirm_phrase,
                    "pending_confirmation": True,
                    "audio_base64": audio_b64_confirm, "content_type": ct_confirm}
    except Exception as exc:
        logger.debug("voice confirmation pre-check failed (non-fatal): %s", exc)

    # Forward to chat pipeline (non-streaming, synchronous for voice path).
    # Use the device token for auth — do NOT send X-Session-ID because:
    # - The auto-generated session_id doesn't exist in zoe-auth, which would cause 401
    # - The device token lets get_current_user resolve the panel user directly
    device_token = caller.get("raw_token") or ""
    reply_text = ""
    audio_b64: Optional[str] = None
    content_type = "audio/wav"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            chat_url = os.environ.get("ZOE_CHAT_URL", "http://localhost:8000")
            headers: dict[str, str] = {
                "Content-Type": "application/json",
                "X-Voice-Mode": "true",  # Signals chat router to use voice system prompt
            }
            if device_token:
                headers["X-Device-Token"] = device_token
            r = await client.post(
                f"{chat_url}/api/chat/?stream=false",
                json={
                    "message": text,
                    "session_id": session_id,
                    "panel_id": panel_id,
                    **({"identified_user_id": identified_user_id} if identified_user_id else {}),
                },
                headers=headers,
            )
            r.raise_for_status()
            reply_json = r.json()
            reply_text = reply_json.get("response") or reply_json.get("reply") or reply_json.get("message") or ""
    except Exception as exc:
        logger.error("voice/command chat error: %s", exc)
        # Speak an error fallback — never let the panel go silent after wake word.
        audio_b64, content_type = await _make_fallback_audio()
        try:
            from push import broadcaster as _bc_err
            await _bc_err.broadcast("all", "voice:done", {"panel_id": panel_id})
        except Exception:
            pass
        return {
            "ok": False,
            "panel_id": panel_id,
            "reply": _FALLBACK_PHRASE,
            "audio_base64": audio_b64,
            "content_type": content_type,
        }

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

    # Emit show_card so the dashboard card overlay displays the reply text.
    if reply_text:
        try:
            from push import broadcaster as _bc_card
            card_payload: dict = {
                "card_type": "answer",
                "card_data": {"text": reply_text[:300]},
                "panel_id": panel_id,
            }
            await _bc_card.broadcast("all", "ui_action", {
                "action": {
                    "id": f"voice_card_{panel_id}",
                    "action_type": "show_card",
                    "payload": card_payload,
                }
            })
        except Exception:
            pass

    if reply_text:
        try:
            audio_resp = await synthesize({"text": reply_text}, caller=caller)
            audio_b64 = base64.b64encode(audio_resp.body).decode("ascii")
            content_type = audio_resp.media_type
        except Exception as exc:
            logger.warning("voice/command TTS failed: %s", exc)
            # TTS failed but we have text — try fallback voice
            audio_b64, content_type = await _make_fallback_audio()

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


@router.post("/turn")
async def voice_turn(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Combined STT + LLM + TTS in a single HTTP call.

    Accepts raw audio (base64 WAV), transcribes it, sends the transcript through
    the chat pipeline, synthesizes the reply, and returns everything in one response.
    Eliminates the extra network round-trip of separate /transcribe + /command calls.

    Request: { "audio_base64": "...", "panel_id": "...", "identified_user_id": "..." }
    """
    b64 = str((payload or {}).get("audio_base64") or "").strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

    suffix = ".wav" if (len(raw) >= 4 and raw[:4] == b"RIFF") else ".raw"

    # ── Phase 1: STT ──
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            wav_path = tmp.name
        try:
            transcript = await _transcribe_audio(wav_path)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
    except Exception as exc:
        logger.error("voice/turn STT failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc

    transcript = transcript.strip()
    if not transcript:
        return {"ok": True, "panel_id": panel_id, "text": "", "reply": "", "audio_base64": None}

    logger.info("voice/turn panel=%s transcript=%r", panel_id, transcript[:80])

    # Broadcast transcript so UI shows what was heard.
    try:
        from push import broadcaster as _bc_t
        await _bc_t.broadcast("all", "voice:transcript", {"panel_id": panel_id, "text": transcript})
    except Exception:
        pass

    # ── Phase 2+3: Delegate to /command handler (LLM + TTS) ──
    command_payload = {
        "text": transcript,
        "panel_id": panel_id,
    }
    if (payload or {}).get("identified_user_id"):
        command_payload["identified_user_id"] = payload["identified_user_id"]

    result = await voice_command(command_payload, caller=caller)
    result["text"] = transcript
    return result


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
            audio_resp = await synthesize({"text": ack_phrase}, caller=caller)
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


@router.post("/ambient")
async def voice_ambient(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Receive an ambient audio segment from the Pi daemon, transcribe it, and store in DB.

    The Pi daemon's always-on VAD posts speech segments here.
    Raw audio is transcribed by Whisper, then stored in ambient_memory.
    Raw audio bytes are discarded after transcription — only text is kept.
    """
    from database import get_db

    b64 = str((payload or {}).get("audio_base64", "")).strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    room = str((payload or {}).get("room", "")).strip() or None
    duration_s = float((payload or {}).get("duration_seconds", 0.0))

    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    # Transcribe via whisper.cpp.
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

    import uuid as _uuid
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        wav_path = tmp.name
    transcript = ""
    try:
        transcript = await _run_whisper_cpp(wav_path)
    except Exception as exc:
        logger.debug("ambient transcription failed: %s", exc)
        return {"ok": False, "panel_id": panel_id, "transcript": ""}
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass

    transcript = transcript.strip()
    if not transcript:
        return {"ok": True, "panel_id": panel_id, "transcript": ""}

    # Store in ambient_memory table.
    try:
        import aiosqlite
        from database import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO ambient_memory (panel_id, room, transcript, duration_seconds, source)
                   VALUES (?, ?, ?, ?, 'ambient')""",
                (panel_id, room, transcript, duration_s),
            )
            await db.commit()
        logger.debug("ambient_memory: panel=%s chars=%d", panel_id, len(transcript))
    except Exception as exc:
        logger.warning("ambient_memory insert failed: %s", exc)

    return {"ok": True, "panel_id": panel_id, "transcript": transcript}


# ── Speaker identification ─────────────────────────────────────────────────

def _compute_resemblyzer_embedding(wav_path: str) -> Optional[bytes]:
    """Compute a 256-dim resemblyzer voice embedding from a WAV file.

    Returns raw float32 bytes or None if resemblyzer is not installed.
    """
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
        import numpy as np
        encoder = VoiceEncoder()
        wav = preprocess_wav(wav_path)
        embedding = encoder.embed_utterance(wav)  # shape: (256,)
        return embedding.astype(np.float32).tobytes()
    except ImportError:
        logger.debug("resemblyzer not installed; speaker ID unavailable")
        return None
    except Exception as exc:
        logger.warning("resemblyzer embedding failed: %s", exc)
        return None


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """Cosine similarity between two float32 byte blobs."""
    try:
        import numpy as np
        va = np.frombuffer(a, dtype=np.float32)
        vb = np.frombuffer(b, dtype=np.float32)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:
        return 0.0


@router.post("/enroll")
async def voice_enroll(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Enroll a speaker voice profile using a WAV audio sample.

    Request: { "audio_base64": "...", "user_id": "...", "display_name": "...", "panel_id": "..." }
    Computes a resemblyzer 256-dim embedding and stores it in speaker_profiles.
    """
    from database import get_db
    import uuid as _uuid
    import aiosqlite
    from database import DB_PATH

    b64 = str((payload or {}).get("audio_base64", "")).strip()
    user_id = str((payload or {}).get("user_id", caller.get("user_id", "unknown")))
    display_name = str((payload or {}).get("display_name", user_id)).strip() or user_id
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or ""))

    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        wav_path = tmp.name

    try:
        embedding_bytes = _compute_resemblyzer_embedding(wav_path)
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass

    if embedding_bytes is None:
        raise HTTPException(status_code=503, detail="resemblyzer not available; install resemblyzer")

    profile_id = str(_uuid.uuid4())
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Check if user already has a profile — update sample_count and average embedding.
            async with db.execute(
                "SELECT id, embedding_blob, sample_count FROM speaker_profiles WHERE user_id=?",
                (user_id,)
            ) as cur:
                existing = await cur.fetchone()
            if existing:
                import numpy as np
                old_id, old_blob, old_count = existing
                old_emb = np.frombuffer(old_blob, dtype=np.float32)
                new_emb = np.frombuffer(embedding_bytes, dtype=np.float32)
                # Weighted average of embeddings.
                n = old_count or 1
                averaged = (old_emb * n + new_emb) / (n + 1)
                averaged_norm = averaged / (np.linalg.norm(averaged) + 1e-9)
                await db.execute(
                    """UPDATE speaker_profiles SET embedding_blob=?, sample_count=sample_count+1,
                       display_name=? WHERE id=?""",
                    (averaged_norm.astype(np.float32).tobytes(), display_name, old_id),
                )
                profile_id = old_id
            else:
                await db.execute(
                    """INSERT INTO speaker_profiles (id, user_id, display_name, embedding_blob, panel_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (profile_id, user_id, display_name, embedding_bytes, panel_id or None),
                )
            await db.commit()
    except Exception as exc:
        logger.error("voice/enroll DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to store profile") from exc

    return {"ok": True, "profile_id": profile_id, "user_id": user_id, "display_name": display_name}


@router.post("/identify")
async def voice_identify(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Identify speaker from a WAV audio sample by comparing to enrolled profiles.

    Request: { "audio_base64": "...", "panel_id": "..." }
    Returns best-match profile with confidence score.
    """
    import aiosqlite
    from database import DB_PATH

    b64 = str((payload or {}).get("audio_base64", "")).strip()
    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        wav_path = tmp.name

    try:
        query_emb = _compute_resemblyzer_embedding(wav_path)
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass

    if query_emb is None:
        raise HTTPException(status_code=503, detail="resemblyzer not available")

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, user_id, display_name, embedding_blob FROM speaker_profiles"
            ) as cur:
                profiles = await cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="DB error") from exc

    if not profiles:
        return {"ok": True, "identified": False, "reason": "no_profiles"}

    best_id = None
    best_user_id = None
    best_name = None
    best_score = -1.0
    for row in profiles:
        pid, uid, dname, emb_blob = row
        score = _cosine_similarity(query_emb, emb_blob)
        if score > best_score:
            best_score = score
            best_id = pid
            best_user_id = uid
            best_name = dname

    # Resemblyzer cosine similarity > 0.82 is typically a match.
    threshold = float(os.environ.get("ZOE_SPEAKER_ID_THRESHOLD", "0.82"))
    if best_score >= threshold:
        return {
            "ok": True,
            "identified": True,
            "profile_id": best_id,
            "user_id": best_user_id,
            "display_name": best_name,
            "confidence": round(best_score, 4),
        }
    return {
        "ok": True,
        "identified": False,
        "best_confidence": round(best_score, 4),
        "threshold": threshold,
    }


# ── Voice confirmation state ───────────────────────────────────────────────
# Track pending confirmations per panel: panel_id → {intent_name, slots, expire_at, session_id}
_PENDING_CONFIRMATIONS: dict[str, dict] = {}
_CONFIRM_TIMEOUT_S = 30  # seconds to wait for "yes/confirm" before expiring

# Intents that require confirmation before execution (irreversible writes).
_CONFIRM_INTENTS = frozenset({
    "calendar_create", "list_add", "note_create", "journal_create",
    "reminder_create", "transaction_create", "people_create",
})

_CONFIRM_KEYWORDS = frozenset({
    "yes", "yeah", "yep", "confirm", "do it", "go ahead", "sure", "ok", "okay",
    "correct", "that's right", "sounds good", "proceed",
})
_CANCEL_KEYWORDS = frozenset({
    "no", "nope", "cancel", "stop", "don't", "abort", "never mind", "nevermind",
})
