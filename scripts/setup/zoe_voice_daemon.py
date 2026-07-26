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
from collections import deque
import pyaudio
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [voice-daemon] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _env(name: str, default: str, *legacy_names: str) -> str:
    """Read an environment value, accepting documented legacy aliases."""
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    for legacy_name in legacy_names:
        value = os.environ.get(legacy_name)
        if value not in (None, ""):
            log.warning("Using deprecated env %s; prefer %s", legacy_name, name)
            return value
    return default


def _int_env(name: str, default: int) -> int:
    """Read an int env var, falling back to the default on a missing or malformed
    value instead of crashing the daemon at import (e.g. ZOE_TTS_KEEP_TAIL_MS=off)."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("Env %s=%r is not an integer; using default %d", name, raw, default)
        return default


# Optional persistent log file (e.g. ZOE_VOICE_LOG=/home/zoe/.zoe-voice/voice.log)
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
AUDIO_DEVICE = _env("AUDIO_DEVICE", "default", "MIC_DEVICE_INDEX")
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1280"))
RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS_MAX", "8"))
SILENCE_TIMEOUT_S = float(os.environ.get("SILENCE_TIMEOUT_S", "1.5"))
RECORD_SILENCE_AMPLITUDE = int(os.environ.get("RECORD_SILENCE_AMPLITUDE", "300"))
# ── VAD endpointing: close the turn on Silero speech-absence, not amplitude ──
# Amplitude endpointing waits SILENCE_TIMEOUT_S (1.5s) of raw quiet after every
# utterance — a fixed tail on every single turn. Silero already runs on this Pi
# (barge-in/ambient), so when enabled the recorder stops after
# VAD_ENDPOINT_SILENCE_S of speech-absence once speech has been heard; before
# any speech the longer SILENCE_TIMEOUT_S still applies (slow starters aren't
# cut off). Falls back to amplitude mode automatically if Silero is unavailable.
VAD_ENDPOINT_ENABLED = os.environ.get("VAD_ENDPOINT_ENABLED", "false").lower() in ("1", "true", "yes")
VAD_ENDPOINT_SILENCE_S = float(os.environ.get("VAD_ENDPOINT_SILENCE_S", "0.8"))
VAD_ENDPOINT_THRESHOLD = float(os.environ.get("VAD_ENDPOINT_THRESHOLD", "0.35"))
# Default 0.28 — 0.35 misses many real mics/rooms; tune via WAKEWORD_THRESHOLD.
WAKEWORD_THRESHOLD = float(_env("WAKEWORD_THRESHOLD", "0.28", "OWW_THRESHOLD"))
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")
WAKEWORD_DEBUG = os.environ.get("WAKEWORD_DEBUG", "").lower() in ("1", "true", "yes")
# ── Barge-in: Silero VAD during TTS playback ─────────────────────────────
BARGE_IN_ENABLED = os.environ.get("BARGE_IN_ENABLED", "true").lower() in ("1", "true", "yes")
# Speaker identification via resemblyzer (disable until profiles are enrolled).
SPEAKER_ID_ENABLED = os.environ.get("SPEAKER_ID_ENABLED", "false").lower() in ("1", "true", "yes")
# VAD probability threshold for barge-in detection (0.0-1.0).
BARGE_IN_THRESHOLD = float(os.environ.get("BARGE_IN_THRESHOLD", "0.5"))
# Rolling-window trigger for the playback barge monitor: >= BARGE_MIN_CHUNKS
# speech chunks (80ms each) within the last BARGE_WINDOW_CHUNKS. Real speech
# dips between syllables — a single-chunk trigger is too flaky, sustained
# windows are robust against beeps/echo blips.
# Live-tuned 2026-07-07: Jason's real interrupts scored prob=0.99 with ZERO
# false fires across a 12-turn session, so 2 chunks (~160ms) is safe and snappy.
BARGE_MIN_CHUNKS = int(os.environ.get("BARGE_MIN_CHUNKS", "2"))
BARGE_WINDOW_CHUNKS = int(os.environ.get("BARGE_WINDOW_CHUNKS", "5"))
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
POST_PLAY_COOLDOWN_S = float(os.environ.get("POST_PLAY_COOLDOWN_S", "1.5"))
# Extra settle time after playback before arming wake again (room reverb).
POST_PLAY_TAIL_S = float(os.environ.get("POST_PLAY_TAIL_S", "0.4"))
# ── Follow-up listening: after TTS, wait for speech without requiring wake word ──
FOLLOW_UP_LISTEN_S = float(os.environ.get("FOLLOW_UP_LISTEN_S", "5.0"))
_FOLLOW_UP_MAX_TURNS_RAW = int(os.environ.get("FOLLOW_UP_MAX_TURNS", "5"))
# FOLLOW_UP_MAX_TURNS counts turns AFTER the initial response.
FOLLOW_UP_MAX_TURNS = max(0, _FOLLOW_UP_MAX_TURNS_RAW)
FOLLOW_UP_VAD_THRESHOLD = float(os.environ.get("FOLLOW_UP_VAD_THRESHOLD", "0.35"))

# ── Conversation mode: "hey zoe, let's talk" ─────────────────────────────
# The server's turn_stream fast-path answers an opener phrase with
# {"conversation_mode": true} on the done frame; the daemon then holds an OPEN
# conversation: long no-wake-word listen windows, unlimited-ish turns, until an
# ender phrase ({"conversation_end": true}), sustained silence, or the caps.
CONV_WINDOW_S = float(os.environ.get("CONV_WINDOW_S", "12.0"))
CONV_MAX_TURNS = int(os.environ.get("CONV_MAX_TURNS", "40"))
CONV_MAX_S = float(os.environ.get("CONV_MAX_S", "300"))
CONV_SILENT_WINDOWS = int(os.environ.get("CONV_SILENT_WINDOWS", "2"))
# "first" = beep only when the conversation opens; "every" = each window; "off".
CONV_BEEP = os.environ.get("CONV_BEEP", "first").strip().lower()

# Flags from the LAST turn's done frame (conversation_mode / conversation_end).
# Set by the turn functions, read by voice_command right after the turn —
# avoids changing every bool return path in the turn functions.
_last_turn_flags: dict = {}
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
# Retry transient backend errors so voice turns are less flaky on brief network
# hiccups without masking persistent auth/configuration problems.
VOICE_API_MAX_RETRIES = max(0, int(os.environ.get("VOICE_API_MAX_RETRIES", "2")))
VOICE_API_RETRY_BACKOFF_S = max(0.0, float(os.environ.get("VOICE_API_RETRY_BACKOFF_S", "0.35")))
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

# ── Resemblyzer singleton (cached to avoid ~60s reload per call on Pi) ───────
_voice_encoder = None
_voice_encoder_lock = threading.Lock()


def _get_voice_encoder():
    global _voice_encoder
    if _voice_encoder is not None:
        return _voice_encoder
    with _voice_encoder_lock:
        if _voice_encoder is not None:
            return _voice_encoder
        try:
            from resemblyzer import VoiceEncoder  # type: ignore
            log.info("Loading resemblyzer VoiceEncoder (one-time, ~5s)...")
            _voice_encoder = VoiceEncoder()
            log.info("Resemblyzer VoiceEncoder ready.")
        except ImportError:
            log.debug("resemblyzer not installed; speaker ID disabled")
        except Exception as exc:
            log.warning("VoiceEncoder load failed: %s", exc)
    return _voice_encoder


# ── Recording-active flag (B14) ──────────────────────────────────────────────
# Set to True during the entire wake→record→STT cycle so ambient thread pauses.
_recording_active = threading.Event()

# ── Shared audio fan-out queues (A6) ─────────────────────────────────────────
# Single PyAudio input stream; chunks distributed to consumers via queues.
_WAKE_QUEUE: "queue.Queue[bytes]" = None   # type: ignore  # set in main()
_BARGE_QUEUE: "queue.Queue[bytes]" = None  # type: ignore  # set in main()
_AMBIENT_QUEUE: "queue.Queue[bytes]" = None  # type: ignore  # set in main()
# Pre-roll ring buffer so the wake->record stream-open gap does not clip the
# START of the command (the "what's the" that went missing).
# ~1.6s @1280/16k. The window must reach back from the wake-fire instant (end of
# the wake word + the 2-confirm delay + openWakeWord's own lag) to BEFORE "Hey", or
# the capture opens mid-phrase: at 12 chunks (960ms) it landed inside the wake word
# and real captures began on "Zoe", losing the command onset behind it.
_PREROLL: deque = deque(maxlen=int(os.environ.get("PREROLL_CHUNKS", "20")))
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


_SILERO_WINDOW = 512  # Silero VAD requires exactly 512 samples at 16kHz


def _vad_prob(model, chunk_int16: np.ndarray, sample_rate: int = 16000) -> float:
    """Return max speech probability across 512-sample windows in the chunk."""
    try:
        import torch  # type: ignore
        float32 = chunk_int16.astype(np.float32) / 32768.0
        max_prob = 0.0
        for start in range(0, len(float32) - _SILERO_WINDOW + 1, _SILERO_WINDOW):
            window = float32[start:start + _SILERO_WINDOW]
            tensor = torch.from_numpy(window)
            prob = float(model(tensor, sample_rate).item())
            if prob > max_prob:
                max_prob = prob
        return max_prob
    except Exception:
        return 0.0


class _Endpointer:
    """Decides when a command recording is finished.

    Amplitude mode (legacy): stop after SILENCE_TIMEOUT_S of mean-amplitude
    quiet. VAD mode (VAD_ENDPOINT_ENABLED): stop after VAD_ENDPOINT_SILENCE_S
    of Silero speech-absence once speech has been heard — a much shorter tail
    (0.8s vs 1.5s) that also doesn't stay open on background hum; until speech
    is heard the amplitude-mode timeout still applies so slow starters aren't
    cut off. Falls back to amplitude mode when Silero is unavailable.
    """

    def __init__(self, spoke: bool = False):
        self.mode = "amplitude"
        self._model = None
        if VAD_ENDPOINT_ENABLED:
            model, _ = _get_silero_vad()
            if model is not None:
                self._model = model
                self.mode = "vad"
        self._quiet = 0
        # spoke=True when the caller already confirmed speech (the follow-up
        # recorder's VAD trigger) so the fast tail applies from the first pause.
        self._spoke = spoke
        self._amp_max_silent = int(SILENCE_TIMEOUT_S * SAMPLE_RATE / CHUNK_SIZE)
        self._vad_max_silent = max(1, int(VAD_ENDPOINT_SILENCE_S * SAMPLE_RATE / CHUNK_SIZE))
        self._min_frames = int(0.5 * SAMPLE_RATE / CHUNK_SIZE)

    def push(self, data: bytes, n_frames: int) -> bool:
        """Feed one recorded chunk; True when the recording should stop."""
        if self.mode == "vad":
            prob = _vad_prob(self._model, np.frombuffer(data, dtype=np.int16))
            if prob >= VAD_ENDPOINT_THRESHOLD:
                self._spoke = True
                self._quiet = 0
                return False
            self._quiet += 1
            limit = self._vad_max_silent if self._spoke else self._amp_max_silent
            return self._quiet >= limit and n_frames > self._min_frames
        amplitude = np.abs(np.frombuffer(data, dtype=np.int16)).mean()
        if amplitude >= RECORD_SILENCE_AMPLITUDE:
            self._quiet = 0
            return False
        self._quiet += 1
        return self._quiet >= self._amp_max_silent and n_frames > self._min_frames


def _api_post(path: str, data: dict, timeout: int = 60, retries: int | None = None) -> dict:
    url = f"{ZOE_URL}{path}"
    max_retries = VOICE_API_MAX_RETRIES if retries is None else max(0, int(retries))
    last_error = "unknown"
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(url, json=data, headers=_headers, timeout=timeout, verify=VERIFY_SSL)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            last_error = f"HTTP {status}"
            if status == 401:
                log.error(
                    "API auth failure %s (401). Verify DEVICE_TOKEN for panel=%s and token binding in zoe-data panel_auth.",
                    path,
                    PANEL_ID,
                )
                return {"ok": False, "error": last_error}
            retryable = status in (408, 429, 500, 502, 503, 504)
            if retryable and attempt < max_retries:
                sleep_s = VOICE_API_RETRY_BACKOFF_S * (2 ** attempt)
                log.warning(
                    "API transient HTTP %s on %s, retrying in %.2fs (%d/%d)",
                    status,
                    path,
                    sleep_s,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(sleep_s)
                continue
            log.error("API error %s: HTTP %s", path, status)
            return {"ok": False, "error": last_error}
        except requests.exceptions.SSLError:
            log.warning("SSL error — set VERIFY_SSL=false if using self-signed cert")
            raise
        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            if attempt < max_retries:
                sleep_s = VOICE_API_RETRY_BACKOFF_S * (2 ** attempt)
                log.warning(
                    "API transport error on %s: %s (retry in %.2fs %d/%d)",
                    path,
                    exc,
                    sleep_s,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(sleep_s)
                continue
            log.error("API error %s: %s", path, exc)
            return {"ok": False, "error": last_error}
        except Exception as exc:
            last_error = str(exc)
            log.error("API error %s: %s", path, exc)
            return {"ok": False, "error": last_error}
    return {"ok": False, "error": last_error}


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


class _BargeMonitor:
    """Dedicated mic reader for barge-in DURING TTS playback.

    The always-on wake stream is CLOSED for the whole command cycle (the Jabra
    cannot hold two input streams — PyAudio -9985), so the queue-fed barge
    thread hears nothing exactly when barge-in matters. This monitor opens its
    own short-lived stream while Zoe is speaking (no other input stream is open
    then) and sets _barge_in_requested on sustained speech: >= BARGE_MIN_CHUNKS
    speech chunks within the last BARGE_WINDOW_CHUNKS (~240ms in ~480ms).
    """

    def __init__(self, pa: "pyaudio.PyAudio"):
        self._pa = pa
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not BARGE_IN_ENABLED or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="barge-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
        self._thread = None

    def _run(self) -> None:
        model, _ = _get_silero_vad()
        if model is None:
            return
        kw: dict = dict(
            format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
            input=True, frames_per_buffer=CHUNK_SIZE,
        )
        if _INPUT_DEVICE_INDEX is not None:
            kw["input_device_index"] = _INPUT_DEVICE_INDEX
        try:
            stream = self._pa.open(**kw)
        except OSError as exc:
            log.debug("Barge monitor mic open failed: %s", exc)
            return
        log.debug("Barge monitor listening (th=%.2f, %d/%d chunks)",
                  BARGE_IN_THRESHOLD, BARGE_MIN_CHUNKS, BARGE_WINDOW_CHUNKS)
        window: deque = deque(maxlen=max(1, BARGE_WINDOW_CHUNKS))
        try:
            while not self._stop.is_set() and not _shutdown.is_set():
                try:
                    chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                except Exception:
                    break
                prob = _vad_prob(model, np.frombuffer(chunk, dtype=np.int16))
                window.append(prob >= BARGE_IN_THRESHOLD)
                if sum(window) >= max(1, BARGE_MIN_CHUNKS):
                    with _tts_process_lock:
                        proc = _tts_process
                    if proc is None or proc.poll() is not None:
                        # Nothing is playing — this is the user STILL TALKING
                        # (e.g. the endpointer closed on a long pause), not an
                        # interruption of Zoe. Setting the flag now would abort
                        # a reply that hasn't even started (live 22:28:10: user
                        # spoke 189ms after a 7.84s recording closed, prob=0.99,
                        # turn died with no audio ever played). Keep watching —
                        # the window ages out in ~400ms, so only speech that
                        # actually overlaps playback fires.
                        continue
                    log.info("Barge-in detected during playback (monitor, prob=%.2f)", prob)
                    _barge_in_requested.set()
                    # Kill the TTS subprocess DIRECTLY — the stream loop only
                    # polls the flag at network-chunk boundaries, which can be
                    # seconds away while the brain generates the next sentence.
                    try:
                        proc.terminate()
                        log.info("Barge-in: TTS playback terminated immediately.")
                    except Exception as exc:
                        log.debug("Barge-in terminate failed: %s", exc)
                    break
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass


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


def _notify_wake_background():
    """Fire-and-forget: tell Jetson about the wake event (UI update only)."""
    try:
        if VOICE_ROUTE_MODE in {"ha_bridge", "hybrid"}:
            _bridge_post("/voice/wake", {"panel_id": PANEL_ID, "source": "satellite_pi"}, timeout=3)
        else:
            _api_post("/api/voice/wake", {"panel_id": PANEL_ID}, timeout=3, retries=0)
    except Exception as exc:
        log.debug("Background wake notify failed (non-critical): %s", exc)


def _wake_panel_agent() -> None:
    """Fire-and-forget POST to the local panel agent so the screen wakes up."""
    try:
        requests.post(
            "http://127.0.0.1:8765/wake",
            json={"hold_s": 20},
            timeout=1.0,
        )
    except Exception as exc:
        log.debug("panel-agent wake failed: %s", exc)


def on_wake():
    """Called when wake word is detected.

    Everything here is fire-and-forget: the command is already being recorded from
    the still-open wake stream, so any blocking work (the chime spawns aplay and
    opens the ALSA device — hundreds of ms) would just delay capture. The chime now
    overlaps the start of the recording; a quiet ~260ms two-tone is far cheaper than
    deleting the words the user is saying. Set WAKE_BEEP_ENABLED=false to drop it.
    """
    log.info("Wake word detected! Notifying Jetson and waking screen...")
    threading.Thread(target=play_wake_beep, daemon=True, name="wake-beep").start()
    threading.Thread(target=_wake_panel_agent, daemon=True, name="wake-screen").start()
    threading.Thread(target=_notify_wake_background, daemon=True, name="wake-notify").start()


def play_follow_up_beep() -> None:
    """Play a soft single-tone beep to signal follow-up listening is active."""
    try:
        freq = int(WAKE_BEEP_FREQ_HZ * 1.5)
        dur_ms = max(30, WAKE_BEEP_DURATION_MS // 2)
        amp = max(0.0, min(1.0, WAKE_BEEP_VOLUME * 0.6))
        n_frames = max(1, int(SAMPLE_RATE * (dur_ms / 1000.0)))
        frames = bytearray()
        for i in range(n_frames):
            t = i / max(1, n_frames - 1)
            env = min(1.0, t * 20.0) * min(1.0, (1.0 - t) * 20.0)
            sample = int(32767.0 * amp * env * math.sin(2.0 * math.pi * freq * (i / SAMPLE_RATE)))
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
        subprocess.run(cmd, check=False, timeout=3)
        os.unlink(fpath)
    except Exception as exc:
        log.debug("Follow-up beep failed: %s", exc)


def record_command(pa: pyaudio.PyAudio, stream=None) -> bytes | None:
    """Record audio until silence or max duration.

    When ``stream`` is given, record from that ALREADY-OPEN mic stream (the
    always-on wake stream) and leave closing it to the caller — the no-dead-air
    path. Closing the wake stream, playing the chime and opening a fresh stream
    took several hundred ms, and every word spoken in that window was silently
    dropped: "Hey Zoe, what's my name?" reached STT as "My name.". Recording
    straight from the open stream keeps the capture contiguous with the pre-roll,
    so a command spoken in one breath with the wake word survives intact.

    ``stream=None`` keeps the open-my-own-stream behaviour used by the follow-up
    windows, which run after the wake stream is closed for the turn.
    """
    log.info("Recording command (max %ds)...", RECORD_SECONDS)
    owns_stream = stream is None
    if owns_stream:
        kw: dict = dict(
            format=pyaudio.paInt16,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        if _INPUT_DEVICE_INDEX is not None:
            kw["input_device_index"] = _INPUT_DEVICE_INDEX
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
    frames = list(_PREROLL)  # prepend pre-roll so the start of speech is kept
    endpointer = _Endpointer()
    stop_reason = "max_duration"
    max_chunks = int(RECORD_SECONDS * SAMPLE_RATE / CHUNK_SIZE)
    for _ in range(max_chunks):
        data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        frames.append(data)
        if endpointer.push(data, len(frames)):
            stop_reason = "silence"
            break
    if owns_stream:
        stream.stop_stream()
        stream.close()
    duration_s = len(frames) * CHUNK_SIZE / float(SAMPLE_RATE)
    log.info(
        "Recorded command: duration=%.2fs chunks=%d stop=%s endpoint=%s silence_timeout=%.2fs",
        duration_s, len(frames), stop_reason, endpointer.mode, SILENCE_TIMEOUT_S,
    )
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


# ── Speaker-ID profile cache (local matching) ────────────────────────────────
# The Jetson stays storage+policy only: this daemon pulls consented profile
# embeddings from GET /api/voice/profiles/sync, cosine-matches locally, and
# sends only a {voice_user_id, voice_score} CLAIM per turn — the server applies
# its own threshold (a panel can claim, never decide). Cache persists to disk
# (0600 — it holds biometric embeddings) so a server outage doesn't blind us.
_PROFILE_CACHE_PATH = os.path.expanduser("~/.zoe-voice/speaker_profiles.json")
_PROFILE_SYNC_TTL_S = float(os.environ.get("SPEAKER_ID_SYNC_TTL_S", "3600"))
_profile_cache: dict = {"fetched_at": 0.0, "profiles": [], "syncing": False}
_profile_cache_lock = threading.Lock()


def _load_profile_cache_from_disk() -> None:
    try:
        with open(_PROFILE_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("profiles"), list):
            with _profile_cache_lock:
                # fetched_at stays 0 so the first sync still refreshes.
                _profile_cache["profiles"] = data["profiles"]
            log.info("Speaker profiles loaded from disk: %d", len(data["profiles"]))
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.debug("speaker profile cache load failed: %s", exc)


def _sync_speaker_profiles(force: bool = False) -> None:
    """Refresh the local profile cache from the server (TTL-gated).

    The `syncing` flag makes TTL expiry single-flight: when many concurrent
    turns cross the TTL together, only the first fetches — the rest keep
    matching against the (still valid) cached profiles.
    """
    with _profile_cache_lock:
        fresh = (time.time() - _profile_cache["fetched_at"]) < _PROFILE_SYNC_TTL_S
        if (fresh and not force) or _profile_cache["syncing"]:
            return
        _profile_cache["syncing"] = True
    try:
        r = requests.get(
            f"{ZOE_URL}/api/voice/profiles/sync",
            headers=_headers, timeout=10, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        data = r.json()
        profiles = data.get("profiles") or []
        with _profile_cache_lock:
            _profile_cache["fetched_at"] = time.time()
            _profile_cache["profiles"] = profiles
        try:
            os.makedirs(os.path.dirname(_PROFILE_CACHE_PATH), mode=0o700, exist_ok=True)
            fd = os.open(_PROFILE_CACHE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"profiles": profiles}, f)
        except Exception as exc:
            log.debug("speaker profile cache persist failed: %s", exc)
        log.info("Speaker profiles synced: %d", len(profiles))
    except Exception as exc:
        with _profile_cache_lock:
            cached_count = len(_profile_cache["profiles"])
        log.debug("speaker profile sync failed (keeping cached %d): %s",
                  cached_count, exc)
    finally:
        with _profile_cache_lock:
            _profile_cache["syncing"] = False


def _match_speaker_local(embedding) -> tuple[str, float] | None:
    """Cosine-match an embedding against the cached profiles.

    Returns (user_id, score) for the best match, or None when no profiles are
    cached. The ACCEPTANCE decision is the server's — we always send the best
    claim with its raw score.
    """
    import base64 as _b64
    import numpy as _np

    with _profile_cache_lock:
        profiles = list(_profile_cache["profiles"])
    if not profiles:
        return None
    best_user, best_score = None, -1.0
    for p in profiles:
        uid = p.get("user_id")
        if not uid:
            continue
        try:
            ref = _np.frombuffer(_b64.b64decode(p["embedding_base64"]), dtype=_np.float32)
            if ref.shape != _np.shape(embedding):
                continue  # model-version mismatch — skip this row, keep the rest
            denom = float(_np.linalg.norm(embedding) * _np.linalg.norm(ref))
            if denom <= 0:
                continue
            score = float(_np.dot(embedding, ref) / denom)
        except Exception:
            continue  # one bad row must never cost the whole turn's speaker ID
        if score > best_score:
            best_user, best_score = uid, score
    if best_user is None:
        return None
    return best_user, best_score


def _identify_speaker_from_wav(wav_bytes: bytes) -> tuple[str, float] | None:
    """Compute a resemblyzer embedding and identify the speaker.

    Matches locally against the synced profile cache (preferred — keeps the
    Jetson model-free) and falls back to POST /api/voice/identify when no
    profiles are cached (older server / first run). Returns (user_id, score);
    the remote fallback reports the server's confidence. None when speaker ID
    is disabled, resemblyzer is unavailable, or nothing matched.
    """
    if not SPEAKER_ID_ENABLED:
        return None
    encoder = _get_voice_encoder()
    if encoder is None:
        return None
    try:
        from resemblyzer import preprocess_wav  # type: ignore
        import tempfile as _tmp, os as _os, base64 as _b64, numpy as _np

        with _tmp.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            wav_path = f.name
        try:
            wav = preprocess_wav(wav_path)
            embedding = encoder.embed_utterance(wav)
        finally:
            try:
                _os.unlink(wav_path)
            except OSError:
                pass

        _sync_speaker_profiles()
        local = _match_speaker_local(embedding)
        if local is not None:
            return local

        emb_bytes = embedding.astype(_np.float32).tobytes()
        emb_b64 = _b64.b64encode(emb_bytes).decode()
        resp = _api_post("/api/voice/identify", {"embedding_base64": emb_b64}, timeout=5)
        if resp.get("identified"):
            uid = resp.get("user_id")
            if not uid:
                return None  # legacy server echoed identified without a user
            return uid, float(resp.get("confidence") or 1.0)
        return None
    except ImportError:
        return None
    except Exception as exc:
        log.debug("Speaker ID failed: %s", exc)
        return None


_BUFFER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buffers")
_BUFFER_ENABLED = os.environ.get("ZOE_BUFFER_PHRASES", "1").strip().lower() not in {"0", "false", "no", "off"}
# Only speak a buffer phrase if the answer hasn't come back within this many
# seconds — so fast/cached turns (~0.5s) play the answer directly with no filler
# and no added latency, and only genuinely-slow turns get a "thinking" phrase.
_BUFFER_DELAY_S = float(os.environ.get("ZOE_BUFFER_DELAY_S", "0.8"))


def _play_buffer_phrase():
    """Play a random pre-rendered 'thinking' phrase (non-blocking) to fill the
    gap while the server computes the answer. Returns the Popen (or None).

    These WAVs are pre-synthesised in Zoe's af_sky voice and staged in
    ~/.zoe-voice/buffers/ — playback is local + instant, no round-trip."""
    if not _BUFFER_ENABLED:
        return None
    try:
        import glob
        import random
        files = glob.glob(os.path.join(_BUFFER_DIR, "buf_*.wav"))
        if not files:
            return None
        chosen = random.choice(files)
        cmd = ["aplay", "-q"]
        if AUDIO_OUTPUT_DEVICE != "default":
            cmd += ["-D", AUDIO_OUTPUT_DEVICE]
        cmd.append(chosen)
        log.info("Buffer phrase playing: %s", os.path.basename(chosen))
        return subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
    except Exception as exc:
        log.debug("buffer phrase skipped: %s", exc)
        return None


# ── Streaming turn: play TTS sentence chunks the instant they arrive ──────────
# When enabled, the panel hits /api/voice/turn_stream and plays each sentence as
# Kokoro finishes it (first audio ~1.3s) instead of waiting for the whole reply
# to synthesize. This makes the "thinking" buffer phrase unnecessary on most
# turns. Any failure falls back to the blocking _do_single_turn.
VOICE_STREAM_ENABLED = os.environ.get("ZOE_VOICE_STREAM", "1").strip().lower() in ("1", "true", "yes", "on")


def _pcm_from_wav(wav_bytes: bytes):
    """Return (pcm_frames, rate, channels, sampwidth) from one WAV chunk."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.readframes(wf.getnframes()), wf.getframerate(), wf.getnchannels(), wf.getsampwidth()


