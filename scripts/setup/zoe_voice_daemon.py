#!/usr/bin/env python3
"""
Zoe voice daemon — runs on Raspberry Pi.
Listens for an openWakeWord model (default: hey_jarvis), then records and sends STT to Jetson.
Place hey_zoe.onnx next to this file to use a custom "Hey Zoe" model instead.
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
    """Decode and play base64 audio via aplay/mpg123 depending on format."""
    if not audio_b64:
        return
    try:
        raw = base64.b64decode(audio_b64)
        ext = "mp3" if "mpeg" in content_type else "wav"
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(raw)
            fpath = f.name
        if ext == "mp3":
            # mpg123 ALSA output device flag: -a <device>
            if AUDIO_OUTPUT_DEVICE != "default":
                cmd = ["mpg123", "-q", "-a", AUDIO_OUTPUT_DEVICE, fpath]
            else:
                cmd = ["mpg123", "-q", fpath]
        else:
            cmd = ["aplay", "-q"]
            if AUDIO_OUTPUT_DEVICE != "default":
                cmd += ["-D", AUDIO_OUTPUT_DEVICE]
            cmd.append(fpath)
        subprocess.run(cmd, check=False)
        os.unlink(fpath)
    except Exception as exc:
        log.warning("Audio playback failed: %s", exc)


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


def voice_command(pa: pyaudio.PyAudio, oww) -> None:
    """Record, transcribe, send command, play response; then cool down and reset wake buffers."""
    global _ignore_wake_until
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
        if VOICE_ROUTE_MODE == "ha_bridge":
            resp = _bridge_post(
                "/voice/turn",
                {"panel_id": PANEL_ID, "source": "satellite_pi", "transcript": transcript},
            )
        else:
            resp = _api_post("/api/voice/command", {"text": transcript, "panel_id": PANEL_ID})
        audio_b64 = resp.get("audio_base64")
        if audio_b64:
            play_audio_b64(audio_b64, resp.get("content_type", "audio/wav"))
        else:
            reply = resp.get("reply") or resp.get("response") or ""
            if reply:
                log.info("Reply (no audio): %s", reply)
        if POST_PLAY_TAIL_S > 0:
            time.sleep(POST_PLAY_TAIL_S)
    finally:
        _ignore_wake_until = time.monotonic() + POST_PLAY_COOLDOWN_S
        try:
            oww.reset()
        except Exception as exc:
            log.debug("openWakeWord reset: %s", exc)


_daemon_started_at = time.time()


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
        log.info("Using audio device index %d (%s) for wake + command recording", dev_idx, AUDIO_DEVICE)
    else:
        log.info("Using default PyAudio input for wake + command recording")

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
            audio_chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            # OpenWakeWord expects int16 PCM at 16 kHz (not float32 [-1,1] — that yields ~0 scores forever).
            audio_pcm = np.frombuffer(audio_chunk, dtype=np.int16)
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
            stream.stop_stream()
            stream.close()
        pa.terminate()
        log.info("Voice daemon stopped.")


if __name__ == "__main__":
    main()
