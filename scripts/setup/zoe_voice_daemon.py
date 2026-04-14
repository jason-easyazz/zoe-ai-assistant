#!/usr/bin/env python3
"""
Zoe voice daemon — runs on Raspberry Pi.
Listens for an openWakeWord model (default: hey_jarvis), then records and sends STT to Jetson.
Place hey_zoe.onnx next to this file to use a custom "Hey Zoe" model instead.

Capabilities:
  - Wake word detection (openWakeWord ONNX)
  - Command recording + STT (Whisper on Jetson)
  - Barge-in: Silero VAD runs during TTS playback; detected speech interrupts playback
  - Ambient memory: always-on VAD captures room speech for Jetson transcription
  - Speaker ID: resemblyzer embeddings identify the speaker before posting command
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import wave
import math

import http.server
import numpy as np
import pyaudio
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [voice-daemon] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Optional persistent log file (e.g. ZOE_VOICE_LOG=/home/pi/.zoe-voice/voice.log)
_voice_log = os.environ.get("ZOE_VOICE_LOG", "").strip()
if _voice_log:
    try:
        _fh = logging.FileHandler(_voice_log)
        _fh.setFormatter(logging.Formatter("%(asctime)s [voice-daemon] %(levelname)s %(message)s"))
        logging.getLogger().addHandler(_fh)
        log.info("Logging to %s", _voice_log)
    except OSError as exc:
        log.warning("Could not open ZOE_VOICE_LOG %s: %s", _voice_log, exc)

ZOE_URL = os.environ.get("ZOE_URL", "https://zoe.local").rstrip("/")
HA_BRIDGE_URL = os.environ.get("HA_BRIDGE_URL", "").rstrip("/")
VOICE_ROUTE_MODE = (os.environ.get("VOICE_ROUTE_MODE", "direct").strip().lower() or "direct")
PANEL_ID = os.environ.get("PANEL_ID", "zoe-touch-pi")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "default")
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1280"))
RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS_MAX", "8"))
SILENCE_TIMEOUT_S = float(os.environ.get("SILENCE_TIMEOUT_S", "1.5"))
# Default 0.28 — 0.35 misses many real mics/rooms; tune via WAKEWORD_THRESHOLD.
WAKEWORD_THRESHOLD = float(os.environ.get("WAKEWORD_THRESHOLD", "0.28"))
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")
WAKEWORD_DEBUG = os.environ.get("WAKEWORD_DEBUG", "").lower() in ("1", "true", "yes")
# ── Barge-in: Silero VAD during TTS playback ─────────────────────────────
BARGE_IN_ENABLED = os.environ.get("BARGE_IN_ENABLED", "true").lower() in ("1", "true", "yes")
# VAD probability threshold for barge-in detection (0.0-1.0).
BARGE_IN_THRESHOLD = float(os.environ.get("BARGE_IN_THRESHOLD", "0.5"))
# Guard window after TTS ends — ignore VAD for this many ms (Jabra AEC residual).
POST_TTS_PROTECTION_MS = int(os.environ.get("POST_TTS_PROTECTION_MS", "300"))
# ── Ambient memory: always-on VAD captures room speech ────────────────────
AMBIENT_CAPTURE_ENABLED = os.environ.get("AMBIENT_CAPTURE_ENABLED", "false").lower() in ("1", "true", "yes")
AMBIENT_VAD_THRESHOLD = float(os.environ.get("AMBIENT_VAD_THRESHOLD", "0.4"))
AMBIENT_MIN_SPEECH_MS = int(os.environ.get("AMBIENT_MIN_SPEECH_MS", "500"))
AMBIENT_SILENCE_PAD_MS = int(os.environ.get("AMBIENT_SILENCE_PAD_MS", "800"))
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "7777"))
MIN_WAKE_INTERVAL_S = float(os.environ.get("MIN_WAKE_INTERVAL_S", "3.0"))
WAKE_CONFIRM_COUNT = int(os.environ.get("WAKE_CONFIRM_COUNT", "2"))
WAKE_CONFIRM_WINDOW_S = float(os.environ.get("WAKE_CONFIRM_WINDOW_S", "0.8"))
# Local wake beep (plays immediately on wake, independent of backend).
WAKE_BEEP_ENABLED = os.environ.get("WAKE_BEEP_ENABLED", "true").lower() in ("1", "true", "yes")
WAKE_BEEP_FREQ_HZ = int(os.environ.get("WAKE_BEEP_FREQ_HZ", "1046"))  # C6
WAKE_BEEP_DURATION_MS = int(os.environ.get("WAKE_BEEP_DURATION_MS", "120"))
WAKE_BEEP_VOLUME = float(os.environ.get("WAKE_BEEP_VOLUME", "0.22"))  # 0..1
# Route playback explicitly (default to same ALSA device family as mic).
AUDIO_OUTPUT_DEVICE = os.environ.get("AUDIO_OUTPUT_DEVICE", AUDIO_DEVICE).strip() or "default"
# After TTS plays on the same speakerphone as the mic, ignore wake scores for this long (echo / Whisper "yes" loop).
POST_PLAY_COOLDOWN_S = float(os.environ.get("POST_PLAY_COOLDOWN_S", "4.0"))
# Extra settle time after playback before arming wake again (room reverb).
POST_PLAY_TAIL_S = float(os.environ.get("POST_PLAY_TAIL_S", "0.8"))
# Note: debounce_time on oww.predict() requires a matching `threshold` dict in some openwakeword versions
# and was crashing the daemon — post-play cooldown + oww.reset() handle repeats instead.
# Transcripts (usually Whisper hallucinations on silence or TTS bleed) — do not send to chat.
_junk_raw = os.environ.get(
    "VOICE_IGNORE_TRANSCRIPTS",
    "yes,yeah,yep,yup,no,ok,okay,thank you,thanks,hi,hello,hmm,um",
)
VOICE_IGNORE_TRANSCRIPTS = frozenset(x.strip().lower() for x in _junk_raw.split(",") if x.strip())

_headers = {"X-Device-Token": DEVICE_TOKEN, "Content-Type": "application/json"}
_shutdown = threading.Event()
# Monotonic time: ignore wake-word triggers until this (acoustic echo / TTS tail).
_ignore_wake_until: float = 0.0
_last_wake_at: float = 0.0
# Set in main() after resolving ALSA / PyAudio device — must match wake + record streams.
_INPUT_DEVICE_INDEX: int | None = None

# ── Barge-in state ──────────────────────────────────────────────────────────
# Flag set by the barge-in thread to signal active TTS playback should stop.
_barge_in_requested = threading.Event()
# Current TTS subprocess (aplay/mpg123) so we can kill it on barge-in.
_tts_process: subprocess.Popen | None = None
_tts_process_lock = threading.Lock()

# ── Recording-active flag (B14) ──────────────────────────────────────────────
# Set to True during the entire wake→record→STT cycle so ambient thread pauses.
_recording_active = threading.Event()

# ── Shared audio fan-out queues (A6) ─────────────────────────────────────────
# Single PyAudio input stream; chunks distributed to consumers via queues.
_WAKE_QUEUE: "queue.Queue[bytes]" = None   # type: ignore  # set in main()
_BARGE_QUEUE: "queue.Queue[bytes]" = None  # type: ignore  # set in main()
_AMBIENT_QUEUE: "queue.Queue[bytes]" = None  # type: ignore  # set in main()
import queue as _queue_module


# ── Silero VAD loader (lazy) ─────────────────────────────────────────────────
_silero_model = None
_silero_utils = None
_silero_lock = threading.Lock()


def _get_silero_vad():
    """Load Silero VAD model lazily — ~1MB, loads in <200ms on Pi."""
    global _silero_model, _silero_utils
    with _silero_lock:
        if _silero_model is not None:
            return _silero_model, _silero_utils
        try:
            import torch  # type: ignore
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
            )
            _silero_model, _silero_utils = model, utils
            log.info("Silero VAD loaded.")
            return model, utils
        except Exception as exc:
            log.warning("Silero VAD not available (%s) — barge-in/ambient disabled", exc)
            return None, None


def _vad_prob(model, chunk_int16: np.ndarray, sample_rate: int = 16000) -> float:
    """Return speech probability [0,1] for a 30ms PCM chunk."""
    try:
        import torch  # type: ignore
        tensor = torch.from_numpy(chunk_int16.astype(np.float32) / 32768.0)
        return float(model(tensor, sample_rate).item())
    except Exception:
        return 0.0


def _api_post(path: str, data: dict, timeout: int = 60) -> dict:
    url = f"{ZOE_URL}{path}"
    try:
        r = requests.post(url, json=data, headers=_headers, timeout=timeout, verify=VERIFY_SSL)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.SSLError:
        log.warning("SSL error — set VERIFY_SSL=false if using self-signed cert")
        raise
    except Exception as exc:
        log.error("API error %s: %s", path, exc)
        return {"ok": False, "error": str(exc)}


def _bridge_post(path: str, data: dict, timeout: int = 60) -> dict:
    if not HA_BRIDGE_URL:
        return {"ok": False, "error": "HA_BRIDGE_URL not configured"}
    url = f"{HA_BRIDGE_URL}{path}"
    try:
        r = requests.post(url, json=data, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("Bridge API error %s: %s", path, exc)
        return {"ok": False, "error": str(exc)}


def play_audio_b64(audio_b64: str, content_type: str = "audio/wav"):
    """Decode and play base64 audio via aplay/mpg123.

    Registers the subprocess in _tts_process so the barge-in thread can kill it.
    Checks _barge_in_requested before and during playback.
    """
    global _tts_process
    if not audio_b64:
        return
    _barge_in_requested.clear()
    try:
        raw = base64.b64decode(audio_b64)
        ext = "mp3" if "mpeg" in content_type else "wav"
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(raw)
            fpath = f.name
        if ext == "mp3":
            if AUDIO_OUTPUT_DEVICE != "default":
                cmd = ["mpg123", "-q", "-a", AUDIO_OUTPUT_DEVICE, fpath]
            else:
                cmd = ["mpg123", "-q", fpath]
        else:
            cmd = ["aplay", "-q"]
            if AUDIO_OUTPUT_DEVICE != "default":
                cmd += ["-D", AUDIO_OUTPUT_DEVICE]
            cmd.append(fpath)
        proc = subprocess.Popen(cmd)
        with _tts_process_lock:
            _tts_process = proc
        # Poll for barge-in while playback runs.
        while proc.poll() is None:
            if _barge_in_requested.is_set():
                log.info("Barge-in: stopping TTS playback")
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            time.sleep(0.05)
        with _tts_process_lock:
            _tts_process = None
        try:
            os.unlink(fpath)
        except OSError:
            pass
    except Exception as exc:
        log.warning("Audio playback failed: %s", exc)


def _barge_in_vad_thread():
    """Background thread: runs Silero VAD during TTS playback to detect barge-in.

    Reads audio chunks from the shared _BARGE_QUEUE (fed by the single input stream).
    When human speech is detected during TTS, sets _barge_in_requested so
    play_audio_b64() stops the subprocess.
    """
    model, _ = _get_silero_vad()
    if model is None:
        log.info("Barge-in thread: Silero VAD unavailable, barge-in disabled.")
        return

    log.info("Barge-in VAD thread started (threshold=%.2f)", BARGE_IN_THRESHOLD)
    _protection_until: float = 0.0
    while not _shutdown.is_set():
        try:
            chunk = _BARGE_QUEUE.get(timeout=0.1)
        except _queue_module.Empty:
            continue

        # Only do VAD inference when TTS is actually playing.
        if _tts_process is None:
            continue

        now = time.monotonic()
        if now < _protection_until:
            continue

        prob = _vad_prob(model, np.frombuffer(chunk, dtype=np.int16))
        if prob >= BARGE_IN_THRESHOLD:
            log.info("Barge-in detected (VAD prob=%.2f)", prob)
            _barge_in_requested.set()
            # Protection window: don't fire again for POST_TTS_PROTECTION_MS after TTS ends.
            _protection_until = time.monotonic() + POST_TTS_PROTECTION_MS / 1000.0
    log.info("Barge-in VAD thread stopped.")


def _ambient_capture_thread():
    """Background thread: always-on VAD captures room speech for ambient memory.

    Reads audio chunks from the shared _AMBIENT_QUEUE (fed by the single input stream).
    Speech segments are buffered and POSTed to Jetson /api/voice/ambient.
    Results are stored in the ambient_memory table on the Jetson.
    Raw audio is never stored — only transcripts are kept.
    Pauses during the full wake→record→STT cycle via _recording_active flag.
    """
    if not AMBIENT_CAPTURE_ENABLED:
        log.info("Ambient capture disabled (AMBIENT_CAPTURE_ENABLED=false)")
        return

    model, _ = _get_silero_vad()
    if model is None:
        log.info("Ambient capture: Silero VAD unavailable, skipping.")
        return

    log.info("Ambient capture thread started (VAD threshold=%.2f)", AMBIENT_VAD_THRESHOLD)
    speech_frames: list[bytes] = []
    in_speech = False
    silence_chunks = 0
    silence_pad = int(AMBIENT_SILENCE_PAD_MS * SAMPLE_RATE / CHUNK_SIZE / 1000)
    min_speech_chunks = int(AMBIENT_MIN_SPEECH_MS * SAMPLE_RATE / CHUNK_SIZE / 1000)

    while not _shutdown.is_set():
        try:
            chunk = _AMBIENT_QUEUE.get(timeout=0.1)
        except _queue_module.Empty:
            continue

        # Don't capture during TTS playback, wake/record/STT cycle, or cooldown.
        if (_tts_process is not None
                or _recording_active.is_set()
                or time.monotonic() < _ignore_wake_until):
            speech_frames.clear()
            in_speech = False
            silence_chunks = 0
            continue

        prob = _vad_prob(model, np.frombuffer(chunk, dtype=np.int16))
        if prob >= AMBIENT_VAD_THRESHOLD:
            speech_frames.append(chunk)
            in_speech = True
            silence_chunks = 0
        elif in_speech:
            speech_frames.append(chunk)
            silence_chunks += 1
            if silence_chunks >= silence_pad:
                # Speech segment complete — submit for transcription if long enough.
                if len(speech_frames) >= min_speech_chunks:
                    frames_copy = speech_frames.copy()
                    threading.Thread(
                        target=_submit_ambient_segment,
                        args=(frames_copy,),
                        daemon=True,
                    ).start()
                speech_frames.clear()
                in_speech = False
                silence_chunks = 0
    log.info("Ambient capture thread stopped.")


def _submit_ambient_segment(frames: list[bytes]):
    """Encode captured ambient audio and POST to Jetson for transcription + storage."""
    try:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(frames))
        wav_b64 = base64.b64encode(buf.getvalue()).decode()
        duration_s = len(frames) * CHUNK_SIZE / SAMPLE_RATE
        resp = _api_post(
            "/api/voice/ambient",
            {"audio_base64": wav_b64, "panel_id": PANEL_ID, "duration_seconds": duration_s},
            timeout=30,
        )
        if resp.get("ok"):
            log.debug("Ambient segment transcribed: %r", resp.get("transcript", "")[:80])
    except Exception as exc:
        log.debug("Ambient segment submit failed: %s", exc)


def play_wake_beep() -> None:
    """Play a premium short two-tone wake confirmation chime."""
    if not WAKE_BEEP_ENABLED:
        return
    try:
        # Premium earcon: short up-chirp feel using two tones + tiny pause.
        seg_ms = max(50, WAKE_BEEP_DURATION_MS)
        tones = [(WAKE_BEEP_FREQ_HZ, seg_ms), (0, 28), (int(WAKE_BEEP_FREQ_HZ * 1.33), int(seg_ms * 0.92))]
        amp = max(0.0, min(1.0, WAKE_BEEP_VOLUME))
        frames = bytearray()
        for freq, dur_ms in tones:
            n_frames = max(1, int(SAMPLE_RATE * (dur_ms / 1000.0)))
            for i in range(n_frames):
                if freq == 0:
                    sample = 0
                else:
                    # Gentle envelope to avoid click pops.
                    t = i / max(1, n_frames - 1)
                    env = min(1.0, t * 18.0) * min(1.0, (1.0 - t) * 22.0)
                    sample = int(32767.0 * amp * env * math.sin(2.0 * math.pi * freq * (i / SAMPLE_RATE)))
                # Stereo duplicate (L/R) for USB speakerphones that reject mono playback.
                b = sample.to_bytes(2, byteorder="little", signed=True)
                frames.extend(b)
                frames.extend(b)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            fpath = f.name
        with wave.open(fpath, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(bytes(frames))
        cmd = ["aplay", "-q"]
        if AUDIO_OUTPUT_DEVICE != "default":
            cmd += ["-D", AUDIO_OUTPUT_DEVICE]
        cmd.append(fpath)
        subprocess.run(cmd, check=False)
        os.unlink(fpath)
    except Exception as exc:
        log.warning("Wake beep failed: %s", exc)


def on_wake():
    """Called when wake word is detected."""
    log.info("Wake word detected! Notifying Jetson...")
    play_wake_beep()
    if VOICE_ROUTE_MODE in {"ha_bridge", "hybrid"}:
        resp = _bridge_post("/voice/wake", {"panel_id": PANEL_ID, "source": "satellite_pi"})
    else:
        resp = _api_post("/api/voice/wake", {"panel_id": PANEL_ID})
    ack = resp.get("ack_audio_base64")
    if ack:
        play_audio_b64(ack, resp.get("content_type", "audio/wav"))


def record_command(pa: pyaudio.PyAudio) -> bytes | None:
    """Record audio until silence or max duration."""
    log.info("Recording command (max %ds)...", RECORD_SECONDS)
    kw: dict = dict(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE,
    )
    if _INPUT_DEVICE_INDEX is not None:
        kw["input_device_index"] = _INPUT_DEVICE_INDEX
    stream = None
    for attempt in range(1, 6):
        try:
            stream = pa.open(**kw)
            break
        except OSError as exc:
            # USB speakerphones can report transient busy/unavailable right after wake playback.
            if attempt == 5:
                log.error("Could not open mic stream for command recording: %s", exc)
                return None
            wait_s = 0.15 * attempt
            log.warning("Mic open failed (%s), retrying in %.2fs [%d/5]...", exc, wait_s, attempt)
            time.sleep(wait_s)
    frames = []
    silent_chunks = 0
    max_silent = int(SILENCE_TIMEOUT_S * SAMPLE_RATE / CHUNK_SIZE)
    max_chunks = int(RECORD_SECONDS * SAMPLE_RATE / CHUNK_SIZE)
    for _ in range(max_chunks):
        data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        frames.append(data)
        amplitude = np.abs(np.frombuffer(data, dtype=np.int16)).mean()
        if amplitude < 300:
            silent_chunks += 1
            if silent_chunks >= max_silent and len(frames) > int(0.5 * SAMPLE_RATE / CHUNK_SIZE):
                break
        else:
            silent_chunks = 0
    stream.stop_stream()
    stream.close()
    if len(frames) < int(0.3 * SAMPLE_RATE / CHUNK_SIZE):
        log.info("Command too short, ignoring.")
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def stt_on_jetson(wav_bytes: bytes) -> str:
    """Send WAV to Jetson Whisper endpoint; return transcript."""
    b64 = base64.b64encode(wav_bytes).decode()
    resp = _api_post("/api/voice/transcribe", {"audio_base64": b64, "panel_id": PANEL_ID})
    return str(resp.get("text", "")).strip()


def _is_junk_transcript(text: str) -> bool:
    """Filter STT garbage common on silence or speakerphone TTS bleed."""
    s = text.strip().lower()
    if len(s) < 2:
        return True
    if s in VOICE_IGNORE_TRANSCRIPTS:
        return True
    # Strip trailing punctuation from short single-word hallucinations
    s2 = s.rstrip(".!?…")
    if s2 in VOICE_IGNORE_TRANSCRIPTS:
        return True
    # Repeated short tokens ("you you you", "okay okay ...") are common noise hallucinations.
    toks = [t for t in re.findall(r"[a-z']+", s2) if t]
    if len(toks) >= 4 and len(set(toks)) == 1 and len(toks[0]) <= 5:
        return True
    return False


def _espeak_local(text: str) -> None:
    """Speak a short phrase locally using espeak-ng as an emergency fallback."""
    try:
        subprocess.run(
            ["espeak-ng", "-s", "140", "-p", "44", text],
            check=False,
            timeout=10,
        )
    except FileNotFoundError:
        log.debug("espeak-ng not installed; local TTS fallback unavailable")
    except Exception as exc:
        log.debug("espeak-ng fallback failed: %s", exc)


def _identify_speaker_from_wav(wav_bytes: bytes) -> str | None:
    """Compute resemblyzer embedding and POST to Jetson /api/voice/identify.

    Returns the identified user_id string, or None if identification fails or
    resemblyzer is unavailable.
    """
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
        import io as _io, wave as _wave
        import tempfile as _tmp, os as _os

        # Write wav_bytes to a temp file (resemblyzer needs a file path).
        with _tmp.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            wav_path = f.name

        try:
            encoder = VoiceEncoder()
            wav = preprocess_wav(wav_path)
            embedding = encoder.embed_utterance(wav)
        finally:
            try:
                _os.unlink(wav_path)
            except OSError:
                pass

        import base64 as _b64, numpy as _np
        emb_bytes = embedding.astype(_np.float32).tobytes()
        emb_b64 = _b64.b64encode(emb_bytes).decode()
        resp = _api_post("/api/voice/identify", {"audio_base64": emb_b64}, timeout=10)
        if resp.get("identified"):
            return resp.get("user_id")
        return None
    except ImportError:
        return None
    except Exception as exc:
        log.debug("Speaker ID failed: %s", exc)
        return None


def voice_command(pa: pyaudio.PyAudio, oww) -> None:
    """Record, transcribe, send command, play response; then cool down and reset wake buffers."""
    global _ignore_wake_until
    _recording_active.set()
    try:
        wav = record_command(pa)
        if not wav:
            return
        transcript = stt_on_jetson(wav)
        if not transcript:
            log.info("Empty transcript, skipping.")
            return
        if _is_junk_transcript(transcript):
            log.info("Ignoring junk/hallucination transcript: %r (set VOICE_IGNORE_TRANSCRIPTS to tune)", transcript)
            return
        log.info("Transcript: %r", transcript)

        # Speaker identification (optional — requires resemblyzer on Pi).
        identified_user_id: str | None = None
        try:
            identified_user_id = _identify_speaker_from_wav(wav)
            if identified_user_id:
                log.info("Speaker identified: %s", identified_user_id)
        except Exception:
            pass

        cmd_payload: dict = {"text": transcript, "panel_id": PANEL_ID}
        if identified_user_id:
            cmd_payload["identified_user_id"] = identified_user_id

        ok = False
        if VOICE_ROUTE_MODE == "ha_bridge":
            resp = _bridge_post(
                "/voice/turn",
                {"panel_id": PANEL_ID, "source": "satellite_pi", "transcript": transcript},
            )
            ok = resp.get("ok", False)
        else:
            resp = _api_post("/api/voice/command", cmd_payload)
            ok = resp.get("ok", False)

        if not ok and not resp.get("audio_base64"):
            # Jetson API unreachable or returned an error — speak a local fallback phrase.
            log.warning("Jetson API unavailable — playing local espeak fallback")
            _recording_active.clear()
            _espeak_local("Zoe is not available right now. Please check the connection.")
            return

        audio_b64 = resp.get("audio_base64")
        if audio_b64:
            _recording_active.clear()
            play_audio_b64(audio_b64, resp.get("content_type", "audio/wav"))
        else:
            reply = resp.get("reply") or resp.get("response") or ""
            if reply:
                log.info("Reply (no audio): %s", reply)
        if POST_PLAY_TAIL_S > 0:
            time.sleep(POST_PLAY_TAIL_S)
    finally:
        _recording_active.clear()
        _ignore_wake_until = time.monotonic() + POST_PLAY_COOLDOWN_S
        try:
            oww.reset()
        except Exception as exc:
            log.debug("openWakeWord reset: %s", exc)


_daemon_started_at = time.time()


# Shared Event that orb-tap /activate can set to trigger a wake sequence.
_orb_activate_event = threading.Event()


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps(
                {
                    "status": "ok",
                    "service": "zoe-voice-daemon",
                    "panel_id": PANEL_ID,
                    "uptime_s": int(time.time() - _daemon_started_at),
                    "wake_phrase": os.environ.get("_ZOE_WAKE_PHRASE_LOG", ""),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        """Handle orb-tap activation from the touch UI (POST /activate)."""
        if self.path == "/activate":
            _orb_activate_event.set()
            body = json.dumps({"ok": True, "triggered": "wake"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # noqa: D401
        pass


def _start_health_server():
    try:
        class _ReuseAddrTcp(socketserver.TCPServer):
            allow_reuse_address = True

        srv = _ReuseAddrTcp(("", HEALTH_PORT), _HealthHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        log.info("Health endpoint: http://0.0.0.0:%d/health", HEALTH_PORT)
    except Exception as e:
        log.warning("Could not start health server on port %d: %s", HEALTH_PORT, e)


def main():
    global _INPUT_DEVICE_INDEX, _last_wake_at

    _start_health_server()
    try:
        from openwakeword.model import Model as OWWModel
    except ImportError:
        log.error("openwakeword not installed — run the installer script.")
        sys.exit(1)

    if not DEVICE_TOKEN:
        log.error("DEVICE_TOKEN is empty. Set it in ~/.zoe-voice/.env.voice and restart.")
        sys.exit(1)

    log.info("Loading wake word model...")
    custom_model = os.path.join(os.path.dirname(__file__), "hey_zoe.onnx")
    wake_phrase = "Hey Zoe"
    # Optional Speex NS support: only enable if dependency is available.
    oww_kwargs = {"inference_framework": "onnx"}
    try:
        import speexdsp_ns  # type: ignore  # noqa: F401
        oww_kwargs["enable_speex_noise_suppression"] = True
    except Exception:
        log.info("speexdsp_ns not installed; running wake model without Speex NS")

    if os.path.exists(custom_model):
        oww = OWWModel(
            wakeword_models=[custom_model],
            **oww_kwargs,
        )
        log.info("Loaded custom hey_zoe model from %s", custom_model)
    else:
        oww = OWWModel(
            wakeword_models=["hey_jarvis"],
            **oww_kwargs,
        )
        wake_phrase = "Hey Jarvis"
        log.warning(
            "Custom hey_zoe.onnx not found — using bundled 'hey_jarvis'. "
            "Say clearly: **Hey Jarvis** (not Hey Zoe). Place hey_zoe.onnx in %s to change.",
            os.path.dirname(__file__),
        )
    os.environ["_ZOE_WAKE_PHRASE_LOG"] = wake_phrase

    pa = pyaudio.PyAudio()
    dev_idx: int | None = None
    if AUDIO_DEVICE and AUDIO_DEVICE != "default":
        if AUDIO_DEVICE.isdigit():
            dev_idx = int(AUDIO_DEVICE)
        else:
            hw_match = re.match(r"hw:(\d+)", AUDIO_DEVICE)
            card_num = int(hw_match.group(1)) if hw_match else None
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                name = str(info.get("name", ""))
                if card_num is not None and f"(hw:{card_num}," in name:
                    dev_idx = i
                    break
                if AUDIO_DEVICE in name:
                    dev_idx = i
                    break
            if dev_idx is None:
                log.warning("Could not resolve AUDIO_DEVICE=%s — using default input", AUDIO_DEVICE)

    _INPUT_DEVICE_INDEX = dev_idx
    if dev_idx is not None:
        log.info("Using audio device index %d (%s) for all audio capture", dev_idx, AUDIO_DEVICE)
    else:
        log.info("Using default PyAudio input for all audio capture")

    # ── Shared audio input stream with fan-out queues (A6 fix) ────────────────
    # ONE PyAudio instance, ONE input stream.  Chunks are distributed to
    # wake detection, barge-in VAD, and ambient VAD via thread-safe queues.
    # This avoids opening 3 concurrent input streams on the same ALSA device,
    # which causes IOError -9996 "Invalid input device" on most USB speakerphones.
    global _WAKE_QUEUE, _BARGE_QUEUE, _AMBIENT_QUEUE
    _WAKE_QUEUE = _queue_module.Queue(maxsize=200)
    _BARGE_QUEUE = _queue_module.Queue(maxsize=200)
    _AMBIENT_QUEUE = _queue_module.Queue(maxsize=200)

    if BARGE_IN_ENABLED:
        _barge_thread = threading.Thread(
            target=_barge_in_vad_thread, daemon=True, name="barge-in-vad"
        )
        _barge_thread.start()
        log.info("Barge-in VAD thread started.")
    else:
        log.info("Barge-in disabled (BARGE_IN_ENABLED=false).")

    _ambient_thread = threading.Thread(
        target=_ambient_capture_thread, daemon=True, name="ambient-capture"
    )
    _ambient_thread.start()

    stream_kw: dict = dict(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE,
    )
    if dev_idx is not None:
        stream_kw["input_device_index"] = dev_idx
    stream = pa.open(**stream_kw)

    log.info(
        "Listening on panel=%s | wake phrase: %s | threshold=%.2f | set WAKEWORD_DEBUG=1 for score logging",
        PANEL_ID,
        wake_phrase,
        WAKEWORD_THRESHOLD,
    )
    log.info(
        "Wake beep: enabled=%s freq=%dHz dur=%dms vol=%.2f",
        WAKE_BEEP_ENABLED,
        WAKE_BEEP_FREQ_HZ,
        WAKE_BEEP_DURATION_MS,
        WAKE_BEEP_VOLUME,
    )
    log.info("Audio output device: %s", AUDIO_OUTPUT_DEVICE)

    def _shutdown_handler(sig, frame):
        log.info("Shutdown signal received.")
        _shutdown.set()

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    last_debug = 0.0
    last_near = 0.0
    wake_hits = 0
    wake_first_hit_at = 0.0
    try:
        while not _shutdown.is_set():
            # ── Check for orb-tap activation ────────────────────────────
            if _orb_activate_event.is_set():
                _orb_activate_event.clear()
                now = time.monotonic()
                if (now - _last_wake_at) >= MIN_WAKE_INTERVAL_S:
                    _last_wake_at = now
                    log.info("Orb-tap activation received — triggering wake sequence")
                    stream.stop_stream()
                    stream.close()
                    try:
                        on_wake()
                        voice_command(pa, oww)
                    except Exception as exc:
                        log.error("Orb-tap command pipeline error: %s", exc)
                    finally:
                        time.sleep(0.3)
                        stream = pa.open(**stream_kw)
                    continue

            audio_chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            # OpenWakeWord expects int16 PCM at 16 kHz (not float32 [-1,1] — that yields ~0 scores forever).
            audio_pcm = np.frombuffer(audio_chunk, dtype=np.int16)

            # ── Fan-out: distribute chunk to all consumers ───────────────
            # Non-blocking puts — drop if consumer queue is full (never block main loop).
            try:
                _BARGE_QUEUE.put_nowait(audio_chunk)
            except _queue_module.Full:
                pass
            try:
                _AMBIENT_QUEUE.put_nowait(audio_chunk)
            except _queue_module.Full:
                pass

            now = time.monotonic()
            if now < _ignore_wake_until:
                # Still run predict so streaming feature state stays aligned.
                oww.predict(audio_pcm)
                continue

            prediction = oww.predict(audio_pcm)
            scores = list(prediction.values()) if prediction else []
            mx = max(scores) if scores else 0.0
            if WAKEWORD_DEBUG and (now - last_debug) >= 3.0:
                last_debug = now
                log.info(
                    "wakeword debug: max_score=%.4f threshold=%.4f keys=%s",
                    mx,
                    WAKEWORD_THRESHOLD,
                    list(prediction.keys()) if prediction else [],
                )
            elif (not WAKEWORD_DEBUG) and mx >= 0.18 and mx < WAKEWORD_THRESHOLD and (now - last_near) >= 12.0:
                last_near = now
                log.info(
                    "wakeword near-miss: max_score=%.4f (need %.4f) — lower WAKEWORD_THRESHOLD or say Hey Jarvis more clearly",
                    mx,
                    WAKEWORD_THRESHOLD,
                )

            if scores and mx >= WAKEWORD_THRESHOLD:
                if wake_hits == 0:
                    wake_hits = 1
                    wake_first_hit_at = now
                elif (now - wake_first_hit_at) <= WAKE_CONFIRM_WINDOW_S:
                    wake_hits += 1
                else:
                    wake_hits = 1
                    wake_first_hit_at = now
            else:
                # Expire stale partial confirmations.
                if wake_hits and (now - wake_first_hit_at) > WAKE_CONFIRM_WINDOW_S:
                    wake_hits = 0

            if wake_hits >= WAKE_CONFIRM_COUNT:
                wake_hits = 0
                # Guard against back-to-back re-triggers from echo bursts/noise.
                if (now - _last_wake_at) < MIN_WAKE_INTERVAL_S:
                    continue
                _last_wake_at = now
                # Close the always-on wake stream before command capture.
                # On some USB speakerphones (e.g. Jabra), opening a second input stream
                # while this one is paused causes PyAudio -9985 "Device unavailable".
                stream.stop_stream()
                stream.close()
                try:
                    on_wake()
                    voice_command(pa, oww)
                except Exception as exc:
                    log.error("Command pipeline error: %s", exc)
                finally:
                    time.sleep(0.5)
                    stream = pa.open(**stream_kw)
                    log.info("Listening again (cooldown %.1fs after TTS)...", POST_PLAY_COOLDOWN_S)
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        try:
            pa.terminate()
        except Exception:
            pass
        log.info("Voice daemon stopped.")


if __name__ == "__main__":
    main()