# The reply is streamed one sentence per TTS chunk, and Kokoro bakes ~0.4-0.5s of
# silence onto the front AND back of every utterance. Concatenated back-to-back that
# is ~0.9s of dead air at every sentence boundary — the reply plays "in pieces". Trim
# each chunk's leading/trailing near-silence before feeding aplay, keeping a short tail
# so sentences don't slur together. Set ZOE_TTS_TRIM_SILENCE=false to disable.
_TTS_TRIM_SILENCE = os.environ.get("ZOE_TTS_TRIM_SILENCE", "true").lower() in ("1", "true", "yes")
_TTS_KEEP_TAIL_MS = _int_env("ZOE_TTS_KEEP_TAIL_MS", 130)
_TTS_LEAD_GUARD_MS = _int_env("ZOE_TTS_LEAD_GUARD_MS", 20)


def _trim_chunk_silence(pcm: bytes, rate: int, ch: int, width: int) -> bytes:
    """Trim baked-in leading/trailing silence from one 16-bit PCM chunk, keeping a
    short natural tail. Non-16-bit or all-silent chunks pass through untouched, so a
    mis-detection can never drop real speech."""
    if not _TTS_TRIM_SILENCE or width != 2 or not pcm:
        return pcm
    try:
        a = np.frombuffer(pcm, dtype=np.int16)
        frames = a.reshape(-1, ch) if ch > 1 else a
        env = np.abs(frames).max(axis=1) if ch > 1 else np.abs(a)
        if env.size == 0:
            return pcm
        peak = int(env.max())
        if peak == 0:
            return pcm
        thr = max(int(peak * 0.02), 96)  # 2% of this chunk's peak, with a floor
        loud = np.where(env > thr)[0]
        if loud.size == 0:
            return pcm  # no clear speech detected — leave the chunk intact
        lead_guard = int(rate * _TTS_LEAD_GUARD_MS / 1000)
        keep_tail = int(rate * _TTS_KEEP_TAIL_MS / 1000)
        start = max(0, int(loud[0]) - lead_guard)
        end = min(env.size, int(loud[-1]) + 1 + keep_tail)
        trimmed = frames[start:end]
        return (trimmed.reshape(-1) if ch > 1 else trimmed).astype(np.int16).tobytes()
    except Exception as exc:
        log.debug("silence trim failed (%s) — feeding chunk untrimmed", exc)
        return pcm


