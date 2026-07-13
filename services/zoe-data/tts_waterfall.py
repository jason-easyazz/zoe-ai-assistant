"""TTS synthesis engines for the voice waterfall (Kokoro sidecar / Kokoro ONNX /
local sidecar / Edge TTS / espeak-ng).

Pure mechanics extracted verbatim from routers/voice_tts.py: engine availability
checks, the per-engine synthesis calls, the Kokoro model + pooled-HTTP-client
singletons, and the speech-text normalisation the engines share. The waterfall
ORDER (which engine is tried before which) is policy and stays inline in the
/synthesize, /speak and /stream handlers in routers/voice_tts.py — pinned there
by test_canonical_invariants.py and test_voice_smoke_ci.py.
"""
import asyncio
import concurrent.futures
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import httpx

import voice_settings

logger = logging.getLogger(__name__)


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


def _has_espeak_ng() -> bool:
    return shutil.which("espeak-ng") is not None


def edge_tts_available() -> bool:
    return importlib.util.find_spec("edge_tts") is not None


# asyncio.create_subprocess_exec forks() SYNCHRONOUSLY on the calling (event
# loop) thread. zoe-data is a large multi-GB, multi-threaded process; a fork
# can wedge post-fork/pre-exec on an atfork lock, freezing the whole event loop
# (the outage class fixed for the hermes kanban CLI in commit 5e5ec34d — see
# services/zoe-data/AGENTS.md's "Background loops must not fork on the event
# loop thread" rule). These TTS fallback CLIs (espeak-ng, ffmpeg) are
# run-to-completion, so — like the kanban CLI — they're spawned via
# subprocess.run in a small dedicated thread pool instead, bounded twice: the
# blocking run()'s own timeout, plus a coroutine-side wait_for that still
# bounds the caller even if the fork itself wedges (run()'s timeout only starts
# once Popen returns).
_TTS_CLI_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="tts-cli"
)
_TTS_CLI_WAIT_GRACE_S = 5.0


async def _spawn_tts_cli(
    args: list[str], *, timeout: float
) -> "subprocess.CompletedProcess[bytes]":
    """Run a TTS-fallback CLI off the event loop, bounded even if fork() wedges."""
    loop = asyncio.get_running_loop()

    def _blocking() -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run(
            args, capture_output=True, timeout=timeout, check=False
        )

    return await asyncio.wait_for(
        loop.run_in_executor(_TTS_CLI_POOL, _blocking),
        timeout=timeout + _TTS_CLI_WAIT_GRACE_S,
    )


async def _synthesize_espeak(text: str, speed: int, pitch: int, volume: int) -> bytes:
    if not _has_espeak_ng():
        raise RuntimeError("espeak-ng is not installed")

    with tempfile.TemporaryDirectory(prefix="zoe-tts-") as td:
        mono_path = Path(td) / "mono.wav"
        stereo_path = Path(td) / "stereo.wav"

        try:
            proc = await _spawn_tts_cli(
                [
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
                ],
                timeout=15.0,
            )
        except (subprocess.TimeoutExpired, asyncio.TimeoutError) as exc:
            raise RuntimeError("espeak-ng timed out") from exc
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode(errors="ignore").strip()
            raise RuntimeError(f"espeak-ng failed: {err}")

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

        try:
            proc = await _spawn_tts_cli(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(mp3_path),
                    "-ac",
                    "2",
                    "-ar",
                    "22050",
                    str(wav_path),
                ],
                timeout=15.0,
            )
        except (subprocess.TimeoutExpired, asyncio.TimeoutError):
            return mp3_path.read_bytes()
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


_MONTHS_SPOKEN = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]")


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _humanize_iso_date(m: "re.Match") -> str:
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"the {_ordinal(d)} of {_MONTHS_SPOKEN[mo - 1]} {y}"
    except Exception:
        pass
    return m.group(0)


def _clean_for_speech(text: str) -> str:
    """Normalize reply text so Kokoro doesn't read junk aloud: strip a leaked
    JSON object, markdown (* _ ` # ~ |), emoji; speak ISO dates as words; turn
    em/en dashes and ° into spoken forms. Applied at every TTS entry point."""
    if not text:
        return text
    t = str(text)
    t = re.sub(r"^\s*\{[^{}]*\}\s*", "", t)            # drop leading JSON leak
    t = _ISO_DATE_RE.sub(_humanize_iso_date, t)         # 2026-06-22 -> the 22nd of June 2026
    t = t.replace("°C", " degrees").replace("°F", " degrees").replace("°", " degrees")
    t = t.replace("—", ", ").replace("–", ", ")
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"[*_`#~|>]+", "", t)                    # markdown
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)              # no space before punctuation
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


