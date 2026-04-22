import asyncio
import base64
import json
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# ── Voice session continuity ───────────────────────────────────────────────
# Persist one session_id per panel so follow-up voice commands have context.
# Pass 3 B3: also stores bound_user_id (resolved via daemon VID or PIN) with
# the same 5-min TTL so mid-conversation turns don't re-challenge.
# Dict: panel_id → {"session_id": str, "last_at": float, "bound_user_id": str|None}
_VOICE_SESSIONS: dict[str, dict] = {}
_VOICE_SESSION_TTL_S = 5 * 60  # Reset after 5 min silence

# Pass 3 B4: stash of voice turns awaiting PIN confirmation.
# Dict: panel_id → {pending_id, transcript, session_id, expire_at}
_PENDING_VOICE_IDENT: dict[str, dict] = {}


def _get_or_create_voice_session(panel_id: str) -> str:
    """Return the existing session_id for this panel, or create a new one."""
    import uuid as _uuid
    now = time.monotonic()
    entry = _VOICE_SESSIONS.get(panel_id)
    if entry and (now - entry["last_at"]) < _VOICE_SESSION_TTL_S:
        entry["last_at"] = now
        return entry["session_id"]
    session_id = f"voice-panel-{panel_id}-{_uuid.uuid4().hex[:8]}"
    # Preserve bound identity across idle TTL rollovers so authenticated
    # panels do not get unnecessary repeat auth challenges.
    _next = {"session_id": session_id, "last_at": now}
    if entry and entry.get("bound_user_id"):
        _next["bound_user_id"] = entry["bound_user_id"]
    _VOICE_SESSIONS[panel_id] = _next
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


async def _synthesize_kokoro_sidecar(text: str) -> Optional[bytes]:
    """Synthesize via Kokoro PyTorch sidecar (GPU, natural af_sky voice).

    Calls the local FastAPI sidecar on port 10201 which keeps the Kokoro
    model warm in CUDA memory.  Sub-200ms warm latency on Jetson Orin.
    Set ZOE_KOKORO_SIDECAR_URL to override (default http://127.0.0.1:10201).
    Falls through silently if the sidecar is unavailable.
    """
    sidecar_url = os.environ.get("ZOE_KOKORO_SIDECAR_URL", "http://127.0.0.1:10201").rstrip("/")
    voice = os.environ.get("ZOE_KOKORO_VOICE", "af_sky").strip() or "af_sky"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{sidecar_url}/synthesize",
                json={"text": text, "voice": voice},
            )
            if r.status_code >= 400 or not r.content:
                logger.debug("kokoro-sidecar HTTP %s", r.status_code)
                return None
            return r.content
    except Exception as exc:
        logger.debug("kokoro-sidecar unavailable: %s", exc)
        return None


async def _synthesize_wyoming_piper(text: str) -> Optional[bytes]:
    """Synthesize via wyoming-piper TCP socket (rhasspy/wyoming-piper Docker container).

    p50 ~127ms first-audio-byte on Jetson — 8x faster than Kokoro ONNX on CPU.
    Set ZOE_WYOMING_PIPER_HOST (default 127.0.0.1) and ZOE_WYOMING_PIPER_PORT (default 10200).
    Wyoming protocol: newline-delimited JSON headers + optional binary payloads.
    """
    host = os.environ.get("ZOE_WYOMING_PIPER_HOST", "127.0.0.1").strip()
    port = int(os.environ.get("ZOE_WYOMING_PIPER_PORT", "10200"))
    voice_name = os.environ.get("ZOE_WYOMING_PIPER_VOICE", "en_US-lessac-medium").strip()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=2.0
        )
    except Exception as exc:
        logger.debug("wyoming-piper connect failed: %s", exc)
        return None

    try:
        event = {
            "type": "synthesize",
            "data": {"text": text, "voice": {"name": voice_name}},
            "data_length": 0,
        }
        writer.write((json.dumps(event) + "\n").encode())
        await writer.drain()

        audio_rate = 22050
        audio_width = 2
        audio_channels = 1
        pcm_chunks: list[bytes] = []
        buf = b""

        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=10.0)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            buf += chunk

            while b"\n" in buf:
                idx = buf.index(b"\n")
                header_bytes, buf = buf[:idx], buf[idx + 1:]
                try:
                    hdr = json.loads(header_bytes.decode(errors="replace"))
                except Exception:
                    continue

                ev_type = hdr.get("type", "")
                data_len = hdr.get("data_length", 0)
                payload_len = hdr.get("payload_length", 0)

                # Consume structured sub-data (audio format info)
                while len(buf) < data_len:
                    buf += await asyncio.wait_for(reader.read(65536), timeout=10.0)
                if data_len > 0:
                    try:
                        sub = json.loads(buf[:data_len].decode(errors="replace"))
                        if ev_type == "audio-start":
                            audio_rate = sub.get("rate", audio_rate)
                            audio_width = sub.get("width", audio_width)
                            audio_channels = sub.get("channels", audio_channels)
                    except Exception:
                        pass
                    buf = buf[data_len:]

                # Consume raw PCM payload
                while len(buf) < payload_len:
                    buf += await asyncio.wait_for(reader.read(65536), timeout=10.0)
                if payload_len > 0:
                    pcm_chunks.append(buf[:payload_len])
                    buf = buf[payload_len:]

                if ev_type == "audio-stop":
                    # Wrap collected PCM in a WAV container
                    import wave as _wave, io as _io

                    raw_pcm = b"".join(pcm_chunks)
                    wav_buf = _io.BytesIO()
                    with _wave.open(wav_buf, "wb") as wf:
                        wf.setnchannels(audio_channels)
                        wf.setsampwidth(audio_width)
                        wf.setframerate(audio_rate)
                        wf.writeframes(raw_pcm)
                    return wav_buf.getvalue()

        return None
    except Exception as exc:
        logger.warning("wyoming-piper synthesis failed: %s", exc)
        return None
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


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
            kokoro_speed = float(os.environ.get("ZOE_KOKORO_SPEED", "1.15"))
            samples, sample_rate = kokoro.create(text, voice=voice, speed=kokoro_speed, lang="en-us")
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