def _feed_pcm_chunk(aplay, wav_bytes: bytes):
    """Strip the WAV header and stream raw PCM into a single persistent aplay so
    sentence chunks play gaplessly. Starts aplay on the first chunk (format taken
    from that chunk's header) and registers it as the active TTS process so the
    barge-in thread can stop it."""
    global _tts_process
    try:
        pcm, rate, ch, width = _pcm_from_wav(wav_bytes)
    except Exception as exc:
        log.debug("bad wav chunk: %s", exc)
        return aplay
    pcm = _trim_chunk_silence(pcm, rate, ch, width)
    if aplay is None:
        fmt = {1: "U8", 2: "S16_LE", 3: "S24_3LE", 4: "S32_LE"}.get(width, "S16_LE")
        cmd = ["aplay", "-q", "-t", "raw", "-f", fmt, "-c", str(ch), "-r", str(rate)]
        if AUDIO_OUTPUT_DEVICE != "default":
            cmd += ["-D", AUDIO_OUTPUT_DEVICE]
        aplay = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        with _tts_process_lock:
            _tts_process = aplay
    try:
        if aplay.stdin:
            aplay.stdin.write(pcm)
            aplay.stdin.flush()
    except (BrokenPipeError, ValueError, OSError):
        pass
    return aplay


