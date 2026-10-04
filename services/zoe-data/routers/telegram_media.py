"""Telegram media contracts — voice notes in and out (flag-dark, internal-only).

Two small internal endpoints for the Telegram lane (labs/flue-zoe-telegram-2x),
mounted ONLY when ``ZOE_TELEGRAM_MEDIA`` is on (``register(app)`` below; off =
the routes do not exist, 404):

  POST /api/system/telegram/transcribe   raw OGG/Opus (or any ffmpeg-decodable
                                          audio) body → {"ok", "text", "duration_s"}
  POST /api/system/telegram/synthesize   {"text"} → OGG/Opus bytes (audio/ogg)

WHY NOT /api/voice/transcribe: that route broadcasts ``voice:transcript`` to
EVERY panel and captures into the panel STT corpus — a phone whisper would land
on the wall screen and seed the panel corpus with phone-mic audio. These routes
emit no push event and capture nothing unless ``ZOE_TELEGRAM_STT_CAPTURE_DIR``
names a SEPARATE directory (never ``~/.zoe-voice-samples``).

VOICE PATH UNTOUCHED: this module only CALLS the service's existing helpers —
``routers.voice_tts._transcribe_audio(path, capture=False)`` (the in-process
Moonshine singleton, behind its own ``_moonshine_infer_lock``, so Telegram STT
serialises behind the panel's) and ``tts_waterfall._synthesize_kokoro_sidecar``
(the Kokoro sidecar on :10201, 24 kHz WAV). No model is loaded here, nothing in
``VOICE_PATH_PATTERNS`` changes, and the replay gate does not front this file.

Decode/encode is ``ffmpeg`` (static 7.0.2 with libopus at ``~/.local/bin/ffmpeg``
on the box; ``ZOE_TELEGRAM_FFMPEG`` overrides) run as an argv subprocess with a
timeout — never a shell string. Every failure is structured JSON, never a 500.

Design record: docs/research/telegram-calls-voice-photos-2026-10-04.md §3.1.
Operator record: docs/knowledge/telegram-voice-notes.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from auth import require_internal_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system/telegram", tags=["telegram-media"])

# Moonshine's contract: 16 kHz mono 16-bit PCM WAV (voice_tts._MOONSHINE_SAMPLE_RATE).
_STT_RATE = 16000
_STT_BYTES_PER_S = _STT_RATE * 2
_WAV_HEADER_BYTES = 44
# Hard ingress bound for one upload. A 60 s Telegram voice note is ~250 KB; an
# mp3 ``audio`` message at 320 kbps for 60 s is ~2.4 MB. Not a flag on purpose.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# Hard bound on text handed to Kokoro per voice reply.
MAX_TTS_CHARS = 2000


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def media_enabled() -> bool:
    """``ZOE_TELEGRAM_MEDIA`` — default OFF (routes not mounted)."""
    return _truthy(os.environ.get("ZOE_TELEGRAM_MEDIA", "off"))


def max_seconds() -> int:
    """``ZOE_TELEGRAM_VOICE_MAX_S`` — the longest note we transcribe (default 60).
    Read lazily so a .env change + restart (or a test) applies without re-import."""
    try:
        return max(1, int(os.environ.get("ZOE_TELEGRAM_VOICE_MAX_S", "60")))
    except ValueError:
        return 60


def ffmpeg_timeout_s() -> float:
    try:
        return max(1.0, float(os.environ.get("ZOE_TELEGRAM_FFMPEG_TIMEOUT_S", "20")))
    except ValueError:
        return 20.0


def ffmpeg_binary() -> Optional[str]:
    """``ZOE_TELEGRAM_FFMPEG`` → PATH → ``~/.local/bin/ffmpeg`` (where the box's
    static libopus build lives; zoe-data's unit PATH does not include it)."""
    override = os.environ.get("ZOE_TELEGRAM_FFMPEG", "").strip()
    if override:
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "ffmpeg"
    return str(local) if local.is_file() else None


def capture_dir() -> Optional[Path]:
    """``ZOE_TELEGRAM_STT_CAPTURE_DIR`` — unset (default) = capture nothing."""
    raw = os.environ.get("ZOE_TELEGRAM_STT_CAPTURE_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code, **extra}, status_code=status)


# ─── subprocess seam (tests replace this) ────────────────────────────────────


async def _run_ffmpeg(argv: list[str], timeout_s: float) -> tuple[int, str]:
    """Run ffmpeg as an argv subprocess (no shell). Returns (returncode, stderr).
    A timeout kills the process and returns -1."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode or 0, (err or b"")[-400:].decode("utf-8", "replace")


def decode_argv(binary: str, src: str, dst: str, max_s: int) -> list[str]:
    """OGG/Opus (or anything ffmpeg reads) → 16 kHz mono s16 WAV, clipped at
    ``max_s + 1`` seconds so an over-long note is cheap to detect and refuse."""
    return [
        binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", src,
        "-vn", "-ac", "1", "-ar", str(_STT_RATE), "-sample_fmt", "s16",
        "-t", str(max_s + 1),
        "-f", "wav", dst,
    ]


def encode_argv(binary: str, src: str, dst: str) -> list[str]:
    """Kokoro WAV → Telegram-playable OGG/Opus voice note."""
    return [
        binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", src,
        "-vn", "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
        "-application", "voip",
        "-f", "ogg", dst,
    ]


# ─── model seams (thin wrappers over the service's existing helpers) ─────────


async def _transcribe_wav(wav_path: str) -> str:
    """The service's Moonshine, capture=False: no panel broadcast, no panel corpus."""
    from routers.voice_tts import _transcribe_audio

    return await _transcribe_audio(wav_path, capture=False)


async def _synthesize_wav(text: str) -> Optional[bytes]:
    """The Kokoro sidecar (:10201) → 24 kHz WAV bytes, or None when unavailable."""
    from tts_waterfall import _synthesize_kokoro_sidecar

    return await _synthesize_kokoro_sidecar(text)


def _maybe_capture(wav_path: str, text: str) -> None:
    """Optional phone-mic capture into a SEPARATE corpus — never the panel one."""
    target = capture_dir()
    if target is None:
        return
    try:
        target.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = target / f"tg-{stamp}-{os.getpid()}"
        shutil.copyfile(wav_path, f"{base}.wav")
        Path(f"{base}.txt").write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("telegram STT capture failed (non-fatal): %s", exc)


# ─── routes ──────────────────────────────────────────────────────────────────


@router.post("/transcribe")
async def telegram_transcribe(request: Request, _: None = Depends(require_internal_token)):
    """Raw audio body (Telegram voice note OGG/Opus, or an ``audio`` file) →
    ``{"ok": true, "text": ..., "duration_s": ...}``. Bounded by
    ``MAX_UPLOAD_BYTES`` and ``ZOE_TELEGRAM_VOICE_MAX_S`` (413 beyond either)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        return _error(413, "audio too large", max_bytes=MAX_UPLOAD_BYTES)
    body = await request.body()
    if not body:
        return _error(400, "empty audio")
    if len(body) > MAX_UPLOAD_BYTES:
        return _error(413, "audio too large", max_bytes=MAX_UPLOAD_BYTES)
    binary = ffmpeg_binary()
    if not binary:
        return _error(503, "ffmpeg unavailable")

    max_s = max_seconds()
    timings: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="zoe-tg-") as tmp:
        src = os.path.join(tmp, "in.audio")
        dst = os.path.join(tmp, "out.wav")
        with open(src, "wb", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
            fh.write(body)

        t0 = time.monotonic()
        rc, err = await _run_ffmpeg(decode_argv(binary, src, dst, max_s), ffmpeg_timeout_s())
        timings["decode_ms"] = int((time.monotonic() - t0) * 1000)
        if rc == -1:
            return _error(504, "decode timeout")
        if rc != 0 or not os.path.isfile(dst):
            logger.info("telegram transcribe: ffmpeg rc=%s %s", rc, err.strip()[:200])
            return _error(422, "undecodable audio")

        pcm_bytes = max(0, os.path.getsize(dst) - _WAV_HEADER_BYTES)
        duration_s = pcm_bytes / _STT_BYTES_PER_S
        if duration_s > max_s:
            return _error(413, "audio too long", max_s=max_s)
        if pcm_bytes == 0:
            return {"ok": True, "text": "", "duration_s": 0.0, "timings": timings}

        t1 = time.monotonic()
        try:
            text = (await _transcribe_wav(dst) or "").strip()
        except Exception as exc:  # noqa: BLE001 — a backend failure must stay structured
            logger.warning("telegram transcribe: STT failed: %s", exc)
            return _error(502, "stt failed")
        timings["stt_ms"] = int((time.monotonic() - t1) * 1000)
        _maybe_capture(dst, text)

    logger.info(
        "TELEGRAM_STT duration=%.1fs decode_ms=%s stt_ms=%s chars=%d",
        duration_s, timings.get("decode_ms"), timings.get("stt_ms"), len(text),
    )
    return {"ok": True, "text": text, "duration_s": round(duration_s, 2), "timings": timings}


class _SynthesizeBody(BaseModel):
    text: str


@router.post("/synthesize")
async def telegram_synthesize(body: _SynthesizeBody, _: None = Depends(require_internal_token)):
    """``{"text"}`` → OGG/Opus bytes for ``sendVoice``. Kokoro → ffmpeg libopus.
    No push event. 503 when Kokoro is unavailable (the lane falls back to text)."""
    text = (body.text or "").strip()
    if not text:
        return _error(400, "empty text")
    if len(text) > MAX_TTS_CHARS:
        return _error(413, "text too long", max_chars=MAX_TTS_CHARS)
    binary = ffmpeg_binary()
    if not binary:
        return _error(503, "ffmpeg unavailable")

    t0 = time.monotonic()
    try:
        wav = await _synthesize_wav(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram synthesize: TTS raised: %s", exc)
        wav = None
    if not wav:
        return _error(503, "tts unavailable")
    tts_ms = int((time.monotonic() - t0) * 1000)

    with tempfile.TemporaryDirectory(prefix="zoe-tg-") as tmp:
        src = os.path.join(tmp, "reply.wav")
        dst = os.path.join(tmp, "reply.ogg")
        with open(src, "wb", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
            fh.write(wav)
        t1 = time.monotonic()
        rc, err = await _run_ffmpeg(encode_argv(binary, src, dst), ffmpeg_timeout_s())
        if rc == -1:
            return _error(504, "encode timeout")
        if rc != 0 or not os.path.isfile(dst):
            logger.info("telegram synthesize: ffmpeg rc=%s %s", rc, err.strip()[:200])
            return _error(502, "encode failed")
        ogg = Path(dst).read_bytes()
        encode_ms = int((time.monotonic() - t1) * 1000)

    logger.info("TELEGRAM_TTS chars=%d tts_ms=%d encode_ms=%d ogg_bytes=%d", len(text), tts_ms, encode_ms, len(ogg))
    return Response(
        content=ogg,
        media_type="audio/ogg",
        headers={"X-Zoe-TTS-Ms": str(tts_ms), "X-Zoe-Encode-Ms": str(encode_ms)},
    )


def register(app: FastAPI) -> bool:
    """Mount the router iff ``ZOE_TELEGRAM_MEDIA`` is on. Returns whether it did.
    Flag off = the routes are ABSENT (404), not merely refusing."""
    if not media_enabled():
        return False
    app.include_router(router)
    logger.info("Telegram media router registered (ZOE_TELEGRAM_MEDIA on; ffmpeg=%s)", ffmpeg_binary() or "MISSING")
    return True