def _extract_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Extract finished sentences from a streaming token buffer."""
    if not buffer:
        return [], ""
    complete: list[str] = []
    last_end = 0
    for m in re.finditer(r"(.+?[.!?])(?:\s+|$)", buffer, flags=re.DOTALL):
        sentence = m.group(1).strip()
        if sentence:
            complete.append(sentence)
        last_end = m.end()
    return complete, buffer[last_end:]


async def _broadcast_weather_ui(
    panel_id: str,
    summary: str = "Fetching weather...",
    turn_key: Optional[str] = None,
) -> None:
    """Mirror chat weather navigation on voice path with durable delivery."""
    delivery_key = turn_key or str(time.monotonic_ns())
    nav_action = {
        "id": f"voice_weather_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            "url": "/touch/weather.html",
            "label": "Opening weather",
            "panel_id": panel_id,
        },
    }
    card_action = {
        "id": f"voice_weather_card_{panel_id}_{delivery_key}",
        "action_type": "show_card",
        "payload": {
            "type": "weather",
            "data": {"summary": summary[:200]},
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
        await broadcaster.broadcast("all", "ui_action", {"action": card_action})
    except Exception as exc:
        logger.debug("voice weather ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_weather_nav_{panel_id}_{delivery_key}",
            )
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="show_card",
                payload=card_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_weather_card_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice weather ui enqueue failed (non-fatal): %s", exc)


async def _broadcast_calendar_ui(
    panel_id: str,
    summary: str = "Opening your calendar...",
    turn_key: Optional[str] = None,
) -> None:
    """Mirror calendar intents on voice path with durable delivery."""
    delivery_key = turn_key or str(time.monotonic_ns())
    nav_action = {
        "id": f"voice_calendar_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            "url": "/touch/calendar.html",
            "label": "Opening calendar",
            "panel_id": panel_id,
        },
    }
    card_action = {
        "id": f"voice_calendar_card_{panel_id}_{delivery_key}",
        "action_type": "show_card",
        "payload": {
            "type": "calendar",
            "data": {"summary": summary[:200]},
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
        await broadcaster.broadcast("all", "ui_action", {"action": card_action})
    except Exception as exc:
        logger.debug("voice calendar ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_calendar_nav_{panel_id}_{delivery_key}",
            )
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="show_card",
                payload=card_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_calendar_card_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice calendar ui enqueue failed (non-fatal): %s", exc)


async def _broadcast_shopping_chat_ui(
    panel_id: str,
    list_type: str,
    item_text: str,
    summary: str = "Shopping item added.",
    turn_key: Optional[str] = None,
) -> None:
    """Navigate to touch chat and surface live shopping list context."""
    delivery_key = turn_key or str(time.monotonic_ns())
    safe_list_type = (list_type or "shopping").strip() or "shopping"
    safe_item = (item_text or "").strip()
    query = (
        f"list_focus=1&list_type={quote_plus(safe_list_type)}"
        f"&list_item={quote_plus(safe_item)}"
    )
    nav_action = {
        "id": f"voice_shop_chat_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            "url": f"/touch/chat.html?{query}",
            "label": "Opening shopping list in chat",
            "panel_id": panel_id,
        },
    }
    card_action = {
        "id": f"voice_shop_chat_card_{panel_id}_{delivery_key}",
        "action_type": "show_card",
        "payload": {
            "type": "list",
            "data": {
                "summary": summary[:200],
                "item": safe_item,
                "list_type": safe_list_type,
            },
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
        await broadcaster.broadcast("all", "ui_action", {"action": card_action})
    except Exception as exc:
        logger.debug("voice shopping ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_shop_chat_nav_{panel_id}_{delivery_key}",
            )
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="show_card",
                payload=card_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_shop_chat_card_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice shopping ui enqueue failed (non-fatal): %s", exc)


async def _broadcast_calendar_chat_prefill_ui(
    panel_id: str,
    slots: dict,
    turn_key: Optional[str] = None,
) -> None:
    """Navigate to touch chat with calendar form prefill for user confirmation."""
    delivery_key = turn_key or str(time.monotonic_ns())
    title = str((slots or {}).get("title", "")).strip()
    date = str((slots or {}).get("date", "")).strip()
    time_val = str((slots or {}).get("time", "")).strip()
    query = (
        f"calendar_focus=1&title={quote_plus(title)}"
        f"&date={quote_plus(date)}&time={quote_plus(time_val)}"
    )
    nav_action = {
        "id": f"voice_calendar_chat_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            "url": f"/touch/chat.html?{query}",
            "label": "Opening calendar form in chat",
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
    except Exception as exc:
        logger.debug("voice calendar chat prefill broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_calendar_chat_nav_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice calendar chat prefill enqueue failed (non-fatal): %s", exc)


async def _broadcast_list_ui(
    panel_id: str,
    item_text: str,
    list_type: str = "shopping",
    summary: str = "Item added.",
    turn_key: Optional[str] = None,
) -> None:
    delivery_key = turn_key or str(time.monotonic_ns())
    nav_action = {
        "id": f"voice_list_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            "url": "/touch/lists.html",
            "label": "Opening list",
            "panel_id": panel_id,
        },
    }
    card_action = {
        "id": f"voice_list_card_{panel_id}_{delivery_key}",
        "action_type": "show_card",
        "payload": {
            "type": "list",
            "data": {
                "summary": summary[:200],
                "item": item_text,
                "list_type": list_type,
            },
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
        await broadcaster.broadcast("all", "ui_action", {"action": card_action})
    except Exception as exc:
        logger.debug("voice list ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_list_nav_{panel_id}_{delivery_key}",
            )
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="show_card",
                payload=card_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_list_card_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice list ui enqueue failed (non-fatal): %s", exc)


async def _broadcast_reminder_ui(
    panel_id: str,
    summary: str,
    turn_key: Optional[str] = None,
) -> None:
    delivery_key = turn_key or str(time.monotonic_ns())
    nav_action = {
        "id": f"voice_reminder_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            "url": "/touch/dashboard.html",
            "label": "Opening reminders",
            "panel_id": panel_id,
        },
    }
    card_action = {
        "id": f"voice_reminder_card_{panel_id}_{delivery_key}",
        "action_type": "show_card",
        "payload": {
            "type": "reminder",
            "data": {"summary": summary[:200]},
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
        await broadcaster.broadcast("all", "ui_action", {"action": card_action})
    except Exception as exc:
        logger.debug("voice reminder ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_reminder_nav_{panel_id}_{delivery_key}",
            )
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="show_card",
                payload=card_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_reminder_card_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice reminder ui enqueue failed (non-fatal): %s", exc)


async def _request_auth_ui(panel_id: str, challenge_id: str, reason: str) -> bool:
    """Request panel auth UI via both websocket broadcast and durable queue."""
    if not challenge_id:
        return False
    auth_action = {
        "id": f"voice_auth_{panel_id}_{challenge_id}",
        "action_type": "panel_request_auth",
        "payload": {
            "panel_id": panel_id,
            "challenge_id": challenge_id,
            "action_context": reason,
        },
    }
    delivered = False
    try:
        from push import broadcaster as _bc_auth
        await _bc_auth.broadcast("all", "ui_action", {"action": auth_action})
        delivered = True
    except Exception as exc:
        logger.debug("voice auth ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_request_auth",
                payload=auth_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_auth_{panel_id}_{challenge_id}",
            )
            delivered = True
            break
    except Exception as exc:
        logger.warning("voice auth ui enqueue failed: %s", exc)
    return delivered


async def _resolve_panel_default_user(panel_id: str, db) -> Optional[str]:
    """Best-effort panel user resolution for voice fallback identity."""
    try:
        cur = await db.execute(
            "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
            (panel_id,),
        )
        row = await cur.fetchone()
        if row and row["user_id"]:
            return str(row["user_id"])
    except Exception:
        pass
    try:
        cur = await db.execute(
            "SELECT user_id FROM panel_user_bindings WHERE panel_id = ? AND binding_type = 'default' LIMIT 1",
            (panel_id,),
        )
        row = await cur.fetchone()
        if row and row["user_id"]:
            return str(row["user_id"])
    except Exception:
        pass
    return None


def _panel_session_trust_window_s() -> int:
    """Seconds that an active panel session is trusted for voice scope gating."""
    raw = str(os.environ.get("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "900")).strip()
    try:
        value = int(raw)
    except Exception:
        return 900
    return max(0, min(value, 24 * 60 * 60))


async def _resolve_recent_panel_session_user(panel_id: str, db) -> Optional[str]:
    """
    Resolve panel user only when the panel session heartbeat is fresh enough
    to be considered actively authenticated.
    """
    trust_window_s = _panel_session_trust_window_s()
    if trust_window_s <= 0:
        return None
    try:
        cur = await db.execute(
            "SELECT user_id, last_seen_at FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
            (panel_id,),
        )
        row = await cur.fetchone()
        if not row or not row["user_id"] or not row["last_seen_at"]:
            return None

        raw_last_seen = str(row["last_seen_at"]).strip()
        parsed: Optional[datetime] = None
        try:
            parsed = datetime.fromisoformat(raw_last_seen.replace("Z", "+00:00"))
        except Exception:
            try:
                parsed = datetime.strptime(raw_last_seen, "%Y-%m-%d %H:%M:%S")
            except Exception:
                logger.debug("voice scope trust: unsupported last_seen_at format: %s", raw_last_seen)
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        age_s = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        if age_s <= trust_window_s:
            return str(row["user_id"])
    except Exception:
        pass
    return None


def _contains_decision_keyword(text: str, keywords: frozenset[str]) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return False
    for kw in keywords:
        phrase = re.sub(r"\s+", r"\\s+", re.escape(kw.strip().lower()))
        if re.search(rf"(?<![a-z0-9]){phrase}(?![a-z0-9])", normalized):
            return True
    return False


def _should_handoff_calendar(user_text: str, reply_text: str, intent_name: Optional[str] = None) -> bool:
    if intent_name in {"calendar_show", "daily_briefing", "calendar_create"}:
        return True
    u = (user_text or "").lower()
    r = (reply_text or "").lower()
    user_hint = any(k in u for k in ("calendar", "schedule", "weekly", "week plan"))
    reply_hint = any(k in r for k in ("calendar", "schedule", "this week", "upcoming"))
    return user_hint and reply_hint


def _wav_bytes_from_float32_samples(samples, sample_rate: int) -> bytes:
    """Convert float32 [-1,1] samples to mono WAV bytes."""
    import io
    import wave
    import numpy as np

    clipped = np.clip(samples, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


async def _stream_kokoro_sentence_wavs(sentence: str, voice: str = "af_sky"):
    """Yield WAV chunks from Kokoro create_stream() for one sentence."""
    kokoro = await _get_kokoro_instance()
    if kokoro is None or not hasattr(kokoro, "create_stream"):
        return
    voice = os.environ.get("ZOE_KOKORO_VOICE", voice).strip() or voice
    kokoro_speed = float(os.environ.get("ZOE_KOKORO_SPEED", "1.15"))
    try:
        async for samples, sample_rate in kokoro.create_stream(
            sentence, voice=voice, speed=kokoro_speed, lang="en-us"
        ):
            if samples is None:
                continue
            try:
                yield _wav_bytes_from_float32_samples(samples, sample_rate)
            except Exception:
                continue
    except Exception as exc:
        logger.warning("Kokoro create_stream failed: %s", exc)
        return


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

    # ── TTS waterfall: kokoro-sidecar → local sidecar → wyoming-piper → Kokoro ONNX → Edge TTS → espeak-ng ──
    if mode in {"hybrid", "local"}:
        audio_bytes = await _synthesize_local_service(text, profile=profile, base_url=local_tts_url)
        if audio_bytes:
            provider = "local-tts"
            if not audio_bytes.startswith(b"RIFF"):
                content_type = "audio/mpeg"

    # Kokoro sidecar — GPU-accelerated natural af_sky voice (~150ms warm on Jetson).
    if audio_bytes is None and mode != "cloud":
        audio_bytes = await _synthesize_kokoro_sidecar(text)
        if audio_bytes:
            provider = "kokoro-sidecar"
            content_type = "audio/wav"

    # wyoming-piper — p50 127ms on Jetson, 8x faster than Kokoro ONNX CPU.
    if audio_bytes is None and mode != "cloud":
        wyoming_host = os.environ.get("ZOE_WYOMING_PIPER_HOST", "127.0.0.1").strip()
        wyoming_port = int(os.environ.get("ZOE_WYOMING_PIPER_PORT", "10200"))
        if wyoming_host and wyoming_port:
            audio_bytes = await _synthesize_wyoming_piper(text)
            if audio_bytes:
                provider = "wyoming-piper"
                content_type = "audio/wav"

    # Kokoro ONNX — offline fallback (CPU ~1.1s on Jetson, fast if CUDA available).
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

            # Waterfall: kokoro-sidecar → local sidecar → wyoming-piper → Kokoro ONNX → Edge TTS → espeak
            if mode in {"hybrid", "local"} and local_tts_url:
                audio_bytes = await _synthesize_local_service(sentence, profile=profile, base_url=local_tts_url)
                if audio_bytes:
                    provider = "local-tts"

            if audio_bytes is None and mode != "cloud":
                audio_bytes = await _synthesize_kokoro_sidecar(sentence)
                if audio_bytes:
                    provider = "kokoro-sidecar"

            if audio_bytes is None and mode != "cloud":
                audio_bytes = await _synthesize_wyoming_piper(sentence)
                if audio_bytes:
                    provider = "wyoming-piper"

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
            # int8_float16 = int8-quantized weights + float16 compute: best speed/memory for Jetson
            # Falls back to int8 if VRAM is very tight
            default_compute = "int8_float16" if device == "cuda" else "int8"
            compute_type = os.environ.get("ZOE_WHISPER_COMPUTE_TYPE", default_compute).strip() or default_compute
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
async def voice_command(
    payload: dict,
    caller: dict = Depends(_require_voice_auth),
    stream: bool = Query(False, description="When true, stream audio chunks as they are produced."),
    db=Depends(get_db),
):
    """
    Receive a transcribed voice command from the Pi daemon (after wake word + STT).

    The Pi daemon POSTs: { "text": "...", "panel_id": "...", "session_id": "..." }
    Zoe routes the text through the chat pipeline and returns a JSON response
    with the reply text and synthesised audio (base64) so the Pi can play it back.
    """
    text = str((payload or {}).get("text", "")).strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    identified_user_id: Optional[str] = (payload or {}).get("identified_user_id") or None
    # Forwarded by /voice/turn so end-to-end total can be recorded from the
    # true start of the request (audio upload). Falls back to command start.
    _t_turn_start = (payload or {}).get("_t_turn_start")
    _t_cmd_start = time.monotonic()
    _turn_key = str(time.monotonic_ns())
    _quick_intent_name: Optional[str] = None
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Classify identity source for Pass 3 observability (Pass 1 is measurement-only).
    try:
        from voice_metrics import voice_identity_source_count
        if identified_user_id:
            voice_identity_source_count.labels(source="daemon_vid").inc()
        elif caller.get("panel_id"):
            # device-token path resolves to family-admin today (Pass 0 finding).
            voice_identity_source_count.labels(source="fallback_family_admin").inc()
        else:
            voice_identity_source_count.labels(source="fallback_guest").inc()
    except Exception:
        pass

    # Session continuity: reuse the same session for follow-up commands within 5 min.
    # Caller can override with an explicit session_id (e.g. for HA bridge routing).
    explicit_session = str((payload or {}).get("session_id", "")).strip()
    session_id = explicit_session if explicit_session else _get_or_create_voice_session(panel_id)

    # Pass 3 B3: if daemon supplied an identified_user_id, bind it to the session
    # so the next turn from the same panel doesn't need to re-verify.
    if identified_user_id:
        _ses = _VOICE_SESSIONS.get(panel_id)
        if _ses:
            _ses["bound_user_id"] = identified_user_id

    # Resolve effective user once so all downstream branches (intent fast path,
    # scope checks, Pi agent, and OpenClaw fallback) share the same identity.
    _bound_user = (_VOICE_SESSIONS.get(panel_id) or {}).get("bound_user_id")
    _panel_recent_user = await _resolve_recent_panel_session_user(panel_id, db)
    if not _bound_user and _panel_recent_user:
        _ses = _VOICE_SESSIONS.get(panel_id)
        if _ses is not None:
            _ses["bound_user_id"] = _panel_recent_user
        _bound_user = _panel_recent_user
    _panel_default_user = await _resolve_panel_default_user(panel_id, db)
    _scope_identity_user = identified_user_id or _bound_user or _panel_recent_user
    _has_scope_identity = bool(_scope_identity_user)
    effective_user = (
        identified_user_id
        or _bound_user
        or _panel_recent_user
        or _panel_default_user
        or caller.get("user_id")
        or "guest"
    )
    if effective_user == "voice-daemon":
        effective_user = _panel_default_user or "guest"

    logger.info("voice/command panel=%s session=%s user=%s len=%d",
                panel_id, session_id, effective_user, len(text))

    # ── Voice confirmation: check if we're waiting for yes/no ─────────────
    pending = _PENDING_CONFIRMATIONS.get(panel_id)
    if pending:
        now = time.monotonic()
        if now > pending.get("expire_at", 0):
            del _PENDING_CONFIRMATIONS[panel_id]
            pending = None
        else:
            lc = text.lower().strip().rstrip(".!?")
            if _contains_decision_keyword(lc, _CONFIRM_KEYWORDS):
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
                    if confirmed_intent.name in {"calendar_create", "calendar_show", "daily_briefing"}:
                        await _broadcast_calendar_ui(panel_id, reply_text, turn_key=_turn_key)
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
            elif _contains_decision_keyword(lc, _CANCEL_KEYWORDS):
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
        _t_scope_start = time.monotonic()
        _quick_intent = _detect(text)
        _quick_intent_name = _quick_intent.name if _quick_intent else None
        try:
            from voice_metrics import voice_stage_seconds, voice_intent_hit_count
            voice_stage_seconds.labels(stage="scope").observe(time.monotonic() - _t_scope_start)
            voice_intent_hit_count.labels(
                intent=(_quick_intent.name if _quick_intent else "none"),
            ).inc()
        except Exception:
            pass
        if _quick_intent and _quick_intent.name in _CONFIRM_INTENTS:
            # User-scoped intents must go through PIN challenge first.
            try:
                from voice_scope import classify as _vscope_pre
                _quick_scope = _vscope_pre(text, _quick_intent)
                if _quick_scope.scope == "user_scoped" and not _has_scope_identity:
                    _quick_intent = None
            except Exception:
                pass
        if _quick_intent and _quick_intent.name == "list_add":
            # Fast-path list adds so the touch chat can immediately show the updated list.
            _allow_quick_list = True
            try:
                from voice_scope import classify as _vscope_list
                _list_scope = _vscope_list(text, _quick_intent)
                if _list_scope.scope == "user_scoped" and not _has_scope_identity:
                    _allow_quick_list = False
            except Exception:
                pass
            if _allow_quick_list:
                try:
                    from intent_router import execute_intent as _exec_list
                    slots = _quick_intent.slots or {}
                    _list_reply = await _exec_list(_quick_intent, effective_user)
                    reply_text = _list_reply or "Added to your list."
                    await _broadcast_list_ui(
                        panel_id=panel_id,
                        item_text=str(slots.get("item", "")),
                        list_type=str(slots.get("list_type", "shopping")),
                        summary=reply_text,
                        turn_key=_turn_key,
                    )
                    _list_audio = await synthesize({"text": reply_text}, caller=caller)
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": reply_text,
                        "audio_base64": base64.b64encode(_list_audio.body).decode("ascii"),
                        "content_type": _list_audio.media_type,
                        "intent": "list_add",
                    }
                except Exception as _list_exc:
                    logger.warning("voice/command quick list_add failed: %s", _list_exc)
        if _quick_intent and _quick_intent.name == "calendar_create":
            # Calendar writes: execute immediately, then navigate to calendar page.
            _allow_quick_calendar = True
            try:
                from voice_scope import classify as _vscope_cal
                _cal_scope = _vscope_cal(text, _quick_intent)
                if _cal_scope.scope == "user_scoped" and not _has_scope_identity:
                    _allow_quick_calendar = False
            except Exception:
                pass
            if _allow_quick_calendar:
                try:
                    from intent_router import execute_intent as _exec_cal
                    slots = _quick_intent.slots or {}
                    _cal_reply = await _exec_cal(_quick_intent, effective_user)
                    reply_text = _cal_reply or "I added that calendar event."
                    await _broadcast_calendar_ui(panel_id=panel_id, summary=reply_text, turn_key=_turn_key)
                    _cal_audio = await synthesize({"text": reply_text}, caller=caller)
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": reply_text,
                        "audio_base64": base64.b64encode(_cal_audio.body).decode("ascii"),
                        "content_type": _cal_audio.media_type,
                        "intent": "calendar_create",
                    }
                except Exception as _cal_exc:
                    logger.warning("voice/command quick calendar_create failed: %s", _cal_exc)
        if _quick_intent and _quick_intent.name == "reminder_create":
            _allow_quick_reminder = True
            try:
                from voice_scope import classify as _vscope_rem
                _rem_scope = _vscope_rem(text, _quick_intent)
                if _rem_scope.scope == "user_scoped" and not _has_scope_identity:
                    _allow_quick_reminder = False
            except Exception:
                pass
            if _allow_quick_reminder:
                try:
                    from intent_router import execute_intent as _exec_rem
                    _rem_reply = await _exec_rem(_quick_intent, effective_user)
                    reply_text = _rem_reply or "I set that reminder."
                    await _broadcast_reminder_ui(panel_id=panel_id, summary=reply_text, turn_key=_turn_key)
                    _rem_audio = await synthesize({"text": reply_text}, caller=caller)
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": reply_text,
                        "audio_base64": base64.b64encode(_rem_audio.body).decode("ascii"),
                        "content_type": _rem_audio.media_type,
                        "intent": "reminder_create",
                    }
                except Exception as _rem_exc:
                    logger.warning("voice/command quick reminder_create failed: %s", _rem_exc)
        if _quick_intent and _quick_intent.name in _CONFIRM_INTENTS:
            slots = _quick_intent.slots or {}
            # Build a human-readable confirmation phrase.
            _phrases = {
                "calendar_create": lambda s: f"Add a calendar event: {s.get('title', 'untitled')}"
                    + (f" on {s.get('date', '')}" if s.get("date") else "")
                    + (f" at {s.get('time', '')}" if s.get("time") else "")
                    + ". Shall I confirm?",
                "list_add": lambda s: f"Add '{s.get('item', 'item')}' to "
                    + ("your shopping list" if s.get("list_type") == "shopping"
                       else f"your {s.get('list_type', 'shopping').replace('_', ' ')} list")
                    + ". Shall I confirm?",
                "reminder_create": lambda s: f"Set a reminder: {s.get('title', s.get('text', 'reminder'))}. Shall I confirm?",
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
                "user_id": effective_user,
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

    # ── Pass 2e A7: public-intent short-circuit ─────────────────────────────
    # If intent_router already has a direct answer (time, date, timer, recipe)
    # we skip the LLM entirely — TTS the answer straight from execute_intent.
    # This gives <=500ms first-audio-byte for the most common "quick query" phrases.
    from guest_policy import (
        PUBLIC_HOUSEHOLD_INTENTS as _PUBLIC_VOICE_INTENTS,
        can_use_voice_intent as _can_use_voice_intent,
        record_policy_decision as _record_guest_policy,
    )
    _voice_policy_user = {
        "user_id": _scope_identity_user or "guest",
        "role": "user" if _has_scope_identity else "guest",
    }
    try:
        from intent_router import detect_intent as _detect_pub, execute_intent as _exec_pub
        _pub_intent = _detect_pub(text)
        if _pub_intent and _pub_intent.name in _PUBLIC_VOICE_INTENTS:
            if not await _can_use_voice_intent(db, _voice_policy_user, _pub_intent.name):
                _record_guest_policy(
                    "blocked",
                    surface="voice",
                    resource="intent_fast",
                    action=_pub_intent.name,
                )
                _blocked_phrase = "That request is not available in the current role."
                _blocked_audio = await synthesize({"text": _blocked_phrase}, caller=caller)
                return {
                    "ok": True,
                    "panel_id": panel_id,
                    "reply": _blocked_phrase,
                    "audio_base64": base64.b64encode(_blocked_audio.body).decode("ascii"),
                    "content_type": _blocked_audio.media_type,
                    "status": "role_blocked",
                }
            _pub_reply = await _exec_pub(_pub_intent, effective_user if effective_user != "family-admin" else "guest")
            if _pub_reply:
                if _pub_intent.name == "weather":
                    await _broadcast_weather_ui(panel_id, _pub_reply, turn_key=_turn_key)
                if _pub_intent.name in {"calendar_show", "daily_briefing"}:
                    await _broadcast_calendar_ui(panel_id, _pub_reply, turn_key=_turn_key)
                try:
                    from voice_metrics import voice_stage_seconds, voice_turn_count, voice_intent_hit_count
                    voice_stage_seconds.labels(stage="llm_first_token").observe(0.0)
                    voice_intent_hit_count.labels(intent=_pub_intent.name).inc()
                    voice_turn_count.labels(outcome="ok", path="intent_fast").inc()
                except Exception:
                    pass
                try:
                    from push import broadcaster as _bc_pub
                    await _bc_pub.broadcast("all", "voice:responding", {"panel_id": panel_id, "text": _pub_reply[:200]})
                except Exception:
                    pass
                _pub_audio_resp = await synthesize({"text": _pub_reply}, caller=caller)
                _pub_audio_b64 = base64.b64encode(_pub_audio_resp.body).decode("ascii")
                try:
                    from push import broadcaster as _bc_pub2
                    await _bc_pub2.broadcast("all", "voice:done", {"panel_id": panel_id})
                except Exception:
                    pass
                try:
                    from voice_metrics import voice_stage_seconds
                    voice_stage_seconds.labels(stage="tts_first_byte").observe(time.monotonic() - _t_cmd_start)
                    voice_stage_seconds.labels(stage="total").observe(time.monotonic() - _t_cmd_start)
                except Exception:
                    pass
                _record_guest_policy(
                    "guest_allowed" if not _has_scope_identity else "auth_ok",
                    surface="voice",
                    resource="intent_fast",
                    action=_pub_intent.name,
                )
                return {
                    "ok": True, "panel_id": panel_id,
                    "reply": _pub_reply,
                    "audio_base64": _pub_audio_b64,
                    "content_type": _pub_audio_resp.media_type,
                    "intent": _pub_intent.name,
                }
    except Exception as _pub_exc:
        logger.warning("voice/command public-intent check failed: %s", _pub_exc)

    # Weather fallback: if public-intent short-circuit failed for any reason,
    # still resolve weather deterministically instead of dropping to generic LLM text.
    try:
        from intent_router import detect_intent as _detect_weather_fb, execute_intent as _exec_weather_fb
        _weather_fb_intent = _detect_weather_fb(text)
        if _weather_fb_intent and _weather_fb_intent.name == "weather":
            _weather_fb_reply = await _exec_weather_fb(
                _weather_fb_intent,
                effective_user if effective_user != "family-admin" else "guest",
            )
            if _weather_fb_reply:
                await _broadcast_weather_ui(panel_id, _weather_fb_reply, turn_key=_turn_key)
                _weather_fb_audio = await synthesize({"text": _weather_fb_reply}, caller=caller)
                return {
                    "ok": True,
                    "panel_id": panel_id,
                    "reply": _weather_fb_reply,
                    "audio_base64": base64.b64encode(_weather_fb_audio.body).decode("ascii"),
                    "content_type": _weather_fb_audio.media_type,
                    "intent": "weather",
                }
    except Exception as _weather_fb_exc:
        logger.warning("voice/command weather fallback failed: %s", _weather_fb_exc)

    # ── Pass 3 B3/B4: scope gate + PIN challenge ─────────────────────────────
    # Check if this utterance needs a known user. If so, and we have none,
    # challenge via panel_auth rather than leaking personal data to guest.
    _ident_for_scope = _scope_identity_user
    if os.environ.get("ZOE_VOICE_IDENT", "").strip() in ("1", "true"):
        try:
            from voice_scope import classify as _vscope, ScopeDecision as _SD
            from intent_router import detect_intent as _detect_scope
            _scope_intent = _detect_scope(text) if "_pub_intent" not in dir() else locals().get("_pub_intent")
            _scope: _SD = _vscope(text, _scope_intent)
            _scope_action = _scope.intent_name or "text"
            _scope_allowed = True
            if _scope.intent_name:
                _scope_allowed = await _can_use_voice_intent(db, _voice_policy_user, _scope.intent_name)
            if _scope.intent_name and not _scope_allowed and not (_scope.scope == "user_scoped" and not _ident_for_scope):
                _record_guest_policy(
                    "blocked",
                    surface="voice",
                    resource="scope_gate",
                    action=_scope_action,
                )
                _blocked_phrase = "That request is blocked for this role. Please sign in with an allowed profile."
                _blocked_audio = await synthesize({"text": _blocked_phrase}, caller=caller)
                return {
                    "ok": True,
                    "panel_id": panel_id,
                    "reply": _blocked_phrase,
                    "status": "role_blocked",
                    "audio_base64": base64.b64encode(_blocked_audio.body).decode("ascii"),
                    "content_type": _blocked_audio.media_type,
                }
            if _scope.scope == "user_scoped" and not _ident_for_scope:
                _record_guest_policy(
                    "auth_required",
                    surface="voice",
                    resource="scope_gate",
                    action=_scope_action,
                )
                import uuid as _uuid_mod
                _pending_id = _uuid_mod.uuid4().hex
                _pending_payload = {
                    "pending_id": _pending_id,
                    "transcript": text,
                    "session_id": session_id,
                    "expire_at": time.monotonic() + 120,
                }
                _PENDING_VOICE_IDENT[panel_id] = {
                    **_pending_payload,
                }
                _pin_phrase = "Please authenticate on the touch panel. Choose your profile and enter your PIN to continue."
                _pin_audio_resp = await synthesize({"text": _pin_phrase}, caller=caller)
                _pin_b64 = base64.b64encode(_pin_audio_resp.body).decode("ascii")
                _challenge_id = None
                try:
                    from routers.panel_auth import create_pin_challenge_internal
                    from database import get_db as _get_db
                    async for _db in _get_db():
                        _challenge = await create_pin_challenge_internal(
                            panel_id=panel_id,
                            user_id=None,
                            action_context={
                                "kind": "voice_turn",
                                "pending_id": _pending_id,
                                "panel_id": panel_id,
                                # Durable replay fallback if process-local in-memory maps are lost.
                                "pending_transcript": text,
                                "pending_session_id": session_id,
                            },
                            db=_db,
                        )
                        _challenge_id = (_challenge or {}).get("challenge_id")
                        break
                except Exception as _challenge_exc:
                    logger.warning("voice/command PIN challenge failed: %s", _challenge_exc)
                if not _challenge_id:
                    _fail_phrase = "I could not open the authentication screen just now. Please open the touch login page and try again."
                    _fail_audio = await synthesize({"text": _fail_phrase}, caller=caller)
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": _fail_phrase,
                        "status": "auth_unavailable",
                        "audio_base64": base64.b64encode(_fail_audio.body).decode("ascii"),
                        "content_type": _fail_audio.media_type,
                    }

                _requested = await _request_auth_ui(
                    panel_id=panel_id,
                    challenge_id=_challenge_id,
                    reason="Voice command needs identity. Enter your PIN to continue.",
                )
                if not _requested:
                    logger.warning(
                        "voice/command auth challenge created but panel auth action was not delivered: panel=%s challenge=%s",
                        panel_id,
                        _challenge_id,
                    )
                try:
                    from voice_metrics import voice_turn_count
                    voice_turn_count.labels(outcome="ok", path="awaiting_pin").inc()
                except Exception:
                    pass
                return {
                    "ok": True,
                    "panel_id": panel_id,
                    "reply": _pin_phrase,
                    "status": "awaiting_pin",
                    "audio_base64": _pin_b64,
                    "content_type": _pin_audio_resp.media_type,
                }
            _record_guest_policy(
                "guest_allowed" if not _ident_for_scope else "auth_ok",
                surface="voice",
                resource="scope_gate",
                action=_scope_action,
            )
        except Exception as _scope_exc:
            logger.debug("voice/command scope gate failed (non-fatal): %s", _scope_exc)

    # Forward to chat pipeline.
    reply_text = ""
    audio_b64: Optional[str] = None
    content_type = "audio/wav"

    try:
        t_chat_start = time.monotonic()

        # ── A4 (Pass 2c): in-process streaming + F3 identity fix ──────────
        # Previously we did an httpx.post -> /api/chat/?stream=false, which
        #   (a) paid TCP+JSON serialization cost twice,
        #   (b) silently dropped `identified_user_id` because chat.py never
        #       reads that field (Pass 0 finding), causing every identified
        #       turn to resolve to the device-token's user (family-admin).
        # We now call run_pi_agent_streaming directly:
        #   * user_id is passed explicitly -> memory writes, MemPalace reads,
        #     transaction rows, etc. all land under the right user.
        #   * token_budget is the same tight voice budget.
        #   * Streaming tokens are accumulated so that Pass 2b can hand the
        #     sentence-buffered stream to Kokoro.create_stream() and bring
        #     first-audio-byte latency down further.
        from pi_agent import run_pi_agent_streaming

        voice_timeout = float(os.environ.get("ZOE_VOICE_CHAT_TIMEOUT_S", "20"))
        try:
            _openclaw_cap = float(os.environ.get("ZOE_VOICE_OPENCLAW_TIMEOUT_S", str(voice_timeout)))
        except Exception:
            _openclaw_cap = voice_timeout
        openclaw_voice_timeout = max(5.0, min(voice_timeout, _openclaw_cap))
        _t_first_token: Optional[float] = None

        if stream:
            # Stream mode: emit chunks as soon as sentence boundaries appear.
            async def _generate_voice_stream():
                from push import broadcaster as _bc_stream
                import json as _json

                chunk_index = 0
                token_buf = ""
                full_reply_parts: list[str] = []
                _t_first_audio: Optional[float] = None

                try:
                    async def _emit_sentence(sentence: str):
                        nonlocal chunk_index, _t_first_audio
                        s = sentence.strip()
                        if not s:
                            return
                        full_reply_parts.append(s)
                        await _bc_stream.broadcast("all", "voice:responding", {
                            "panel_id": panel_id,
                            "text": s[:200],
                        })

                        # Prefer kokoro-sidecar (natural GPU voice); fall back to wyoming-piper then Kokoro stream
                        wav_bytes = await _synthesize_kokoro_sidecar(s)
                        _tts_provider = "kokoro-sidecar"
                        if not wav_bytes:
                            wav_bytes = await _synthesize_wyoming_piper(s)
                            _tts_provider = "wyoming-piper"
                        if wav_bytes:
                            if _t_first_audio is None:
                                _t_first_audio = time.monotonic() - t_chat_start
                                try:
                                    from voice_metrics import voice_stage_seconds
                                    voice_stage_seconds.labels(stage="tts_first_byte").observe(_t_first_audio)
                                except Exception:
                                    pass
                            header = _json.dumps({
                                "chunk": chunk_index,
                                "text": s[:80],
                                "provider": _tts_provider,
                            })
                            yield (header + "\n").encode()
                            yield base64.b64encode(wav_bytes) + b"\n"
                            chunk_index += 1
                            return

                        sent_any = False
                        async for wav_bytes in _stream_kokoro_sentence_wavs(s):
                            if _t_first_audio is None:
                                _t_first_audio = time.monotonic() - t_chat_start
                                try:
                                    from voice_metrics import voice_stage_seconds
                                    voice_stage_seconds.labels(stage="tts_first_byte").observe(_t_first_audio)
                                except Exception:
                                    pass
                            header = _json.dumps({
                                "chunk": chunk_index,
                                "text": s[:80],
                                "provider": "kokoro-onnx-stream",
                            })
                            yield (header + "\n").encode()
                            yield base64.b64encode(wav_bytes) + b"\n"
                            chunk_index += 1
                            sent_any = True

                        if sent_any:
                            return
                        # Fallback if stream synthesis unavailable.
                        audio_resp = await synthesize({"text": s}, caller=caller)
                        if _t_first_audio is None:
                            _t_first_audio = time.monotonic() - t_chat_start
                            try:
                                from voice_metrics import voice_stage_seconds
                                voice_stage_seconds.labels(stage="tts_first_byte").observe(_t_first_audio)
                            except Exception:
                                pass
                        header = _json.dumps({
                            "chunk": chunk_index,
                            "text": s[:80],
                            "provider": audio_resp.headers.get("X-Zoe-TTS-Provider", "fallback"),
                        })
                        yield (header + "\n").encode()
                        yield base64.b64encode(audio_resp.body) + b"\n"
                        chunk_index += 1

                    async def _emit_line(line: dict):
                        yield (_json.dumps(line) + "\n").encode()

                    async for delta in run_pi_agent_streaming(text, session_id, user_id=effective_user, voice_mode=True):
                        if not delta:
                            continue
                        if delta.startswith("__ESCALATE__:") or delta.startswith("__ESCALATE_BG__:"):
                            try:
                                from openclaw_ws import openclaw_cli
                                _, body = delta.split(":", 1)
                                reason, _, oc_task = body.partition("|")
                                oc_prompt = (oc_task or text).strip()
                                logger.info("voice/command stream escalation -> OpenClaw reason=%s", reason or "unspecified")
                                try:
                                    await _bc_stream.broadcast("all", "voice:responding", {
                                        "panel_id": panel_id,
                                        "text": "Give me a second - this one may take a little longer. I will come back with the result.",
                                    })
                                except Exception:
                                    pass
                                delta = (
                                    await asyncio.wait_for(
                                        openclaw_cli(oc_prompt, session_id, user_id=effective_user),
                                        timeout=openclaw_voice_timeout,
                                    )
                                ).strip()
                                if not delta:
                                    continue
                            except Exception as esc_exc:
                                logger.warning("voice/command OpenClaw escalation failed: %s", esc_exc)
                                delta = "I couldn't complete that advanced request right now. Please try again."
                        if _t_first_token is None:
                            _t_first_token = time.monotonic() - t_chat_start
                            try:
                                from voice_metrics import voice_stage_seconds
                                voice_stage_seconds.labels(stage="llm_first_token").observe(_t_first_token)
                            except Exception:
                                pass
                        token_buf += delta
                        ready, token_buf = _extract_complete_sentences(token_buf)
                        for sentence in ready:
                            async for out_chunk in _emit_sentence(sentence):
                                yield out_chunk

                    if token_buf.strip():
                        async for out_chunk in _emit_sentence(token_buf):
                            yield out_chunk
                        token_buf = ""

                    final_reply = " ".join(part.strip() for part in full_reply_parts if part.strip()).strip()
                    try:
                        await _bc_stream.broadcast("all", "ui_action", {
                            "action": {
                                "id": f"voice_card_{panel_id}",
                                "action_type": "show_card",
                                "payload": {
                                    "type": "answer",
                                    "data": {"text": final_reply[:300]},
                                    "panel_id": panel_id,
                                },
                            }
                        })
                    except Exception:
                        pass
                    try:
                        await _bc_stream.broadcast("all", "voice:done", {"panel_id": panel_id})
                    except Exception:
                        pass
                    try:
                        from voice_metrics import voice_stage_seconds, voice_turn_count
                        if _t_turn_start is None:
                            voice_stage_seconds.labels(stage="total").observe(time.monotonic() - _t_cmd_start)
                        voice_turn_count.labels(outcome="ok", path="command").inc()
                    except Exception:
                        pass
                    async for out_line in _emit_line({"done": True, "reply": final_reply, "panel_id": panel_id}):
                        yield out_line
                except asyncio.TimeoutError:
                    async for out_line in _emit_line({"error": "voice command stream timeout"}):
                        yield out_line
                except Exception as exc:
                    logger.warning("voice/command stream error: %s", exc)
                    async for out_line in _emit_line({"error": "voice command stream failure"}):
                        yield out_line

            return StreamingResponse(
                _generate_voice_stream(),
                media_type="application/x-zoe-audio-stream",
                headers={"Cache-Control": "no-cache"},
            )

        collected: list[str] = []

        async def _stream_collect() -> None:
            nonlocal _t_first_token
            async for delta in run_pi_agent_streaming(
                text,
                session_id,
                user_id=effective_user,
                voice_mode=True,
            ):
                if not delta:
                    continue
                if delta.startswith("__ESCALATE__:") or delta.startswith("__ESCALATE_BG__:"):
                    try:
                        from openclaw_ws import openclaw_cli
                        from push import broadcaster as _bc_escalate
                        _, body = delta.split(":", 1)
                        reason, _, oc_task = body.partition("|")
                        oc_prompt = (oc_task or text).strip()
                        logger.info("voice/command escalation -> OpenClaw reason=%s", reason or "unspecified")
                        try:
                            await _bc_escalate.broadcast("all", "voice:responding", {
                                "panel_id": panel_id,
                                "text": "Give me a second - this one may take a little longer. I will come back with the result.",
                            })
                        except Exception:
                            pass
                        delta = (
                            await asyncio.wait_for(
                                openclaw_cli(oc_prompt, session_id, user_id=effective_user),
                                timeout=openclaw_voice_timeout,
                            )
                        ).strip()
                        if not delta:
                            continue
                    except Exception as esc_exc:
                        logger.warning("voice/command OpenClaw escalation failed: %s", esc_exc)
                        delta = "I couldn't complete that advanced request right now. Please try again."
                if _t_first_token is None:
                    _t_first_token = time.monotonic() - t_chat_start
                    try:
                        from voice_metrics import voice_stage_seconds
                        voice_stage_seconds.labels(stage="llm_first_token").observe(_t_first_token)
                    except Exception:
                        pass
                collected.append(delta)

        _llm_timed_out = False
        try:
            await asyncio.wait_for(_stream_collect(), timeout=voice_timeout)
        except asyncio.TimeoutError:
            logger.warning("voice/command LLM stream timeout after %.1fs", voice_timeout)
            _llm_timed_out = True

        reply_text = "".join(collected).strip()
        _t_llm_total = time.monotonic() - t_chat_start

        logger.info(
            "voice/command LLM first_token=%.2fs total=%.2fs reply=%d chars user=%s",
            (_t_first_token if _t_first_token is not None else -1.0),
            _t_llm_total, len(reply_text), effective_user,
        )
    except Exception as exc:
        logger.error("voice/command chat error: %s", exc)
        try:
            from voice_metrics import voice_failure_reason_count
            voice_failure_reason_count.labels(path="command", reason="llm_error").inc()
        except Exception:
            pass
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

    degraded_reason: Optional[str] = None
    if not reply_text:
        degraded_reason = "llm_timeout" if _llm_timed_out else "llm_empty"
        reply_text = _FALLBACK_PHRASE
        audio_b64, content_type = await _make_fallback_audio()
        logger.warning("voice/command degraded fallback reason=%s panel=%s", degraded_reason, panel_id)

    # Fire broadcasts and TTS concurrently — TTS is the slow path (~1-2s).
    if reply_text:
        async def _broadcast_response():
            try:
                from push import broadcaster as _bc2
                await _bc2.broadcast("all", "voice:responding", {
                    "panel_id": panel_id,
                    "text": reply_text[:200],
                })
                await _bc2.broadcast("all", "ui_action", {
                    "action": {
                        "id": f"voice_card_{panel_id}",
                        "action_type": "show_card",
                        "payload": {
                            "type": "answer",
                            "data": {"text": reply_text[:300]},
                            "panel_id": panel_id,
                        },
                    }
                })
                if _should_handoff_calendar(text, reply_text, _quick_intent_name):
                    await _broadcast_calendar_ui(panel_id, reply_text, turn_key=_turn_key)
            except Exception:
                pass

        async def _run_tts():
            nonlocal audio_b64, content_type, degraded_reason
            if degraded_reason and audio_b64:
                return
            try:
                t0 = time.monotonic()
                audio_resp = await synthesize({"text": reply_text}, caller=caller)
                audio_b64 = base64.b64encode(audio_resp.body).decode("ascii")
                content_type = audio_resp.media_type
                _tts_elapsed = time.monotonic() - t0
                try:
                    # Pre-streaming: this is full TTS duration. Post-A3 it becomes
                    # first-audio-byte latency.
                    from voice_metrics import voice_stage_seconds
                    voice_stage_seconds.labels(stage="tts_first_byte").observe(_tts_elapsed)
                except Exception:
                    pass
                logger.info("voice/command TTS %.1fs for %d chars", _tts_elapsed, len(reply_text))
            except Exception as exc:
                logger.warning("voice/command TTS failed: %s", exc)
                audio_b64, content_type = await _make_fallback_audio()
                if not degraded_reason:
                    degraded_reason = "tts_failed"

        await asyncio.gather(_broadcast_response(), _run_tts())

    # Broadcast done so orb returns to idle.
    try:
        from push import broadcaster as _bc3
        await _bc3.broadcast("all", "voice:done", {"panel_id": panel_id})
    except Exception:
        pass

    # Record command-path total. If /voice/turn forwarded _t_turn_start we use
    # that so "total" is true end-to-end including STT; otherwise we bound it
    # to the command path only so the two labels stay comparable.
    try:
        from voice_metrics import voice_stage_seconds, voice_turn_count, voice_failure_reason_count
        if _t_turn_start is None:
            voice_stage_seconds.labels(stage="total").observe(time.monotonic() - _t_cmd_start)
        voice_turn_count.labels(outcome="degraded" if degraded_reason else "ok", path="command").inc()
        if degraded_reason:
            voice_failure_reason_count.labels(path="command", reason=degraded_reason).inc()
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

    t_turn_start = time.monotonic()

    # t_upload: base64 decode cost is our proxy for payload unpack time.
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc
    t_upload = time.monotonic() - t_turn_start

    suffix = ".wav" if (len(raw) >= 4 and raw[:4] == b"RIFF") else ".raw"

    # ── Phase 1: STT ──
    t_stt_start = time.monotonic()
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
        try:
            from voice_metrics import voice_turn_count, voice_failure_reason_count
            voice_turn_count.labels(outcome="error", path="turn").inc()
            voice_failure_reason_count.labels(path="turn", reason="stt_error").inc()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc

    t_stt = time.monotonic() - t_stt_start

    # Record upload + STT histograms right away so they're observable even
    # if the turn aborts later.
    try:
        from voice_metrics import voice_stage_seconds
        voice_stage_seconds.labels(stage="upload").observe(t_upload)
        voice_stage_seconds.labels(stage="stt").observe(t_stt)
    except Exception:
        pass

    transcript = transcript.strip()
    if not transcript:
        try:
            from voice_metrics import voice_turn_count, voice_failure_reason_count
            voice_turn_count.labels(outcome="empty_transcript", path="turn").inc()
            voice_failure_reason_count.labels(path="turn", reason="stt_empty").inc()
        except Exception:
            pass
        return {"ok": True, "panel_id": panel_id, "text": "", "reply": "", "audio_base64": None}

    logger.info("voice/turn panel=%s STT=%.1fs transcript=%r", panel_id, t_stt, transcript[:80])

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
        "_t_turn_start": t_turn_start,  # forwarded so voice_command can record end-to-end total
    }
    if (payload or {}).get("identified_user_id"):
        command_payload["identified_user_id"] = payload["identified_user_id"]

    result = await voice_command(command_payload, caller=caller, stream=False)
    result["text"] = transcript
    t_total = time.monotonic() - t_turn_start
    try:
        from voice_metrics import voice_stage_seconds, voice_turn_count
        voice_stage_seconds.labels(stage="total").observe(t_total)
        voice_turn_count.labels(
            outcome="ok" if result.get("ok") else "error",
            path="turn",
        ).inc()
    except Exception:
        pass
    logger.info("voice/turn total=%.1fs (STT=%.1fs LLM+TTS=%.1fs)", t_total, t_stt, t_total - t_stt)
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
    "note_create", "journal_create",
    "transaction_create", "people_create",
})

_CONFIRM_KEYWORDS = frozenset({
    "yes", "yeah", "yep", "confirm", "do it", "go ahead", "sure", "ok", "okay",
    "correct", "that's right", "sounds good", "proceed",
})
_CANCEL_KEYWORDS = frozenset({
    "no", "nope", "cancel", "stop", "don't", "abort", "never mind", "nevermind",
})