def _do_single_turn_stream(pa: pyaudio.PyAudio, wav: bytes, *, prompt_on_empty: bool = True, conversation: bool = False) -> bool:
    """Streaming turn: POST audio to /api/voice/turn_stream and play each TTS
    sentence chunk as it arrives (no buffer phrase — first audio ~1.3s). Falls
    back to the blocking _do_single_turn on any error."""
    global _tts_process
    import time as _time

    audio_b64_wav = base64.b64encode(wav).decode()
    voice_claim: tuple[str, float] | None = None
    try:
        voice_claim = _identify_speaker_from_wav(wav)
        if voice_claim:
            log.info("Speaker claim: %s (%.3f)", voice_claim[0], voice_claim[1])
    except Exception:
        pass
    _last_turn_flags.clear()
    payload: dict = {"audio_base64": audio_b64_wav, "panel_id": PANEL_ID}
    if conversation:
        # Tell the server we're inside an open conversation so ender phrases
        # ("that's all", "goodbye") are honoured; outside one they never fire.
        payload["conversation"] = True
    if voice_claim:
        # A claim + raw score; the server applies its own threshold.
        payload["voice_user_id"], payload["voice_score"] = voice_claim

    url = f"{ZOE_URL}/api/voice/turn_stream"
    _barge_in_requested.clear()
    t0 = _time.monotonic()
    aplay = None
    ttfa = None
    transcript = ""
    reply = ""
    played_any = False
    expect_audio = False

    try:
        r = requests.post(url, json=payload, headers=_headers, timeout=60, stream=True, verify=VERIFY_SSL)
        r.raise_for_status()
        for raw_line in r.iter_lines(decode_unicode=False):
            if _barge_in_requested.is_set():
                log.info("Barge-in during streamed reply.")
                break
            if not raw_line:
                continue
            if expect_audio:
                expect_audio = False
                try:
                    wav_bytes = base64.b64decode(raw_line)
                except Exception:
                    continue
                if not played_any:
                    _recording_active.clear()
                aplay = _feed_pcm_chunk(aplay, wav_bytes)
                if ttfa is None:
                    ttfa = _time.monotonic() - t0
                played_any = True
                continue
            try:
                obj = json.loads(raw_line.decode("utf-8", "ignore"))
            except Exception:
                continue
            if obj.get("error"):
                log.warning("turn_stream server error: %s", obj["error"])
                break
            if "full_audio" in obj:
                # Skybridge/confirmation path: voice_command returned one full
                # audio blob (wav or mp3). Play it via the robust player.
                if not played_any:
                    _recording_active.clear()
                if ttfa is None:
                    ttfa = _time.monotonic() - t0
                play_audio_b64(str(obj.get("full_audio") or ""), obj.get("content_type") or "audio/wav")
                played_any = True
                reply = obj.get("reply", "") or reply
                continue
            if obj.get("done"):
                reply = obj.get("reply", "") or reply
                for _k in ("conversation_mode", "conversation_end"):
                    if obj.get(_k):
                        _last_turn_flags[_k] = True
                break
            if "transcript" in obj and "chunk" not in obj:
                transcript = obj.get("transcript", "") or transcript
                continue
            if "chunk" in obj:
                expect_audio = True
                continue
    except requests.exceptions.SSLError:
        raise
    except Exception as exc:
        # If we've already started speaking, re-running the blocking turn would
        # double-play (user hears a fragment, then the whole reply again). Only
        # fall back when nothing has played yet; otherwise let the partial reply
        # stand and finish draining what's queued.
        if played_any:
            log.warning("turn_stream failed mid-reply (%s) — keeping partial, no re-play", exc)
            if aplay is not None:
                try:
                    if aplay.stdin:
                        aplay.stdin.close()
                except Exception:
                    pass
                while aplay.poll() is None:
                    if _barge_in_requested.is_set():
                        try:
                            aplay.terminate()
                        except Exception:
                            pass
                        break
                    time.sleep(0.05)
                with _tts_process_lock:
                    _tts_process = None
            return True
        if aplay is not None:
            try:
                aplay.kill()
            except Exception:
                pass
            with _tts_process_lock:
                _tts_process = None
        # If the server already sent the transcript, it PROCESSED this turn
        # (including any write — add to list, create event). Re-POSTing via the
        # blocking turn would execute it a SECOND time (the duplicate-writes bug).
        # Only re-POST when we got nothing back (connection died before any reply).
        if transcript:
            log.warning("turn_stream failed after server processed it (%s) — NOT re-POSTing (avoid duplicate write)", exc)
            return False
        log.warning("turn_stream failed with no server response (%s) — falling back to blocking turn", exc)
        return _do_single_turn(pa, wav, prompt_on_empty=prompt_on_empty)

    # Drain playback (respecting barge-in) once the stream ends.
    if aplay is not None:
        try:
            if aplay.stdin:
                aplay.stdin.close()
        except Exception:
            pass
        while aplay.poll() is None:
            if _barge_in_requested.is_set():
                try:
                    aplay.terminate()
                except Exception:
                    pass
                break
            _time.sleep(0.05)
        with _tts_process_lock:
            _tts_process = None

    if transcript and _is_junk_transcript(transcript):
        log.info("Ignoring junk/hallucination transcript: %r", transcript)
        return False
    if played_any:
        log.info("turn_stream TTFA=%.2fs transcript=%r", ttfa if ttfa is not None else -1.0, transcript[:80])
        return True
    # Stream produced no audio.
    if not transcript:
        if prompt_on_empty:
            log.info("Empty transcript with no audio — retry chime.")
            _recording_active.clear()
            play_follow_up_beep()
        return False
    # Transcript present ⇒ the server PROCESSED this turn, writes included
    # (add-to-list, create-event). Re-POSTing the same wav via the blocking
    # turn executes it a SECOND time — live 2026-07-07: every barge-aborted
    # add landed twice ~1.5-2.5s apart (the duplicate-writes bug). Same rule
    # the exception path above already applies: never re-POST a processed turn.
    if _barge_in_requested.is_set():
        # User cut in before the first audio chunk — they don't want this
        # reply. Open the follow-up window for what they're saying instead.
        log.info("turn_stream: barged before first audio — not re-POSTing (reply=%r).", reply[:80])
        return True
    log.warning("turn_stream: transcript but no audio (reply=%r) — NOT re-POSTing (avoid duplicate write).", reply[:80])
    if prompt_on_empty:
        _recording_active.clear()
        play_follow_up_beep()
    return False