# Pooled client for the Kokoro sidecar, reused across sentences so each spoken
# sentence doesn't pay a fresh TCP/connection setup (the sidecar is hit once per
# sentence on the streaming voice path — per-call AsyncClient added fixed latency
# to every inter-sentence boundary).
_KOKORO_HTTP: "Optional[httpx.AsyncClient]" = None


def _kokoro_http_client() -> "httpx.AsyncClient":
    global _KOKORO_HTTP
    if _KOKORO_HTTP is None or _KOKORO_HTTP.is_closed:
        _KOKORO_HTTP = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60.0),
        )
    return _KOKORO_HTTP


async def _synthesize_kokoro_sidecar(text: str, voice: Optional[str] = None) -> Optional[bytes]:
    """Synthesize via the Kokoro sidecar (the TTS rock's warm process).

    Calls the local FastAPI sidecar on port 10201 which keeps the Kokoro
    model warm.  Sub-200ms warm latency on Jetson Orin.
    Set ZOE_KOKORO_SIDECAR_URL to override (default http://127.0.0.1:10201).
    Falls through silently if the sidecar is unavailable.

    ``voice``: explicit per-call override (the settings Preview); otherwise the
    persisted household preference resolves, with ZOE_KOKORO_VOICE/af_sky as the
    fallback default (voice_settings.resolve_tts_voice).
    """
    text = _clean_for_speech(text)
    sidecar_url = os.environ.get("ZOE_KOKORO_SIDECAR_URL", "http://127.0.0.1:10201").rstrip("/")
    voice = await voice_settings.resolve_tts_voice(voice)
    try:
        client = _kokoro_http_client()
        r = await client.post(
            f"{sidecar_url}/synthesize",
            json={"text": text, "voice": voice},
        )
        if r.status_code >= 400 or not r.content:
            logger.debug("kokoro-sidecar HTTP %s", r.status_code)
            return None
        return r.content
    except httpx.TransportError as exc:
        # A pooled client does NOT auto-close on a transport error the way the old
        # per-call `async with` did, so a timed-out / reset connection would be
        # re-checked-out for the next sentence and fail again. Recycle the pooled
        # client so the next call reconnects cleanly.
        global _KOKORO_HTTP
        logger.debug("kokoro-sidecar transport error, recycling pooled client: %s", exc)
        try:
            if _KOKORO_HTTP is not None:
                await _KOKORO_HTTP.aclose()
        except Exception:
            pass
        _KOKORO_HTTP = None
        return None
    except Exception as exc:
        logger.debug("kokoro-sidecar unavailable: %s", exc)
        return None


async def _synthesize_kokoro(text: str, voice: Optional[str] = None) -> Optional[bytes]:
    """Synthesize using Kokoro ONNX (thewh1teagle/kokoro-onnx).

    ~82M param ONNX model — sub-100ms first chunk on Jetson CUDA, natural AU voice.
    Install: pip install kokoro-onnx
    Model: download from https://github.com/thewh1teagle/kokoro-onnx/releases
    Set ZOE_KOKORO_MODEL to the path of the kokoro-v1.0.onnx file.
    Voice resolves per call: explicit override → persisted preference →
    ZOE_KOKORO_VOICE env default (af_sky). Uses module-level cached instance to
    avoid ~500ms model load per call.
    """
    kokoro = await _get_kokoro_instance()
    if kokoro is None:
        return None
    voice = await voice_settings.resolve_tts_voice(voice)
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


async def _stream_kokoro_sentence_wavs(sentence: str, voice: Optional[str] = None):
    """Yield WAV chunks from Kokoro create_stream() for one sentence."""
    kokoro = await _get_kokoro_instance()
    if kokoro is None or not hasattr(kokoro, "create_stream"):
        return
    voice = await voice_settings.resolve_tts_voice(voice)
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


def kokoro_ready() -> bool:
    return _kokoro_instance is not None


def kokoro_configured() -> bool:
    model_path = os.environ.get("ZOE_KOKORO_MODEL", "").strip()
    return bool(model_path and os.path.isfile(model_path) and importlib.util.find_spec("kokoro_onnx") is not None)