def _do_single_turn(pa: pyaudio.PyAudio, wav: bytes, *, prompt_on_empty: bool = True, conversation: bool = False) -> bool:
    """Process one recorded WAV: combined STT+LLM+TTS via /api/voice/turn.

    Returns True if audio was played (eligible for follow-up listening).
    Uses a single HTTP round-trip instead of separate transcribe + command calls.
    """
    import time as _time
    _t_pi_start = _time.monotonic()

    audio_b64_wav = base64.b64encode(wav).decode()
    _t_encode = _time.monotonic() - _t_pi_start

    voice_claim: tuple[str, float] | None = None
    _t_vid_start = _time.monotonic()
    try:
        voice_claim = _identify_speaker_from_wav(wav)
        if voice_claim:
            log.info("Speaker claim: %s (%.3f)", voice_claim[0], voice_claim[1])
    except Exception:
        pass
    _t_vid = _time.monotonic() - _t_vid_start

    turn_payload: dict = {"audio_base64": audio_b64_wav, "panel_id": PANEL_ID}
    if voice_claim:
        # A claim + raw score; the server applies its own threshold.
        turn_payload["voice_user_id"], turn_payload["voice_score"] = voice_claim

    # Run the turn in a thread so we can play a buffer phrase ONLY when the
    # answer is actually slow. Fast/cached turns (~0.5s) return before the delay
    # and play the answer directly — no filler, no added latency.
    _turn_result: dict = {}

    def _run_turn():
        if VOICE_ROUTE_MODE == "ha_bridge":
            _turn_result["resp"] = _bridge_post(
                "/voice/turn",
                {"panel_id": PANEL_ID, "source": "satellite_pi", "audio_base64": audio_b64_wav},
            )
        else:
            _turn_result["resp"] = _api_post("/api/voice/turn", turn_payload)

    _t_post_start = _time.monotonic()
    _turn_thread = threading.Thread(target=_run_turn, daemon=True)
    _turn_thread.start()
    _turn_thread.join(timeout=_BUFFER_DELAY_S)

    _buffer_proc = None
    if _turn_thread.is_alive():
        # Answer is taking a while → fill the gap with a "thinking" phrase.
        _buffer_proc = _play_buffer_phrase()
        _turn_thread.join()

    resp = _turn_result.get("resp", {}) or {}
    ok = resp.get("ok", False)
    _t_server = _time.monotonic() - _t_post_start
    _t_total = _time.monotonic() - _t_pi_start
    log.info(
        "voice/turn Pi-side timing: encode=%.3fs vid=%.3fs server_rtt=%.3fs total=%.3fs buffered=%s",
        _t_encode, _t_vid, _t_server, _t_total, _buffer_proc is not None,
    )

    # If a buffer phrase is playing, let it finish before the reply so they
    # don't overlap (it's ~1.5s and the turn already took >0.8s, so usually done).
    if _buffer_proc is not None:
        try:
            _buffer_proc.wait(timeout=4)
        except Exception:
            try:
                _buffer_proc.terminate()
            except Exception:
                pass

    if not ok and not resp.get("audio_base64"):
        log.warning("Jetson voice turn failed (%s) — playing local espeak fallback", resp.get("error", "unknown_error"))
        _recording_active.clear()
        _espeak_local("Zoe is not available right now. Please check the connection.")
        return False

    transcript = resp.get("text", "")
    if transcript:
        if _is_junk_transcript(transcript):
            log.info("Ignoring junk/hallucination transcript: %r", transcript)
            return False
        log.info("Transcript: %r", transcript)
    elif not resp.get("audio_base64"):
        # Avoid robot fallback speech on no-transcript turns; use a short earcon
        # and return to wake mode instead.
        if prompt_on_empty:
            log.info("Empty transcript with no audio response — playing retry chime.")
            _recording_active.clear()
            play_follow_up_beep()
        else:
            log.info("Empty follow-up transcript with no audio response — returning to wake mode.")
        return False

    audio_b64 = resp.get("audio_base64")
    if audio_b64:
        _recording_active.clear()
        play_audio_b64(audio_b64, resp.get("content_type", "audio/wav"))
        return True
    else:
        reply = resp.get("reply") or resp.get("response") or ""
        if reply:
            log.info("Reply (no audio): %s", reply)
        return False


def _follow_up_listen(pa: pyaudio.PyAudio, window_s: float | None = None) -> bytes | None:
    """Listen for speech without wake word for `window_s` (default
    FOLLOW_UP_LISTEN_S) seconds.

    Uses Silero VAD on a fresh mic stream. If speech is detected, continues
    recording on the SAME stream (no gap where words get lost) and returns
    the WAV bytes. Returns None if silence throughout the window.
    """
    model, _ = _get_silero_vad()
    if model is None:
        return None

    kw: dict = dict(
        format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
        input=True, frames_per_buffer=CHUNK_SIZE,
    )
    if _INPUT_DEVICE_INDEX is not None:
        kw["input_device_index"] = _INPUT_DEVICE_INDEX

    try:
        stream = pa.open(**kw)
    except OSError as exc:
        log.debug("Follow-up mic open failed: %s", exc)
        return None

    # Drain a few chunks to skip any residual beep/echo from the follow-up chime.
    drain_chunks = max(1, int(0.15 * SAMPLE_RATE / CHUNK_SIZE))
    for _ in range(drain_chunks):
        try:
            stream.read(CHUNK_SIZE, exception_on_overflow=False)
        except Exception:
            break

    deadline = time.monotonic() + (window_s if window_s is not None else FOLLOW_UP_LISTEN_S)
    speech_detected = False
    # Keep a short ring of the chunks scanned BEFORE VAD fires. Silero VAD only
    # crosses threshold a chunk or two into speech, so without this lookback the
    # first syllable of a follow-up ("That's" → "My") is discarded — the onset
    # clip that makes even Moonshine v2 Medium mishear. ~320ms by default.
    lookback: deque = deque(maxlen=int(os.environ.get("FOLLOWUP_LOOKBACK_CHUNKS", "4")))
    max_prob_seen = 0.0
    chunk_count = 0
    try:
        while time.monotonic() < deadline:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            lookback.append(data)  # retain pre-trigger audio so VAD latency can't clip the onset
            prob = _vad_prob(model, np.frombuffer(data, dtype=np.int16))
            chunk_count += 1
            if prob > max_prob_seen:
                max_prob_seen = prob
            if prob >= FOLLOW_UP_VAD_THRESHOLD:
                speech_detected = True
                log.info("Follow-up speech detected (VAD=%.2f), recording...", prob)
                break

        if not speech_detected:
            log.info("Follow-up VAD: no speech in %d chunks, max_prob=%.3f threshold=%.2f",
                     chunk_count, max_prob_seen, FOLLOW_UP_VAD_THRESHOLD)
            stream.stop_stream()
            stream.close()
            return None

        # Continue recording on the same stream — no mic close/reopen gap. Seed with
        # the lookback ring so the recording includes the pre-VAD onset (and the
        # trigger chunk, which is the last entry in the ring).
        log.info("Recording follow-up command (max %ds)...", RECORD_SECONDS)
        frames = list(lookback)
        # The trigger chunk was already-detected speech — seed the endpointer so
        # the fast VAD tail applies from the first pause.
        endpointer = _Endpointer(spoke=True)
        stop_reason = "max_duration"
        max_chunks = int(RECORD_SECONDS * SAMPLE_RATE / CHUNK_SIZE)
        for _ in range(max_chunks):
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            frames.append(data)
            if endpointer.push(data, len(frames)):
                stop_reason = "silence"
                break
        stream.stop_stream()
        stream.close()
        duration_s = len(frames) * CHUNK_SIZE / float(SAMPLE_RATE)
        log.info(
            "Recorded follow-up: duration=%.2fs chunks=%d stop=%s endpoint=%s silence_timeout=%.2fs",
            duration_s, len(frames), stop_reason, endpointer.mode, SILENCE_TIMEOUT_S,
        )

        if len(frames) < int(0.3 * SAMPLE_RATE / CHUNK_SIZE):
            log.info("Follow-up too short, ignoring.")
            return None

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    except Exception as exc:
        log.warning("Follow-up listen error: %s", exc)
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        return None


def voice_command(pa: pyaudio.PyAudio, oww, wake_stream=None) -> None:
    """Record, transcribe, send command, play response, then follow-up listen.

    ``wake_stream`` is the still-open always-on mic stream. The command is recorded
    from it so no audio is lost between wake and capture; it is then closed here,
    before the turn runs, because the Jabra rejects a second input stream and
    _BargeMonitor needs the device for the duration of playback.
    """
    global _ignore_wake_until
    _recording_active.set()
    follow_ups_done = 0
    try:
        try:
            wav = record_command(pa, stream=wake_stream)
        finally:
            # Hand the mic device back before anything else opens it.
            if wake_stream is not None:
                try:
                    wake_stream.stop_stream()
                    wake_stream.close()
                except Exception as exc:
                    log.debug("wake stream close failed (non-fatal): %s", exc)
        if not wav:
            return

        _turn_fn = _do_single_turn_stream if VOICE_STREAM_ENABLED else _do_single_turn
        # Barge monitor: the wake stream is closed for this whole cycle, so
        # nothing else can hear the room while Zoe thinks/speaks. Run a
        # dedicated mic stream for the duration of the turn (record streams are
        # closed by now; follow-up windows open AFTER we stop it).
        _monitor = _BargeMonitor(pa)
        _monitor.start()
        try:
            played_audio = _turn_fn(pa, wav)
        finally:
            _monitor.stop()

        # ── Conversation mode ("hey zoe, let's talk") ──
        # The server's opener fast-path marks the done frame with
        # conversation_mode; hold an open conversation: long no-wake-word
        # windows, many turns, until an ender / sustained silence / the caps.
        if _last_turn_flags.get("conversation_mode"):
            log.info("Conversation mode OPEN (window=%.0fs, max %ds/%d turns)",
                     CONV_WINDOW_S, int(CONV_MAX_S), CONV_MAX_TURNS)
            conv_deadline = time.monotonic() + CONV_MAX_S
            conv_turns = 0
            silent_windows = 0
            conv_beeped = False
            while (time.monotonic() < conv_deadline
                   and conv_turns < CONV_MAX_TURNS
                   and silent_windows < CONV_SILENT_WINDOWS):
                threading.Thread(target=_notify_wake_background, daemon=True,
                                 name="conv-notify").start()
                if CONV_BEEP == "every" or (CONV_BEEP == "first" and not conv_beeped):
                    play_follow_up_beep()
                    conv_beeped = True
                _recording_active.set()
                conv_wav = _follow_up_listen(pa, window_s=CONV_WINDOW_S)
                if conv_wav is None:
                    silent_windows += 1
                    log.info("Conversation: silent window %d/%d",
                             silent_windows, CONV_SILENT_WINDOWS)
                    continue
                silent_windows = 0
                _monitor = _BargeMonitor(pa)
                _monitor.start()
                try:
                    _turn_fn(pa, conv_wav, prompt_on_empty=False, conversation=True)
                finally:
                    _monitor.stop()
                conv_turns += 1
                if _last_turn_flags.get("conversation_end"):
                    log.info("Conversation CLOSED by ender after %d turns", conv_turns)
                    break
            else:
                log.info("Conversation CLOSED (%s) after %d turns",
                         "silence" if silent_windows >= CONV_SILENT_WINDOWS else "cap",
                         conv_turns)
            return  # conversation supersedes the regular follow-up loop

        if FOLLOW_UP_LISTEN_S > 0 and FOLLOW_UP_MAX_TURNS <= 0:
            log.info("Follow-up disabled by config (FOLLOW_UP_MAX_TURNS=%d).", _FOLLOW_UP_MAX_TURNS_RAW)

        while played_audio and FOLLOW_UP_LISTEN_S > 0 and follow_ups_done < FOLLOW_UP_MAX_TURNS:
            # Re-arm the UI orb to "listening" right before follow-up capture opens.
            threading.Thread(target=_notify_wake_background, daemon=True, name="followup-notify").start()
            play_follow_up_beep()
            log.info("Follow-up listening (turn %d/%d, %.1fs window)...", follow_ups_done + 1, FOLLOW_UP_MAX_TURNS, FOLLOW_UP_LISTEN_S)
            _recording_active.set()
            follow_wav = _follow_up_listen(pa)
            if follow_wav is None:
                log.info("No follow-up speech detected, returning to wake mode.")
                break
            # Follow-up misses should fall back silently to wake mode, not speak a
            # robotic retry prompt after a successful prior answer.
            _monitor = _BargeMonitor(pa)
            _monitor.start()
            try:
                played_audio = _turn_fn(pa, follow_wav, prompt_on_empty=False)
            finally:
                _monitor.stop()
            follow_ups_done += 1

        if POST_PLAY_TAIL_S > 0:
            time.sleep(POST_PLAY_TAIL_S)
    finally:
        _recording_active.clear()
        # NEVER SHORTEN an active wake guard (Greptile, PR #1423): an orb-tap
        # voice_command that completes while _speak_announcement's playback is
        # still pumping would otherwise cut the announcement's 600s guard down
        # to 1.5s — re-arming wake mid-announcement and inviting the echo
        # false-wake loop these cooldowns exist to prevent. max() keeps the
        # later deadline; the announcement's own finally releases it when
        # playback actually ends.
        _ignore_wake_until = max(_ignore_wake_until, time.monotonic() + POST_PLAY_COOLDOWN_S)
        try:
            oww.reset()
        except Exception as exc:
            log.debug("openWakeWord reset: %s", exc)


# ── Server-pushed spoken announcements (P-W2.3) ─────────────────────────────
# The daemon is the household SPEAKER: proactive spoken deliveries (the morning
# brief) are enqueued server-side and claimed here via GET /api/voice/announcements
# (device-token auth — the same DEVICE_TOKEN/_headers every other call uses).
# The kiosk browser was never a real audio path (guest session → /speak 401'd
# silently); this poll lane plays announces through the SAME playback machinery
# as replies, so barge-in, echo-suppression cooldown, and wake re-arm behave
# identically. Decision logic lives in zoe_voice_announce.py (same dir, pure
# stdlib) so it is unit-testable off-Pi; deploy both files together.
ANNOUNCE_POLL_ENABLED = os.environ.get("ZOE_ANNOUNCE_POLL_ENABLED", "true").lower() in ("1", "true", "yes")
ANNOUNCE_POLL_S = float(os.environ.get("ZOE_ANNOUNCE_POLL_S", "5.0"))
# While an announce is playing, hold wake detection closed (same reason as the
# post-reply cooldown: the speakerphone hears its own TTS). Ceiling only —
# playback normally ends well before this and resets the guard to the normal
# POST_PLAY_COOLDOWN_S.
_ANNOUNCE_WAKE_GUARD_MAX_S = 600.0

try:
    import zoe_voice_announce as _announce_logic
except ImportError:
    _announce_logic = None  # partial deploy: polling disabled, never a crash


def _daemon_busy() -> bool:
    """True while speaking an announce would overlap live voice activity:
    a wake→record→STT cycle, a reply's TTS playback, or the post-play cooldown
    (the tail of a turn, where follow-up windows may still open)."""
    if _recording_active.is_set():
        return True
    with _tts_process_lock:
        if _tts_process is not None and _tts_process.poll() is None:
            return True
    return time.monotonic() < _ignore_wake_until


def _fetch_announcements() -> list[dict]:
    """Claim pending announcements (device-token auth). Raises on transport
    failure so the poller's backoff sees it — a restarting zoe-data (every
    deploy) must quiet the poll, not crash the daemon."""
    r = requests.get(
        f"{ZOE_URL}/api/voice/announcements",
        headers=_headers,
        timeout=10,
        verify=VERIFY_SSL,
    )
    r.raise_for_status()
    data = r.json()
    return list(data.get("announcements") or [])


def _speak_announcement(ann: dict) -> bool:
    """Synthesize + play one claimed announcement through the reply path.

    /api/voice/speak (device token) → play_audio_b64: the barge-in VAD thread
    watches _tts_process exactly as it does for replies, so the user can talk
    over an announce to stop it; wake stays suppressed while it plays and the
    normal post-play cooldown re-arms it afterwards.
    """
    global _ignore_wake_until
    text = str(ann.get("text") or "").strip()
    if not text:
        return False
    resp = _api_post("/api/voice/speak", {"text": text[:1200], "panel_id": PANEL_ID}, timeout=30)
    audio_b64 = resp.get("audio_base64")
    if not audio_b64:
        log.warning("announce %s: TTS fetch failed (%s)", ann.get("id", "?"), resp.get("error", "no audio"))
        return False
    log.info("announce %s: speaking (%d chars, trigger=%s)",
             ann.get("id", "?"), len(text), ann.get("trigger_type", ""))
    _recording_active.set()  # pause ambient capture, as during a reply
    _ignore_wake_until = time.monotonic() + _ANNOUNCE_WAKE_GUARD_MAX_S
    try:
        play_audio_b64(audio_b64, resp.get("content_type", "audio/wav"))
    finally:
        _recording_active.clear()
        _ignore_wake_until = time.monotonic() + POST_PLAY_COOLDOWN_S
    return True


def _announce_poll_thread():
    """Background thread: poll/claim/speak server announcements (P-W2.3)."""
    if not ANNOUNCE_POLL_ENABLED:
        log.info("Announce polling disabled (ZOE_ANNOUNCE_POLL_ENABLED=false)")
        return
    if _announce_logic is None:
        log.warning(
            "zoe_voice_announce.py not found next to the daemon — server-pushed "
            "announcements will NOT be spoken. Deploy it to the same directory."
        )
        return
    poller = _announce_logic.AnnouncePoller(
        fetch=_fetch_announcements,
        speak=_speak_announcement,
        is_busy=_daemon_busy,
        poll_interval_s=ANNOUNCE_POLL_S,
        logger=log,
    )
    log.info("Announce poll thread started (interval=%.1fs)", ANNOUNCE_POLL_S)
    poller.run(_shutdown.wait)
    log.info("Announce poll thread stopped.")


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
                # Only consider devices that actually have input channels.
                # Some hw: entries appear as output-only even when the same
                # physical device supports capture (e.g. Jabra Speak 750).
                if int(info.get("maxInputChannels", 0)) == 0:
                    continue
                name = str(info.get("name", ""))
                if card_num is not None and f"(hw:{card_num}," in name:
                    dev_idx = i
                    break
                if AUDIO_DEVICE in name:
                    dev_idx = i
                    break
            if dev_idx is None:
                log.warning(
                    "Could not resolve AUDIO_DEVICE=%s to an input-capable device — using default input",
                    AUDIO_DEVICE,
                )

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

    # Pre-warm resemblyzer encoder in background so first command isn't delayed,
    # and pull the speaker-profile cache (disk copy first so a server outage
    # doesn't blind local matching, then a fresh sync).
    if SPEAKER_ID_ENABLED:
        threading.Thread(target=_get_voice_encoder, daemon=True, name="resemblyzer-warmup").start()

        def _profile_warmup() -> None:
            _load_profile_cache_from_disk()
            _sync_speaker_profiles(force=True)

        threading.Thread(target=_profile_warmup, daemon=True, name="speaker-profile-sync").start()

    _ambient_thread = threading.Thread(
        target=_ambient_capture_thread, daemon=True, name="ambient-capture"
    )
    _ambient_thread.start()

    # P-W2.3: server-pushed spoken announcements (morning brief etc.).
    threading.Thread(
        target=_announce_poll_thread, daemon=True, name="announce-poll"
    ).start()

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
        "Follow-up config: listen=%.1fs max_turns=%d (raw=%d) vad_threshold=%.2f",
        FOLLOW_UP_LISTEN_S,
        FOLLOW_UP_MAX_TURNS,
        _FOLLOW_UP_MAX_TURNS_RAW,
        FOLLOW_UP_VAD_THRESHOLD,
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
            _PREROLL.append(audio_chunk)
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
                    "wakeword near-miss: max_score=%.4f (need %.4f) — lower WAKEWORD_THRESHOLD or say %s more clearly",
                    mx,
                    WAKEWORD_THRESHOLD,
                    os.environ.get("_ZOE_WAKE_PHRASE_LOG", "the wake phrase"),
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
                # Keep the wake stream OPEN and record the command straight from it.
                # Closing it here (then chiming, then opening a fresh stream) left a
                # several-hundred-ms hole in which the user was already speaking, so
                # the front of the command was deleted before STT ever saw it.
                # voice_command() closes this stream once the command is captured —
                # before _BargeMonitor opens the device, since some USB speakerphones
                # (e.g. Jabra) reject a second input stream with -9985.
                try:
                    on_wake()
                    voice_command(pa, oww, wake_stream=stream)
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
