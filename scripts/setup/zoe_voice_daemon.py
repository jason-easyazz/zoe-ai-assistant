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
  - Speaker ID: resemblyzer embeddings identify the speaker (W5 shadow mode, the
    default, scores on a background thread so the upload never waits for it)
"""
from __future__ import annotations

import base64
import io
import itertools
import uuid
import json
import logging
import math
import os
import platform
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


def _float_env(name: str, default: float) -> float:
    """Float twin of _int_env: a malformed or non-finite value (nan, inf) logs
    and keeps the default — float() accepts "nan", and every comparison against
    NaN is False, which silently disables whatever the value gates."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        log.warning("Env %s=%r is not a finite number; using default %s", name, raw, default)
        return default
    return value


def _prob_env(name: str, default: float, *, allow_zero: bool = True) -> float:
    """A probability knob: finite and within [0, 1] ((0, 1] when ``allow_zero`` is
    False). Anything else logs a WARNING naming the value and keeps the default —
    e.g. BARGE_FAST_PROB=1.5 or nan would silently switch the fast path off."""
    value = _float_env(name, default)
    if not ((0.0 <= value if allow_zero else 0.0 < value) and value <= 1.0):
        log.warning("Env %s=%r is outside %s0, 1]; using default %s", name,
                    os.environ.get(name), "[" if allow_zero else "(", default)
        return default
    return value


def _count_env(name: str, default: int, *, minimum: int) -> int:
    """An int knob with a floor (a count or a duration). Malformed or below
    ``minimum`` logs a WARNING naming the value and keeps the default."""
    value = _int_env(name, default)
    if value < minimum:
        log.warning("Env %s=%r is below %d; using default %d", name,
                    os.environ.get(name), minimum, default)
        return default
    return value


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
# 12s, was 8: 2 of 10 real panel turns on 2026-09-28 hit the 8s cap mid-sentence
# (stop=max_duration) and were answered from a truncated transcript. STT costs
# ~0.25s per second of clip, so only turns that are genuinely long pay for it.
RECORD_SECONDS = _count_env("RECORD_SECONDS_MAX", 12, minimum=1)
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
# ── Deep-quiet fast tail: cut the endpoint wait when silence is unambiguous ──
# The 800ms VAD tail is the largest controllable block in the panel latency
# budget, but corpus measurement (2026-07-27, 889 utterances) showed a plain
# shorter tail is not safe: ~17% of real commands contain a mid-utterance
# VAD-quiet pause >= 480ms, so lowering the single tail cuts people off
# mid-sentence. The same measurement found a usable signal: true end-of-turn
# silence scores DEEP on Silero (p90 prob median 0.062) while mid-utterance
# pauses hover higher (median 0.179 — breath, mouth noise, trailing voicing).
# So when ZOE_VAD_TAIL_MS > 0, the endpointer closes after that many ms of
# consecutive DEEP quiet (prob < ZOE_VAD_TAIL_DEEP_PROB); pauses that are
# merely below the speech threshold still get the full VAD_ENDPOINT_SILENCE_S.
# Measured on the corpus: at the same tail length the deep gate halves the
# false-cut risk vs a fixed reduction (480ms: 8.3% vs 17.4%; 640ms: 3.6% vs
# 8.3% — upper bounds, the corpus mixes in non-panel lanes whose endpointing
# tolerates longer pauses). 0 (the default) disables the fast tail entirely:
# behaviour is byte-identical to before the flag existed, so deploying this
# code is a no-op until the operator stages the flag on the Pi.
# _int_env, not int(): a malformed value (ZOE_VAD_TAIL_MS=off) must fall back
# to disabled, never crash the daemon at import and take the panel's voice down.
ZOE_VAD_TAIL_MS = _int_env("ZOE_VAD_TAIL_MS", 0)
# ── Adaptive tail: a CLEAN stop closes sooner, a HESITATION waits longer ──
# Both flag-dark (0 = off, behaviour unchanged). Measured 2026-09-28 on the live
# 640/800 panel config: speech end -> recording closed is 720-880ms, because
# Silero decays through the ambiguous band for 1-2 chunks before the 640ms of
# deep quiet starts counting (docs/knowledge/voice-pipeline.md -> Panel per-turn
# dead time).
#  * ZOE_VAD_CLEAN_TAIL_MS: when the quiet since the last speech chunk is a
#    CLEAN fall — at most ZOE_VAD_CLEAN_FALL_CHUNKS ambiguous decay chunks, then
#    nothing but deep quiet — close once the whole quiet run reaches this many ms,
#    provided the turn has at least ZOE_VAD_CLEAN_MIN_SPEECH_MS of speech. Never
#    below _CLEAN_TAIL_FLOOR_MS. It cannot tell a finished sentence from a clean
#    mid-sentence pause, so it trades a cut risk for speed: on 246 corpus turns
#    560ms ended 124 earlier (median 160ms) and cut 5 mid-sentence (2%).
#  * ZOE_VAD_HESITATION_TAIL_MS: when the quiet run went deep and then came back
#    up into the ambiguous band (breath, "um", trailing voicing), the any-quiet
#    limit becomes this (capped at _HESITATION_TAIL_CAP_MS) instead of
#    VAD_ENDPOINT_SILENCE_S. A long deep run still closes on the deep tail.
_CLEAN_TAIL_FLOOR_MS = 500
_HESITATION_TAIL_CAP_MS = 1500
ZOE_VAD_CLEAN_TAIL_MS = _int_env("ZOE_VAD_CLEAN_TAIL_MS", 0)
ZOE_VAD_CLEAN_FALL_CHUNKS = max(0, _int_env("ZOE_VAD_CLEAN_FALL_CHUNKS", 2))
ZOE_VAD_CLEAN_MIN_SPEECH_MS = max(0, _int_env("ZOE_VAD_CLEAN_MIN_SPEECH_MS", 480))
ZOE_VAD_HESITATION_TAIL_MS = _int_env("ZOE_VAD_HESITATION_TAIL_MS", 0)
# ── B1.1 speculative turn-start (flag-dark, default OFF) ─────────────────
# With ZOE_SPECULATIVE_TURN on, the recorder fires the turn at its FIRST
# end-of-turn verdict — ZOE_SPECULATIVE_TAIL_MS of consecutive DEEP quiet after
# confirmed speech — and keeps recording. The server holds everything audible
# until this daemon sends the verdict (commit / resolve / cancel) after the
# real endpoint closes. Needs VAD mode and the streaming turn; off (the
# default) the endpointer never fires and the payload never carries the
# fields — byte-identical to today. Design + protocol:
# docs/architecture/b1-speculative-turn-start.md
ZOE_SPECULATIVE_TURN = os.environ.get("ZOE_SPECULATIVE_TURN", "false").lower() in ("1", "true", "yes", "on")
ZOE_SPECULATIVE_TAIL_MS = _int_env("ZOE_SPECULATIVE_TAIL_MS", 320)
# Process-wide fail-closed latch: set the moment a speculative stream proves
# the server is NOT gating (audio arrived before this daemon sent its verdict —
# daemon flag on, server flag off, e.g. a server rollback). No further turn
# speculates until the daemon restarts; the ERROR log names the cause.
_speculation_disabled = threading.Event()
_speculation_warned: set = set()


def _speculation_available() -> bool:
    return ZOE_SPECULATIVE_TURN and VOICE_STREAM_ENABLED and not _speculation_disabled.is_set()


def _reset_speculation_warnings() -> None:
    _speculation_warned.clear()
try:
    # The default lives INSIDE environ.get so the flag-inventory scanner records
    # it ("or 0.10" outside the call reads as no-default in the committed table).
    ZOE_VAD_TAIL_DEEP_PROB = float(os.environ.get("ZOE_VAD_TAIL_DEEP_PROB", "0.10") or "0.10")
except ValueError:
    ZOE_VAD_TAIL_DEEP_PROB = 0.10
# Clamp: the whole safety design is deep < ambiguous < speech. A value at or
# above VAD_ENDPOINT_THRESHOLD makes EVERY quiet chunk "deep" (borderline
# pauses take the fast exit — the exact cut-people-off failure the gate
# prevents), and NaN/inf comparisons are silently False. Misconfiguration
# degrades to the safe default, never to a sharper knife.
if not (0.0 < ZOE_VAD_TAIL_DEEP_PROB < VAD_ENDPOINT_THRESHOLD):
    # The fallback itself must satisfy deep < speech-threshold: with a speech
    # threshold configured below 0.10, a bare 0.10 fallback would make ALL
    # post-speech quiet "deep" (Bugbot). Half the threshold keeps the invariant
    # at any configuration.
    _fallback = min(0.10, VAD_ENDPOINT_THRESHOLD / 2)
    log.warning("ZOE_VAD_TAIL_DEEP_PROB=%r outside (0, %s); using %s",
                ZOE_VAD_TAIL_DEEP_PROB, VAD_ENDPOINT_THRESHOLD, _fallback)
    ZOE_VAD_TAIL_DEEP_PROB = _fallback
# Default 0.28 — 0.35 misses many real mics/rooms; tune via WAKEWORD_THRESHOLD.
WAKEWORD_THRESHOLD = float(_env("WAKEWORD_THRESHOLD", "0.28", "OWW_THRESHOLD"))
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")


def _silence_insecure_request_warnings(verify: bool) -> bool:
    """Say ONCE that TLS verification is off, instead of once per request.

    With VERIFY_SSL=false (the panel's self-signed Jetson cert) every
    ``requests`` call raises urllib3's InsecureRequestWarning, and urllib3
    registers SecurityWarning as "always" - so the 5 s announce poll alone wrote
    ~17,000 two-line tracebacks a day to stderr/journald (3,343 in the 4h45m
    reviewed on 2026-10-04: 82 % of everything the unit logged), burying the
    real lines. The operator's choice is respected - verification stays off -
    and one INFO line states it. Returns True when the warning was silenced.
    """
    if verify:
        return False
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception as exc:  # a missing/odd urllib3 must never cost the daemon
        log.debug("could not silence InsecureRequestWarning: %s", exc)
        return False
    log.info("TLS certificate verification is OFF (VERIFY_SSL=false) - "
             "the per-request InsecureRequestWarning is silenced; this is the one notice")
    return True


_TLS_WARNINGS_SILENCED = _silence_insecure_request_warnings(VERIFY_SSL)
WAKEWORD_DEBUG = os.environ.get("WAKEWORD_DEBUG", "").lower() in ("1", "true", "yes")
# ── Barge-in: Silero VAD during TTS playback ─────────────────────────────
BARGE_IN_ENABLED = os.environ.get("BARGE_IN_ENABLED", "true").lower() in ("1", "true", "yes")
# Speaker identification via resemblyzer (disable until profiles are enrolled).
SPEAKER_ID_ENABLED = os.environ.get("SPEAKER_ID_ENABLED", "false").lower() in ("1", "true", "yes")
# W5 shadow mode (default ON): identify + LOG each turn's speaker claim, but
# never attach it to the turn payload — the server must not act on identity
# until the shadow week's false-accept/false-reject numbers are reviewed with
# the operator (docs/architecture/samantha-evolution-plan.md §W5,
# docs/architecture/panel-identity-plan.md ops-enable). Metrics rows hold
# {boot, seq, ts, panel_id, user_id, score, n_profiles, source, truth} — metadata ONLY, no audio, no embeddings
# (docs/knowledge/biometric-retention-policy.md).
# Parsed as a default-ON SAFETY gate, not an ordinary feature flag: only an
# EXPLICIT false-y value lifts it. The usual `in ("1","true","yes")` shape would
# read an EMPTY `SPEAKER_ID_SHADOW=` in .env.voice as off — so a typo or a
# half-edited line would silently start attaching voice_user_id/voice_score to
# live turns with no shadow metrics, which is precisely the ungated state the W5
# gate exists to prevent. Unset, empty and unparseable all stay ON.
SPEAKER_ID_SHADOW = os.environ.get("SPEAKER_ID_SHADOW", "").strip().lower() not in (
    "0", "false", "no", "off",
)
SPEAKER_ID_SHADOW_LOG = os.environ.get(
    "SPEAKER_ID_SHADOW_LOG",
    os.path.expanduser("~/.zoe-voice/speaker_shadow_metrics.jsonl"),
)
# VAD probability threshold for barge-in detection, in (0, 1]; 0 would call
# every chunk speech. The live panel overrides it (0.75) in .env.voice.
BARGE_IN_THRESHOLD = _prob_env("BARGE_IN_THRESHOLD", 0.5, allow_zero=False)
# Barge-in decision (_BargeDetector), anchored to the moment playback STARTS.
# Self-interruption 2026-09-28 (docs/knowledge/incident-runbook.md): the old
# trigger (2 of 5 chunks, window live since the monitor opened at turn START)
# fired 0.43-0.81s after the first write in three consecutive replies with a
# quiet room — Zoe's own onset reaching the mic before the Jabra's echo
# canceller settles — and once at t+11ms on the user's own speech from BEFORE
# playback began. Guards:
#  1. only audio captured AFTER playback starts counts: the rolling window is
#     cleared and any buffered mic backlog discarded when playback begins;
#  2. the first BARGE_GRACE_MS after the first write is ignored outright
#     (aplay + the Pulse sink add ~70-90ms before the first sound, so 800ms
#     covers ~0.7s of audible onset — past all three measured false fires);
#  3. sustained speech: >= BARGE_MIN_CHUNKS speech chunks (80ms each) within
#     the last BARGE_WINDOW_CHUNKS (~240ms of speech in ~480ms) ...
#  4. ... or the fast path: BARGE_FAST_CHUNKS CONSECUTIVE chunks scoring
#     >= BARGE_FAST_PROB (a loud, unambiguous interruption, ~160ms).
# Every knob is validated at import: out-of-range values log a WARNING and keep
# the default, never a silently disabled guard. 0 is meaningful for the grace
# (none) and the fast path (off); the window counts must be >= 1.
BARGE_MIN_CHUNKS = _count_env("BARGE_MIN_CHUNKS", 3, minimum=1)
BARGE_WINDOW_CHUNKS = _count_env("BARGE_WINDOW_CHUNKS", 6, minimum=1)
BARGE_GRACE_MS = _count_env("BARGE_GRACE_MS", 800, minimum=0)
BARGE_FAST_PROB = _prob_env("BARGE_FAST_PROB", 0.95)
BARGE_FAST_CHUNKS = _count_env("BARGE_FAST_CHUNKS", 2, minimum=0)  # 0 disables the fast path
# ── Barge-in phase 1: duck → decide → resume (flag-dark) ──────────────────
# docs/research/barge-in-duck-decide-resume-2026-10-04.md §4.1, mechanics in
# docs/knowledge/voice-pipeline.md → "Panel barge-in". OFF = today's hard stop,
# byte for byte (pinned by tests/unit/test_voice_daemon_barge_in.py). ON: the
# detector's fire ducks the player's PulseAudio sink-input (never the sink),
# then _BargeDecider commits (sustained speech), resumes (quiet) or hits the
# ceiling (resume) on the same Silero probabilities; the VAD-failure sentinel
# can never commit. BARGE_SEED_NEXT_TURN is only read when the duck is on.
BARGE_DUCK_ENABLED = os.environ.get("BARGE_DUCK_ENABLED", "false").lower() in ("1", "true", "yes")
BARGE_DUCK_DB = _float_env("BARGE_DUCK_DB", -15.0)
if not (-60.0 <= BARGE_DUCK_DB < 0.0):  # a duck is a negative, bounded gain change
    log.warning("Env BARGE_DUCK_DB=%r is outside [-60, 0); using default -15",
                os.environ.get("BARGE_DUCK_DB"))
    BARGE_DUCK_DB = -15.0
BARGE_DUCK_RAMP_MS = _count_env("BARGE_DUCK_RAMP_MS", 0, minimum=0)  # 0 = single step
BARGE_COMMIT_SPEECH_MS = _count_env("BARGE_COMMIT_SPEECH_MS", 900, minimum=1)
BARGE_RESUME_SILENCE_MS = _count_env("BARGE_RESUME_SILENCE_MS", 400, minimum=1)
BARGE_DECIDE_MAX_MS = _count_env("BARGE_DECIDE_MAX_MS", 2000, minimum=1)
BARGE_PLAYOUT_LATENCY_MS = _count_env("BARGE_PLAYOUT_LATENCY_MS", 100, minimum=0)
BARGE_SEED_NEXT_TURN = os.environ.get("BARGE_SEED_NEXT_TURN", "true").lower() in ("1", "true", "yes")
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


# ── Platform backend: PANEL_PLATFORM=pi|mac|auto (default pi) ────────────────
# Everything the daemon asks of the OS - the player process, the duck, the local
# TTS fallback, the on-box panel agent - goes through ONE object so the same
# daemon can run as a "virtual panel" on a Mac (docs/knowledge/mac-virtual-panel.md).
# `pi` is the live panel's behaviour, command for command: _PiBackend builds the
# exact aplay / mpg123 / pactl / espeak-ng argv the call sites used to inline
# (pinned byte for byte by tests/unit/test_voice_daemon_platform.py). `mac` loads
# scripts/setup/mac_panel/mac_backend.py BY PATH, only when asked for - the Pi
# never imports it, so deploy-pi-voice.sh ships nothing new.
def _resolve_panel_platform(raw: "str | None", system: str) -> str:
    """PANEL_PLATFORM -> "pi" | "mac". Unset/empty/unknown is "pi" (an unknown
    value is logged, never guessed); "auto" follows platform.system()."""
    value = (raw or "").strip().lower()
    if value == "mac":
        return "mac"
    if value == "auto":
        return "mac" if system == "Darwin" else "pi"
    if value not in ("", "pi"):
        log.warning("Env PANEL_PLATFORM=%r is not pi|mac|auto; using pi", raw)
    elif value == "" and system == "Darwin":
        log.warning("Running on macOS with PANEL_PLATFORM unset: the Pi backend needs "
                    "aplay/pactl and will not play anything. Set PANEL_PLATFORM=mac.")
    return "pi"


class _PiBackend:
    """The live panel: ALSA aplay/mpg123, PulseAudio sink-input duck, espeak-ng."""

    name = "pi"
    has_panel_agent = True      # the on-box agent on 127.0.0.1:8765 (screen wake)
    default_health_bind = ""    # every interface, as the Pi has always done

    @staticmethod
    def _aplay_cmd(fpath: str) -> list:
        cmd = ["aplay", "-q"]
        if AUDIO_OUTPUT_DEVICE != "default":
            cmd += ["-D", AUDIO_OUTPUT_DEVICE]
        cmd.append(fpath)
        return cmd

    def start_file_player(self, fpath: str, ext: str):
        """Start playing a file without blocking: (Popen-like, player name)."""
        if ext == "mp3":
            if AUDIO_OUTPUT_DEVICE != "default":
                cmd = ["mpg123", "-q", "-a", AUDIO_OUTPUT_DEVICE, fpath]
            else:
                cmd = ["mpg123", "-q", fpath]
        else:
            cmd = self._aplay_cmd(fpath)
        return subprocess.Popen(cmd), cmd[0]

    def play_file_blocking(self, fpath: str, timeout: "float | None" = None) -> None:
        """Play a short WAV (the chimes) and wait for it."""
        cmd = self._aplay_cmd(fpath)
        if timeout is None:
            subprocess.run(cmd, check=False)
        else:
            subprocess.run(cmd, check=False, timeout=timeout)

    def start_buffer_player(self, fpath: str):
        return subprocess.Popen(self._aplay_cmd(fpath), stderr=subprocess.DEVNULL)

    def start_pcm_stream(self, rate: int, ch: int, width: int):
        """One persistent player fed raw PCM on stdin (gapless sentence chunks)."""
        fmt = {1: "U8", 2: "S16_LE", 3: "S24_3LE", 4: "S32_LE"}.get(width, "S16_LE")
        cmd = ["aplay", "-q", "-t", "raw", "-f", fmt, "-c", str(ch), "-r", str(rate)]
        if AUDIO_OUTPUT_DEVICE != "default":
            cmd += ["-D", AUDIO_OUTPUT_DEVICE]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def local_tts_cmd(self, text: str) -> list:
        return ["espeak-ng", "-s", "140", "-p", "44", text]

    def wakeword_framework(self) -> str:
        return "onnx"

    def make_ducker(self, proc):
        return _SinkInputDucker(proc.pid)


def _make_platform_backend(name: str):
    if name != "mac":
        return _PiBackend()
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mac_panel", "mac_backend.py")
    if not os.path.isfile(path):
        raise RuntimeError(f"PANEL_PLATFORM=mac needs {path} (run from a full repo checkout)")
    spec = importlib.util.spec_from_file_location("zoe_mac_panel_backend", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MacBackend(
        pyaudio_module=pyaudio,
        output_device=AUDIO_OUTPUT_DEVICE,
        duck_params=lambda: (BARGE_DUCK_DB, BARGE_DUCK_RAMP_MS),
        log=log,
    )


PANEL_PLATFORM = _resolve_panel_platform(os.environ.get("PANEL_PLATFORM"), platform.system())
_PLATFORM = _make_platform_backend(PANEL_PLATFORM)
# The health server also serves an unauthenticated POST /activate (it starts a
# recording). Bound to every interface on the Pi's home LAN it is accepted; the
# Mac backend defaults to loopback, because a laptop leaves the house.
HEALTH_BIND = os.environ.get("HEALTH_BIND", _PLATFORM.default_health_bind)
if PANEL_PLATFORM != "pi":
    log.info("Platform backend: %s", PANEL_PLATFORM)
# After a voice cycle or an announcement, ignore wake scores for this long.
# It guards the WAKE detector (not barge-in): the speakerphone hears Zoe's own
# TTS, and the 1.5s it used to be dates from the Whisper era, when echo of a
# reply re-woke the panel and was transcribed as "yes" in a loop. What protects
# that now: voice_command() ends with oww.reset() (wake model state cleared),
# a reply is followed by POST_PLAY_TAIL_S, and the main loop sleeps 0.5s before
# reopening the wake stream — so the wake word is scored no sooner than
# ~0.75s + this after Zoe's last sample. 0.4s keeps ~1.15s of margin.
# Conversation mode never reads it until the conversation has closed.
POST_PLAY_COOLDOWN_S = max(0.0, _float_env("POST_PLAY_COOLDOWN_S", 0.4))
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
# Cloudflare Access service token (flag-dark). Only the virtual panel reaching zoe-data
# through the tunnel sets these (docs/knowledge/mac-virtual-panel.md): Access answers a
# credential-less API call with a 302 to its login page. Unset (the Pi, on the LAN) the
# dict above is untouched. Both halves or neither - a lone half is logged and ignored.
CF_ACCESS_CLIENT_ID = os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
CF_ACCESS_CLIENT_SECRET = os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()


def _zoe_url_may_carry_access_secret(url: str) -> bool:
    """True only for an https URL whose host is a public name or address.

    The Access secret is a bearer credential for the tunnel. Copying the Pi's LAN
    ZOE_URL (http://192.168.x.x, https://zoe.local) into the env that holds it would
    send the secret to a LAN host - in the clear for http - so anything private,
    loopback, link-local, ``.local``/``.lan``/``.internal`` or a bare single-label
    name is refused. (A public name that resolves privately via split DNS cannot be
    seen from here.)"""
    import ipaddress
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parts.scheme != "https" or not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host and not host.endswith((".local", ".localhost", ".lan", ".internal", ".home.arpa"))
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
                or ip.is_multicast or ip.is_reserved)


_CF_ACCESS_REFUSED = False
if CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET:
    if _zoe_url_may_carry_access_secret(ZOE_URL):
        _headers["CF-Access-Client-Id"] = CF_ACCESS_CLIENT_ID
        _headers["CF-Access-Client-Secret"] = CF_ACCESS_CLIENT_SECRET
    else:
        _CF_ACCESS_REFUSED = True
        log.error("CF_ACCESS_CLIENT_ID/SECRET are set but ZOE_URL=%s is not a public https URL; "
                  "the Access secret would be sent to a LAN host (in the clear for http). "
                  "No Access headers are attached and main() will refuse to start.", ZOE_URL)
elif CF_ACCESS_CLIENT_ID or CF_ACCESS_CLIENT_SECRET:
    log.warning("CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET must both be set; "
                "sending no Cloudflare Access headers")
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
# time.monotonic() when the CURRENT _tts_process started (its first audio was
# handed to the player). Written with _tts_process under _tts_process_lock; the
# barge detectors anchor their grace period and stale-audio cut-off to it.
_tts_started_at: float | None = None
# The streaming turn's open HTTP response (BARGE_DUCK_ENABLED): a COMMIT closes
# it at the commit instant, because the stream loop can sit in iter_lines for
# seconds while the brain generates the next sentence.
_turn_response = None
_turn_response_lock = threading.Lock()
_barge_stream_closed = threading.Event()
# Last COMMIT's played-prefix estimate (phase 3 puts it on the wire).
_last_barge_commit: dict = {}


def _set_turn_response(r) -> None:
    global _turn_response
    with _turn_response_lock:
        _turn_response = r


def _close_turn_response() -> bool:
    """COMMIT: close the turn's response NOW so the server-side cancel (and A1's
    abort) lands at the commit instant, not at the next NDJSON line. Best
    effort: the stream loop treats the resulting read error as the barge."""
    global _turn_response
    with _turn_response_lock:
        r, _turn_response = _turn_response, None
    if r is None:
        return False
    _barge_stream_closed.set()
    try:
        r.close()
        return True
    except Exception as exc:
        log.debug("turn response close failed: %s", exc)
        return False


def _register_tts_process(proc: "subprocess.Popen") -> None:
    """Publish a newly started player as the active TTS process + its start time."""
    global _tts_process, _tts_started_at
    with _tts_process_lock:
        _tts_process = proc
        _tts_started_at = time.monotonic()


def _active_playback() -> "tuple[subprocess.Popen, float] | None":
    """(proc, started_at) while a player is running, else None."""
    with _tts_process_lock:
        proc, started = _tts_process, _tts_started_at
    if proc is None or started is None or proc.poll() is not None:
        return None
    return proc, started

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
_BARGE_QUEUE: "queue.Queue[tuple[float, bytes]]" = None  # type: ignore  # set in main(); (captured_at, chunk)
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
        # -1.0 sentinel, NOT 0.0: with the deep-quiet fast tail, 0.0 reads as the
        # strongest possible silence — a crashing Silero would fast-exit every
        # turn early on its own failures (Codex P2). Callers treat the sentinel
        # as quiet-but-AMBIGUOUS: it still counts toward the long 800ms tail
        # (so a permanently broken VAD degrades to the old timeout, never hangs)
        # but never toward the deep counter.
        return -1.0


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
        self._deep_quiet = 0
        # spoke=True when the caller already confirmed speech (the follow-up
        # recorder's VAD trigger) so the fast tail applies from the first pause.
        self._spoke = spoke
        self._amp_max_silent = int(SILENCE_TIMEOUT_S * SAMPLE_RATE / CHUNK_SIZE)
        self._vad_max_silent = max(1, int(VAD_ENDPOINT_SILENCE_S * SAMPLE_RATE / CHUNK_SIZE))
        # Deep-quiet fast tail (see ZOE_VAD_TAIL_MS above): None = disabled,
        # i.e. exactly the pre-flag behaviour.
        # ceil, not floor: 639ms must mean 8 chunks (640ms), not 7 (560ms) —
        # flooring silently exits up to a full chunk EARLIER than configured,
        # which is the aggressive direction and invalidates ear-tuned values.
        self._deep_max_silent = (
            max(1, -(-ZOE_VAD_TAIL_MS * SAMPLE_RATE // (1000 * CHUNK_SIZE)))
            if self.mode == "vad" and ZOE_VAD_TAIL_MS > 0 else None
        )
        # B1.1 first-verdict tail (see ZOE_SPECULATIVE_TURN above): None = the
        # speculative hook never fires. Same ceil rule as the fast tail.
        self._spec_max_silent = (
            max(1, -(-ZOE_SPECULATIVE_TAIL_MS * SAMPLE_RATE // (1000 * CHUNK_SIZE)))
            if self.mode == "vad" and ZOE_SPECULATIVE_TURN and ZOE_SPECULATIVE_TAIL_MS > 0 else None
        )
        if (self._spec_max_silent is not None and self._deep_max_silent is not None
                and self._deep_max_silent <= self._spec_max_silent):
            # The fast tail closes the recording at or before the first verdict
            # could fire, so there is no dead time to reclaim: firing at the
            # close would only add a verdict round-trip. Inert — but LOUDLY,
            # once per process, so an inert flag is never mistaken for a live one.
            self._spec_max_silent = None
            if "inert_tail" not in _speculation_warned:
                _speculation_warned.add("inert_tail")
                log.warning(
                    "ZOE_SPECULATIVE_TURN is inert: ZOE_VAD_TAIL_MS=%d <= ZOE_SPECULATIVE_TAIL_MS=%d "
                    "closes the recording before the first verdict — raise the fast tail or lower the "
                    "speculative tail", ZOE_VAD_TAIL_MS, ZOE_SPECULATIVE_TAIL_MS)
        self.speculation_fired = False
        self.resumed_after_speculation = False
        self._min_frames = int(0.5 * SAMPLE_RATE / CHUNK_SIZE)
        # Adaptive tail (ZOE_VAD_CLEAN_TAIL_MS / ZOE_VAD_HESITATION_TAIL_MS):
        # None = off. ceil like the fast tail — never earlier than configured.
        def _ms_to_chunks(ms: int) -> int:
            return max(1, -(-ms * SAMPLE_RATE // (1000 * CHUNK_SIZE)))
        self._clean_max_silent = (
            _ms_to_chunks(max(ZOE_VAD_CLEAN_TAIL_MS, _CLEAN_TAIL_FLOOR_MS))
            if self.mode == "vad" and ZOE_VAD_CLEAN_TAIL_MS > 0 else None
        )
        self._clean_min_speech = (
            -(-ZOE_VAD_CLEAN_MIN_SPEECH_MS * SAMPLE_RATE // (1000 * CHUNK_SIZE))
        )
        self._hes_max_silent = (
            _ms_to_chunks(min(ZOE_VAD_HESITATION_TAIL_MS, _HESITATION_TAIL_CAP_MS))
            if self.mode == "vad" and ZOE_VAD_HESITATION_TAIL_MS > 0 else None
        )
        self._speech_chunks = 0   # speech chunks heard so far this recording
        self._fall = 0            # ambiguous decay chunks leading the current quiet run
        self._hesitating = False  # the current quiet run went deep, then ambiguous again
        self.tail_rule = ""       # which rule closed the recording (logged)

    def speculative_ready(self, n_frames: int) -> bool:
        """True exactly ONCE per recording: the first end-of-turn verdict
        (confirmed speech, then ZOE_SPECULATIVE_TAIL_MS of consecutive deep
        quiet). Call after ``push`` returned False. Never fires when the flag
        is off, in amplitude mode, or inside the minimum-recording guard."""
        if (self._spec_max_silent is None or self.speculation_fired
                or not self._spoke or n_frames <= self._min_frames):
            return False
        if self._deep_quiet >= self._spec_max_silent:
            self.speculation_fired = True
            return True
        return False

    def push(self, data: bytes, n_frames: int) -> bool:
        """Feed one recorded chunk; True when the recording should stop."""
        if self.mode == "vad":
            prob = _vad_prob(self._model, np.frombuffer(data, dtype=np.int16))
            if prob >= VAD_ENDPOINT_THRESHOLD:
                if self.speculation_fired:
                    # Speech after the first verdict: the speculative turn ran
                    # on a prefix. The verdict becomes ``resolve`` (server
                    # compares transcripts), never a blind commit.
                    self.resumed_after_speculation = True
                self._spoke = True
                self._quiet = 0
                self._deep_quiet = 0
                self._speech_chunks += 1
                self._fall = 0
                self._hesitating = False
                return False
            self._quiet += 1
            # Borderline chunks (deep prob <= prob < speech threshold) count as
            # quiet but RESET the deep counter: an ambiguous pause must never
            # take the fast exit, only unambiguous silence may. The same goes for
            # the inference-failure sentinel (prob < 0): a broken VAD is the
            # opposite of evidence of silence.
            deep = 0.0 <= prob < ZOE_VAD_TAIL_DEEP_PROB
            self._deep_quiet = self._deep_quiet + 1 if deep else 0
            if not deep:
                if self._fall == self._quiet - 1:
                    self._fall += 1          # still the decay straight after speech
                else:
                    self._hesitating = True  # back up after going quiet: not a clean stop
            if self.speculation_fired and not deep:
                # Quiet continuation (soft speech that never crosses the speech
                # threshold, or a VAD failure) after the first verdict: audio
                # the prefix did not contain. Force ``resolve`` — the server
                # compares transcripts and still releases when it was noise —
                # never a blind commit that skips transcribing it.
                self.resumed_after_speculation = True
            if n_frames <= self._min_frames:
                return False
            if (self._spoke and self._clean_max_silent is not None
                    and not self._hesitating
                    and self._fall <= ZOE_VAD_CLEAN_FALL_CHUNKS
                    and self._speech_chunks >= self._clean_min_speech
                    and self._quiet >= self._clean_max_silent):
                self.tail_rule = "clean"
                return True
            if (self._spoke and self._deep_max_silent is not None
                    and self._deep_quiet >= self._deep_max_silent):
                self.tail_rule = "deep"
                return True
            limit = self._vad_max_silent if self._spoke else self._amp_max_silent
            if self._spoke and self._hesitating and self._hes_max_silent is not None:
                limit = max(limit, self._hes_max_silent)
            if self._quiet >= limit:
                self.tail_rule = "hesitation" if limit != self._vad_max_silent and self._spoke else (
                    "quiet" if self._spoke else "no_speech")
                return True
            return False
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


def play_audio_b64(audio_b64: str, content_type: str = "audio/wav") -> bool:
    """Decode and play base64 audio via the platform player (aplay/mpg123 on the Pi).

    Registers the subprocess in _tts_process so the barge-in thread can kill it.
    Checks _barge_in_requested before and during playback.

    Returns True when audio reached the speaker: the player exited 0, or a
    barge-in stopped it (the listener heard it and talked over it). False when
    there was nothing to play, the player could not start, or it exited
    non-zero (device busy/missing, bad audio) — callers that report playback
    (the announcement ACK) must not claim it was heard. Never raises.
    """
    global _tts_process
    if not audio_b64:
        return False
    _barge_in_requested.clear()
    played = False
    try:
        raw = base64.b64decode(audio_b64)
        ext = "mp3" if "mpeg" in content_type else "wav"
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(raw)
            fpath = f.name
        proc, player_name = _PLATFORM.start_file_player(fpath, ext)
        _register_tts_process(proc)
        # Poll for barge-in while playback runs.
        barged = False
        while proc.poll() is None:
            if _barge_in_requested.is_set():
                log.info("Barge-in: stopping TTS playback")
                barged = True
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            time.sleep(0.05)
        played = barged or proc.returncode == 0
        if not played:
            log.warning("Audio playback failed: %s exited %s", player_name, proc.returncode)
        with _tts_process_lock:
            _tts_process = None
        try:
            os.unlink(fpath)
        except OSError:
            pass
    except Exception as exc:
        log.warning("Audio playback failed: %s", exc)
        return False
    return played


_CHUNK_S = CHUNK_SIZE / float(SAMPLE_RATE)  # seconds of audio per mic read (80ms default)


class _BargeDetector:
    """Barge-in decision over per-chunk Silero probabilities, anchored to playback.

    Pure state machine (no I/O) shared by both barge paths — the per-turn
    _BargeMonitor and the queue-fed _barge_in_vad_thread — so the guards below
    cannot drift apart. Self-interruption 2026-09-28 (incident-runbook §12):

    * Only audio CAPTURED after playback started can count. ``new_playback``
      clears the window; ``feed`` rejects any chunk whose capture began before
      ``started_at + grace`` — so neither the user's own trailing words from
      before Zoe spoke nor a mic backlog read late can trip it.
    * Grace: the first ``grace_ms`` of playback is ignored (Zoe's onset while
      the speakerphone's echo canceller and the resumed output sink settle).
    * Sustained speech: ``min_chunks`` of the last ``window_chunks`` at
      ``threshold`` — or ``fast_chunks`` CONSECUTIVE chunks at ``fast_prob``
      (a loud, unambiguous interruption). ``fast_chunks <= 0`` disables it.
    * Fires at most once per playback.
    """

    def __init__(self, *, threshold: float | None = None, min_chunks: int | None = None,
                 window_chunks: int | None = None, grace_ms: int | None = None,
                 fast_prob: float | None = None, fast_chunks: int | None = None):
        self.threshold = BARGE_IN_THRESHOLD if threshold is None else float(threshold)
        self.min_chunks = max(1, BARGE_MIN_CHUNKS if min_chunks is None else int(min_chunks))
        window = BARGE_WINDOW_CHUNKS if window_chunks is None else int(window_chunks)
        self.window_chunks = max(self.min_chunks, window)
        self.grace_s = max(0, BARGE_GRACE_MS if grace_ms is None else int(grace_ms)) / 1000.0
        fast = BARGE_FAST_PROB if fast_prob is None else float(fast_prob)
        # The fast path may never be LESS strict than the ordinary threshold.
        self.fast_prob = max(fast, self.threshold)
        self.fast_chunks = BARGE_FAST_CHUNKS if fast_chunks is None else int(fast_chunks)
        self._probs: deque = deque(maxlen=self.window_chunks)
        self._run = 0
        self._anchor: float | None = None
        self._fired = False
        self.reason = ""

    def new_playback(self, started_at: float) -> bool:
        """Anchor to the playback that began at ``started_at``. True when it is a
        NEW playback (window cleared), False when already anchored to it."""
        if self._anchor == started_at:
            return False
        self._anchor = started_at
        self._probs.clear()
        self._run = 0
        self._fired = False
        self.reason = ""
        return True

    def idle(self) -> None:
        """Nothing is playing: nothing heard now may count toward a barge."""
        self._anchor = None
        self._probs.clear()
        self._run = 0

    def feed(self, prob: float, captured_at: float) -> bool:
        """Score one chunk whose capture BEGAN at ``captured_at`` (monotonic).
        Returns True exactly once per playback, when the barge-in should fire."""
        if self._anchor is None or self._fired:
            return False
        if captured_at < self._anchor + self.grace_s:
            return False  # stale (pre-playback) or inside the onset grace
        self._probs.append(prob)
        self._run = self._run + 1 if prob >= self.fast_prob else 0
        if self.hits() >= self.min_chunks:
            self.reason = "window"
        elif self.fast_chunks > 0 and self._run >= self.fast_chunks:
            self.reason = "fast"
        else:
            return False
        self._fired = True
        return True

    def hits(self) -> int:
        return sum(1 for p in self._probs if p >= self.threshold)

    def onset_offset_chunks(self) -> int:
        """Chunks between the first speech chunk in the window and the last one."""
        for i, p in enumerate(self._probs):
            if p >= self.threshold:
                return len(self._probs) - 1 - i
        return 0

    def rearm(self) -> None:
        """After a RESUME: the same playback may be interrupted again."""
        self._probs.clear()
        self._run = 0
        self._fired = False
        self.reason = ""

    def elapsed_ms(self, now: float) -> int:
        return int(round((now - self._anchor) * 1000)) if self._anchor is not None else -1

    def window_repr(self) -> str:
        probs = " ".join(f"{p:.2f}" for p in self._probs)
        return f"{self.hits()}/{self.window_chunks}[{probs}]{'+' + self.reason if self.reason else ''}"


def _log_barge_detected(source: str, prob: float, det: _BargeDetector, suffix: str = "") -> None:
    log.info("Barge-in detected during playback (%s, prob=%.2f, th=%.2f, t+%dms, window=%s)%s",
             source, prob, det.threshold, det.elapsed_ms(time.monotonic()), det.window_repr(), suffix)


def _fire_barge_in(source: str, prob: float, det: _BargeDetector, proc: "subprocess.Popen") -> None:
    """Log the decision (diagnosable next time), raise the flag, kill the player."""
    _log_barge_detected(source, prob, det)
    _barge_in_requested.set()
    # Kill the TTS subprocess DIRECTLY — the stream loop only polls the flag at
    # network-chunk boundaries, which can be seconds away while the brain
    # generates the next sentence.
    try:
        proc.terminate()
        log.info("Barge-in: TTS playback terminated immediately.")
    except Exception as exc:
        log.debug("Barge-in terminate failed: %s", exc)


def _pactl(*args: str) -> "str | None":
    """Run pactl; stdout on success, None on any failure. Replaced by tests."""
    try:
        res = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=2)
        return res.stdout if res.returncode == 0 else None
    except Exception as exc:
        log.debug("pactl %s failed: %s", " ".join(args[:2]), exc)
        return None


class _SinkInputDucker:
    """Duck / restore ONE player's PulseAudio sink-input (found by the player's
    pid). Applied at the mixer, so audio already queued is ducked too. Never
    the sink: the AirPlay-2 "Zoe Panel" output shares it."""

    def __init__(self, pid: int, *, db: float | None = None, ramp_ms: int | None = None):
        self.pid = int(pid)
        self.db = BARGE_DUCK_DB if db is None else float(db)
        self.ramp_ms = BARGE_DUCK_RAMP_MS if ramp_ms is None else int(ramp_ms)
        self.index: int | None = None
        self.baseline: int | None = None  # absolute volume before the duck
        self.ducked = False
        self._applied_db = 0.0

    def resolve(self) -> bool:
        if self.index is not None:
            return True
        idx, vol = None, None
        for line in (_pactl("list", "sink-inputs") or "").splitlines():
            m = re.match(r"\s*Sink Input #(\d+)", line)
            if m:
                idx, vol = int(m.group(1)), None
                continue
            if idx is None:
                continue
            m = re.search(r"Volume:.*?(\d+)\s*/", line)  # first channel, absolute
            if m and vol is None:
                vol = int(m.group(1))
            m = re.search(r'application\.process\.id\s*=\s*"(\d+)"', line)
            if m and int(m.group(1)) == self.pid:
                self.index, self.baseline = idx, vol
                return True
        return False

    def _step(self, db: float) -> None:
        _pactl("set-sink-input-volume", str(self.index), f"{db:+.1f}dB")  # signed = relative
        self._applied_db += db

    def duck(self) -> bool:
        if self.ducked or not self.resolve():
            return False
        self.ducked = True
        steps = min(3, max(1, self.ramp_ms // 150)) if self.ramp_ms > 0 else 1
        if steps == 1:
            self._step(self.db)
        else:
            threading.Thread(target=self._ramp, args=(steps,), daemon=True,
                             name="barge-duck-ramp").start()
        return True

    def _ramp(self, steps: int) -> None:
        for _ in range(steps):
            if not self.ducked:
                return
            self._step(self.db / steps)
            time.sleep(self.ramp_ms / steps / 1000.0)

    def restore(self) -> None:
        global _duck_leak
        if not self.ducked:
            return
        self.ducked = False
        if self.baseline is not None:
            if _pactl("set-sink-input-volume", str(self.index), str(self.baseline)) is None:
                _duck_leak = self.baseline  # stream-restore now holds the ducked value
        elif self._applied_db:
            self._step(-self._applied_db)
        self._applied_db = 0.0


class _BargeDecider:
    """The decide window after a duck (pure, fed from the detector's thread):
    COMMIT when speech since onset >= commit_ms, RESUME after resume_ms of
    quiet, CEILING (= resume) max_ms after onset. The -1.0 VAD-failure
    sentinel is neither speech nor quiet, so a broken VAD can never commit."""

    def __init__(self, *, threshold: float | None = None, commit_ms: int | None = None,
                 resume_ms: int | None = None, max_ms: int | None = None):
        self.threshold = BARGE_IN_THRESHOLD if threshold is None else float(threshold)
        self.commit_ms = BARGE_COMMIT_SPEECH_MS if commit_ms is None else int(commit_ms)
        self.resume_ms = BARGE_RESUME_SILENCE_MS if resume_ms is None else int(resume_ms)
        self.max_ms = BARGE_DECIDE_MAX_MS if max_ms is None else int(max_ms)
        self.onset_at: float | None = None
        self.speech_ms = 0.0
        self.quiet_ms = 0.0
        self.in_speech = False
        self.outcome = ""

    def start(self, onset_at: float, speech_ms: float = 0.0) -> None:
        self.onset_at, self.speech_ms, self.quiet_ms = onset_at, speech_ms, 0.0
        self.in_speech, self.outcome = True, ""

    def feed(self, prob: float, captured_at: float) -> str:
        """Score one chunk captured at ``captured_at``: "" while undecided, else
        "commit" | "resume" | "ceiling" exactly once."""
        if self.onset_at is None or self.outcome:
            return ""
        chunk_ms = _CHUNK_S * 1000.0
        if prob >= self.threshold:
            self.speech_ms, self.quiet_ms, self.in_speech = self.speech_ms + chunk_ms, 0.0, True
        elif prob >= 0.0:
            self.quiet_ms, self.in_speech = self.quiet_ms + chunk_ms, False
        if self.speech_ms >= self.commit_ms:
            self.outcome = "commit"
        elif self.quiet_ms >= self.resume_ms:
            self.outcome = "resume"
        elif (captured_at + _CHUNK_S - self.onset_at) * 1000.0 >= self.max_ms:
            self.outcome = "ceiling"
        return self.outcome

    def elapsed_ms(self, now: float) -> int:
        return int(round((now - self.onset_at) * 1000)) if self.onset_at is not None else -1


class _PlayoutLedger:
    """Sentence writes to the player (time + duration) → the played prefix at a
    COMMIT: sentence k starts at max(write_k, end_{k-1}), heard when
    end_k + playout latency <= now."""

    def __init__(self):
        self.ends: list[float] = []
        self.durs: list[float] = []

    def reset(self) -> None:
        self.ends, self.durs = [], []

    def note(self, at: float, seconds: float) -> None:
        start = max(at, self.ends[-1]) if self.ends else at
        self.ends.append(start + seconds)
        self.durs.append(seconds)

    def heard(self, at: float, latency_s: float) -> tuple[int, int]:
        n = sum(1 for e in self.ends if e + latency_s <= at)
        return n, int(round(sum(self.durs[:n]) * 1000))


_PLAYOUT = _PlayoutLedger()
# PulseAudio's module-stream-restore persists a stream's volume per application:
# a player that exits while ducked would leave EVERY later aplay stream (replies,
# beeps, announcements) at -15 dB. The leaked baseline is healed on the next player.
_duck_leak: int | None = None
_duck_heal_tried: int | None = None  # pid of the player the heal was attempted on


def _heal_duck_leak(proc: "subprocess.Popen") -> None:
    """Once per player: put the leaked baseline back on its sink-input."""
    global _duck_leak, _duck_heal_tried
    if _duck_leak is None or proc.pid == _duck_heal_tried:
        return
    d = _SinkInputDucker(proc.pid)
    if not d.resolve():  # the new player's sink-input may take a chunk or two to appear
        return
    _duck_heal_tried = proc.pid
    if _pactl("set-sink-input-volume", str(d.index), str(_duck_leak)) is not None:
        log.info("Barge-in duck: healed leaked stream volume (%d) on sink-input %d", _duck_leak, d.index)
        _duck_leak = None


class _BargeEpisode:
    """One duck → decide → commit/resume episode (BARGE_DUCK_ENABLED). Opened
    when the detector fires; ``feed`` returns the outcome once; ``finish``
    restores the volume, acts on a COMMIT and writes the BARGE_DECIDE line."""

    def __init__(self, source: str, proc: "subprocess.Popen", det: _BargeDetector,
                 captured_at: float):
        self.source, self.proc = source, proc
        self.ducker = _PLATFORM.make_ducker(proc)
        self.decider = _BargeDecider()
        self.decider.start(captured_at - det.onset_offset_chunks() * _CHUNK_S,
                           speech_ms=det.hits() * _CHUNK_S * 1000.0)
        self.done = False

    @classmethod
    def open(cls, source: str, proc: "subprocess.Popen", det: _BargeDetector, prob: float,
             captured_at: float) -> "_BargeEpisode | None":
        """None when the player cannot be ducked (no pactl / no sink-input for its
        pid): the caller falls back to today's hard stop."""
        ep = cls(source, proc, det, captured_at)
        if not ep.ducker.duck():
            log.info("Barge-in duck unavailable (no PulseAudio sink-input for pid %s) — hard stop",
                     getattr(proc, "pid", "?"))
            return None
        _log_barge_detected(source, prob, det, f" — ducked {ep.ducker.db:+.0f}dB, deciding")
        return ep

    def feed(self, prob: float, captured_at: float, now: float) -> str:
        outcome = self.decider.feed(prob, captured_at)
        if outcome:
            self.finish(outcome, now)
        return outcome

    def finish(self, outcome: str, now: float) -> None:
        global _duck_leak
        if self.done:
            return
        self.done = True
        if self.proc.poll() is None:
            self.ducker.restore()
        elif self.ducker.ducked and self.ducker.baseline is not None:
            _duck_leak = self.ducker.baseline  # exited ducked: no sink-input left to restore
        heard = (-1, -1)
        if outcome == "commit":
            _barge_in_requested.set()
            try:
                self.proc.terminate()
            except Exception as exc:
                log.debug("Barge-in terminate failed: %s", exc)
            _close_turn_response()
            heard = _PLAYOUT.heard(now, BARGE_PLAYOUT_LATENCY_MS / 1000.0)
            _last_barge_commit.clear()
            _last_barge_commit.update(at=now, heard_chunks=heard[0], heard_ms=heard[1])
        log.info("BARGE_DECIDE outcome=%s ms=%d speech_ms=%d duck_db=%.1f heard_chunks=%d "
                 "heard_ms=%d (%s)", outcome, self.decider.elapsed_ms(now),
                 int(self.decider.speech_ms), self.ducker.db, heard[0], heard[1], self.source)


def _drain_stream_backlog(stream) -> int:
    """Discard whole chunks already buffered on a PyAudio input stream (audio
    captured BEFORE now). Returns the number of chunks dropped."""
    try:
        avail = int(stream.get_read_available())
    except Exception:
        return 0
    n = max(0, avail // CHUNK_SIZE)
    for i in range(n):
        try:
            stream.read(CHUNK_SIZE, exception_on_overflow=False)
        except Exception:
            return i
    return n


class _BargeMonitor:
    """Dedicated mic reader for barge-in DURING TTS playback.

    The always-on wake stream is CLOSED for the whole command cycle (the Jabra
    cannot hold two input streams — PyAudio -9985), so the queue-fed barge
    thread hears nothing exactly when barge-in matters. This monitor opens its
    own short-lived stream for the turn (no other input stream is open then)
    and asks a _BargeDetector whether to stop Zoe. It opens at turn START, so it
    listens through the STT/brain wait; everything heard before playback begins
    is discarded when it does (window cleared, buffered backlog dropped).
    """

    def __init__(self, pa: "pyaudio.PyAudio"):
        self._pa = pa
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capturing = False
        self.seed: bytes | None = None  # a COMMIT's interrupting utterance (WAV)

    def start(self) -> None:
        if not BARGE_IN_ENABLED or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="barge-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            # A seed capture (BARGE_SEED_NEXT_TURN) records to the normal
            # endpoint, exactly where the follow-up listener would block.
            t.join(timeout=RECORD_SECONDS + 2.0 if self._capturing else 1.5)
        self._thread = None

    def take_seed(self) -> "bytes | None":
        seed, self.seed = self.seed, None
        return seed

    def _capture_seed(self, stream, frames: list) -> None:
        """COMMIT: keep recording the interrupting utterance on the SAME stream to
        the normal endpoint; the next turn runs on it, no beep (LiveKit-lane shape)."""
        self._capturing = True
        endpointer = _Endpointer(spoke=True)
        max_chunks = int(RECORD_SECONDS * SAMPLE_RATE / CHUNK_SIZE)
        stop_reason = "max_duration"
        while len(frames) < max_chunks and not _shutdown.is_set():
            try:
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception:
                stop_reason = "stream"
                break
            frames.append(data)
            if endpointer.push(data, len(frames)):
                stop_reason = "silence"
                break
        self.seed = _frames_to_wav(self._pa, frames)
        log.info("Barge-in seed: %.2fs from onset (stop=%s) — next turn runs on it, no beep",
                 len(frames) * _CHUNK_S, stop_reason)

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
        det = _BargeDetector()
        log.debug("Barge monitor listening (th=%.2f, %d/%d chunks, grace=%dms, fast=%d@%.2f)",
                  det.threshold, det.min_chunks, det.window_chunks, int(det.grace_s * 1000),
                  det.fast_chunks, det.fast_prob)
        episode: _BargeEpisode | None = None
        ring: deque | None = None  # FOLLOWUP_LOOKBACK_CHUNKS before onset ...
        seed_frames: list | None = None  # ... then everything in the decide window
        seed_cap = 0
        if BARGE_DUCK_ENABLED and BARGE_SEED_NEXT_TURN:
            lookback = int(os.environ.get("FOLLOWUP_LOOKBACK_CHUNKS", "4"))
            ring = deque(maxlen=lookback)
            # Bounded by construction: lookback + the longest decide window
            # (~2.3 s, ~75 KB at 16 kHz), whatever the decider does.
            seed_cap = lookback + int(math.ceil(BARGE_DECIDE_MAX_MS / 1000.0 / _CHUNK_S)) + 1
        try:
            while not self._stop.is_set() and not _shutdown.is_set():
                try:
                    chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                except Exception:
                    break
                read_at = time.monotonic()
                # Every chunk goes through Silero so its streaming state stays
                # continuous, but only audio heard DURING playback can count.
                prob = _vad_prob(model, np.frombuffer(chunk, dtype=np.int16))
                if ring is not None:
                    ring.append(chunk)
                    if seed_frames is not None and len(seed_frames) < seed_cap:
                        seed_frames.append(chunk)
                playing = _active_playback()
                if playing is None:
                    if episode is not None:
                        # The reply finished while deciding: nothing to resume.
                        # Someone still talking becomes the next turn, no beep.
                        episode.finish("ended", read_at)
                        if seed_frames is not None and episode.decider.in_speech:
                            self._capture_seed(stream, seed_frames)
                            break
                        episode, seed_frames = None, None
                    # Nothing is playing — the user STILL TALKING (e.g. the
                    # endpointer closed on a long pause) or the room during the
                    # STT/brain wait. Never an interruption of Zoe (live 22:28:10:
                    # a reply died before any audio played), and it must not
                    # carry into playback either (live 2026-09-28 18:24:50: a
                    # user still talking at playback start fired it at t+11ms).
                    det.idle()
                    continue
                proc, started_at = playing
                _heal_duck_leak(proc)
                if det.new_playback(started_at):
                    dropped = _drain_stream_backlog(stream)
                    if dropped:
                        log.info("barge monitor: dropped %d stale chunks (~%dms)",
                                 dropped, int(dropped * _CHUNK_S * 1000))
                    # The chunk in hand was captured before playback began.
                    continue
                if episode is not None:
                    outcome = episode.feed(prob, read_at - _CHUNK_S, read_at)
                    if outcome == "commit":
                        if seed_frames is not None:
                            self._capture_seed(stream, seed_frames)
                        break
                    if outcome:  # resume / ceiling: the same playback may be barged again
                        episode, seed_frames = None, None
                        det.rearm()
                    continue
                if det.feed(prob, read_at - _CHUNK_S):
                    if BARGE_DUCK_ENABLED:
                        episode = _BargeEpisode.open("monitor", proc, det, prob, read_at - _CHUNK_S)
                        if episode is not None:
                            if ring is not None:
                                seed_frames = list(ring)  # the onset the detector needed to see
                            continue
                    _fire_barge_in("monitor", prob, det, proc)
                    break
        finally:
            if episode is not None:
                episode.finish("ended", time.monotonic())  # monitor stopped mid-decide
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass


def _drain_barge_queue() -> list:
    """Take everything currently queued on _BARGE_QUEUE without blocking."""
    items = []
    while True:
        try:
            items.append(_BARGE_QUEUE.get_nowait())
        except _queue_module.Empty:
            return items


def _barge_in_vad_thread():
    """Background thread: runs Silero VAD during TTS playback to detect barge-in.

    Reads (captured_at, chunk) pairs from the shared _BARGE_QUEUE, fed by the
    always-on wake stream — so it only hears anything while that stream is
    open, i.e. for playback OUTSIDE a voice turn (announcements). Same
    _BargeDetector as the monitor: chunks queued before playback began are
    dropped, then grace + sustained speech.
    """
    model, _ = _get_silero_vad()
    if model is None:
        log.info("Barge-in thread: Silero VAD unavailable, barge-in disabled.")
        return

    log.info("Barge-in VAD thread started (threshold=%.2f)", BARGE_IN_THRESHOLD)
    det = _BargeDetector()
    episode: _BargeEpisode | None = None
    while not _shutdown.is_set():
        try:
            item = _BARGE_QUEUE.get(timeout=0.1)
        except _queue_module.Empty:
            continue

        # Only do VAD inference when TTS is actually playing.
        playing = _active_playback()
        if playing is None:
            if episode is not None:
                episode.finish("ended", time.monotonic())
                episode = None
            det.idle()
            continue
        proc, started_at = playing
        _heal_duck_leak(proc)
        batch = [item]
        if det.new_playback(started_at):
            batch += _drain_barge_queue()
            stale = [b for b in batch if b[0] < started_at]
            if stale:
                log.info("barge queue: dropped %d stale chunks (~%dms)",
                         len(stale), int(len(stale) * _CHUNK_S * 1000))
            batch = [b for b in batch if b[0] >= started_at]
        for captured_at, chunk in batch:
            prob = _vad_prob(model, np.frombuffer(chunk, dtype=np.int16))
            if episode is not None:
                outcome = episode.feed(prob, captured_at, time.monotonic())
                if outcome:
                    episode = None
                    if outcome == "commit":
                        break
                    det.rearm()
                continue
            if det.feed(prob, captured_at):
                if BARGE_DUCK_ENABLED:
                    episode = _BargeEpisode.open("queue", proc, det, prob, captured_at)
                    if episode is not None:
                        continue
                _fire_barge_in("queue", prob, det, proc)
                break
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
        _PLATFORM.play_file_blocking(fpath)
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
    if not _PLATFORM.has_panel_agent:
        return  # a virtual panel has no on-box agent; its screen is a browser tab
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
        _PLATFORM.play_file_blocking(fpath, timeout=3)
        os.unlink(fpath)
    except Exception as exc:
        log.debug("Follow-up beep failed: %s", exc)


def _frames_to_wav(pa: pyaudio.PyAudio, frames: list[bytes]) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def record_command(pa: pyaudio.PyAudio, stream=None, speculation: "_SpeculativeTurn | None" = None) -> bytes | None:
    """Record audio until silence or max duration.

    ``speculation`` (B1.1, flag-gated) receives the audio-so-far at the
    endpointer's FIRST verdict via ``fire()`` while recording continues on the
    same stream; when the recording closes it learns whether speech resumed.

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
        if speculation is not None and endpointer.speculative_ready(len(frames)):
            speculation.fire(_frames_to_wav(pa, frames))
    if owns_stream:
        stream.stop_stream()
        stream.close()
    if speculation is not None:
        speculation.recording_closed(resumed=endpointer.resumed_after_speculation)
    duration_s = len(frames) * CHUNK_SIZE / float(SAMPLE_RATE)
    log.info(
        "Recorded command: duration=%.2fs chunks=%d stop=%s endpoint=%s tail=%s silence_timeout=%.2fs",
        duration_s, len(frames), stop_reason, endpointer.mode, endpointer.tail_rule or "-",
        SILENCE_TIMEOUT_S,
    )
    if len(frames) < int(0.3 * SAMPLE_RATE / CHUNK_SIZE):
        log.info("Command too short, ignoring.")
        return None
    return _frames_to_wav(pa, frames)


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
    """Speak a short phrase locally (espeak-ng on the Pi, say on a Mac) as an emergency fallback."""
    try:
        cmd = _PLATFORM.local_tts_cmd(text)
        subprocess.run(cmd, check=False, timeout=10)
    except FileNotFoundError:
        log.debug("%s not installed; local TTS fallback unavailable", _PLATFORM.local_tts_cmd("")[0])
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


# Where the last claim on THIS thread came from: "local" (cosine match against
# the synced profile cache) or "server" (the /api/voice/identify fallback).
# n_profiles describes the LOCAL cache, so it is meaningless — and actively
# misleading — for a server-derived claim, which is scored against a profile set
# this process never saw. Thread-local because turns can overlap.
_claim_ctx = threading.local()


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
    compared = 0
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
        compared += 1
        if score > best_score:
            best_user, best_score = uid, score
    # Malformed/zero-norm/dimension-mismatched rows were SKIPPED above — the
    # shadow row must report how many profiles the score was actually chosen
    # from, not the raw cache length.
    _claim_ctx.n_compared = compared
    if best_user is None:
        return None
    return best_user, best_score


def _speaker_id_warmup() -> None:
    """Warm the FULL speaker-ID pipeline (encoder load + preprocess + embed).

    The first preprocess_wav call pays ~2.5s of librosa/numba JIT on the Pi,
    which otherwise lands on the user's first spoken turn. Runs one embed on
    1s of low-amplitude noise — not silence, which preprocess_wav VAD-trims
    to nothing so embed_utterance would raise before the embed JIT runs.
    Best-effort: never raises, never leaves a temp file behind.
    """
    encoder = _get_voice_encoder()
    if encoder is None:
        return
    warm_path = None
    try:
        import tempfile as _tmp, wave as _wave
        from resemblyzer import preprocess_wav  # type: ignore

        with _tmp.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            warm_path = f.name
        with _wave.open(warm_path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            rng = np.random.default_rng(0)
            w.writeframes((rng.standard_normal(16000) * 300).astype(np.int16).tobytes())
        encoder.embed_utterance(preprocess_wav(warm_path))
        log.info("Speaker-ID pipeline warmed (JIT done).")
    except Exception as exc:
        log.debug("speaker-ID warmup embed failed: %s", exc)
    finally:
        if warm_path is not None:
            try:
                os.unlink(warm_path)
            except OSError:
                pass


def _identify_speaker_from_wav(wav_bytes: bytes) -> tuple[str, float] | None:
    """Compute a resemblyzer embedding and identify the speaker.

    Matches locally against the synced profile cache (preferred — keeps the
    Jetson model-free) and falls back to POST /api/voice/identify when no
    profiles are cached (older server / first run). Returns (user_id, score);
    the remote fallback reports the server's confidence. None when speaker ID
    is disabled, resemblyzer is unavailable, or nothing matched.
    """
    _claim_ctx.source = None
    if not SPEAKER_ID_ENABLED:
        return None
    encoder = _get_voice_encoder()
    if encoder is None:
        # NOT a no-match: nothing was scored. Recorded distinctly so a shadow
        # week run on a panel missing resemblyzer produces rows that say so,
        # instead of a file full of null user_ids that reads as "never matched".
        _claim_ctx.source = "encoder_unavailable"
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
            _claim_ctx.source = "local"
            return local

        emb_bytes = embedding.astype(_np.float32).tobytes()
        emb_b64 = _b64.b64encode(emb_bytes).decode()
        resp = _api_post("/api/voice/identify", {"embedding_base64": emb_b64}, timeout=5)
        # _api_post swallows transport/HTTP failures into {"ok": False, ...}
        # instead of raising, so "the call returned" does NOT mean "the server
        # scored the turn". Only a response carrying the identify shape earns
        # source='server'; a swallowed failure is an ERROR row, or the FA/FR
        # review would read a week of dead panel->server calls as no-matches.
        if "identified" not in resp:
            _claim_ctx.source = "error"
            log.warning("Speaker ID identify failed: %s", resp.get("error") or resp)
            return None
        _claim_ctx.source = "server"
        if resp.get("identified"):
            uid = resp.get("user_id")
            if not uid:
                return None  # legacy server echoed identified without a user
            try:
                conf = float(resp.get("confidence") or 1.0)
                if not math.isfinite(conf):
                    raise ValueError("non-finite confidence")
            except (TypeError, ValueError):
                # A malformed confidence is a MALFORMED RESPONSE, not a server
                # score — letting the generic handler keep source='server' here
                # would count it as a scored turn in the FA/FR review.
                _claim_ctx.source = "error"
                log.warning("Speaker ID identify returned malformed confidence: %r",
                            resp.get("confidence"))
                return None
            return uid, conf
        return None
    except ImportError:
        # resemblyzer import failed mid-path — same class as encoder_unavailable.
        _claim_ctx.source = "encoder_unavailable"
        return None
    except Exception as exc:
        # The attempt ERRORED (network to /api/voice/identify, a garbled server
        # response, ...). Recorded distinctly rather than as null: null means
        # "never attempted" (speaker ID disabled), and folding errors into it
        # would let a week of failing lookups read as ordinary unscored turns in
        # the FA/FR review. If the server had already answered, keep "server".
        if getattr(_claim_ctx, "source", None) != "server":
            _claim_ctx.source = "error"
        log.warning("Speaker ID failed: %s", exc)
        return None


# Distinguishes "caller did not score this turn" from a scored no-match (None).
_CLAIM_UNSET = object()

_shadow_log_lock = threading.Lock()

# The row handle is (boot, seq), NOT a global sequence recovered from the log.
#
# Deriving the handle by re-reading the file was the wrong shape: every failure
# mode of that read — a torn tail hiding its own seq, invalid UTF-8, a transient
# OSError — silently restarted the counter and reused handles that are already
# on disk, which is precisely the ambiguity the operator review cannot tolerate.
# A per-process token makes uniqueness structural instead of recovered: `boot`
# is fresh for each daemon start, `seq` counts within that start, so no read of
# the existing file is required and no read failure can cause a collision.
# Labelling is unaffected — the operator labels the (boot, seq) pair.
_SHADOW_BOOT_ID = uuid.uuid4().hex[:8]
_shadow_seq = itertools.count(1)



def _needs_leading_newline(path: str) -> bool:
    """True when `path` is non-empty and its last byte is not a newline.

    Signals a torn tail: the previous append never finished. Any error here
    answers False — a missing separator is recoverable, a crashed turn is not.
    """
    try:
        if os.path.getsize(path) == 0:
            return False
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"
    except (OSError, ValueError):
        return False


def _record_speaker_shadow(claim: tuple[str, float] | None) -> bool:
    """Append one W5 shadow-week metrics row (JSONL) for FA/FR analysis.

    Rows carry {boot, seq, ts, panel_id, user_id, score, n_profiles, source, truth} — metadata ONLY, never audio bytes or embeddings, per
    docs/knowledge/biometric-retention-policy.md. A write failure must never
    cost the turn.

    The daemon cannot know who ACTUALLY spoke, so a row is a prediction, not a
    labelled outcome: `truth` is an empty slot the operator fills during the
    shadow-week review (see docs/architecture/panel-identity-plan.md). Without
    it FA/FR cannot be computed — `(boot, seq)` gives each row a stable handle to
    label, and `n_profiles` pins how many enrolled voices the score was chosen
    from, so a mid-week enrollment doesn't silently change what the score means.
    """
    # n_profiles describes the LOCAL cache. A claim resolved by the server
    # fallback was scored against a profile set this process never saw, so
    # reporting the local count there would state something untrue about how the
    # score was reached. Record the source and leave the count null instead.
    source = getattr(_claim_ctx, "source", None)
    if source == "local":
        # Prefer the count of rows actually COMPARED (skips excluded); fall
        # back to cache length only if the matcher never ran.
        n_profiles = getattr(_claim_ctx, "n_compared", None)
        if n_profiles is None:
            with _profile_cache_lock:
                n_profiles = len(_profile_cache["profiles"])
    else:
        n_profiles = None
    try:
        parent = os.path.dirname(SPEAKER_ID_SHADOW_LOG)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # seq is allocated AND written inside one critical section: allocating
        # outside it would let two turns interleave and land out of order.
        with _shadow_log_lock:
            row = {
                "boot": _SHADOW_BOOT_ID,
                "seq": next(_shadow_seq),
                "ts": time.time(),
                "panel_id": PANEL_ID,
                "user_id": claim[0] if claim else None,
                "score": claim[1] if claim else None,   # RAW: rounding near the threshold can flip the reconstructed accept/reject
                "n_profiles": n_profiles,   # local cache size; null for server claims
                "source": source,           # "local" | "server" | "encoder_unavailable" | "error" | null (never attempted)
                "truth": None,  # operator-filled ground truth; null until reviewed
            }
            # Heal a torn tail before appending. If a previous write was cut
            # short (power loss, SIGKILL) the file can end without a newline;
            # appending straight onto it would fuse the partial record and the
            # new one into a single malformed line, costing the analysis BOTH
            # rows. A leading newline keeps every row independently parseable —
            # the torn one stays skippable on its own line, the new one lands
            # clean. (Handle uniqueness no longer depends on this: see
            # _SHADOW_BOOT_ID.)
            # 0600, regardless of umask: rows are biometric PREDICTIONS
            # (who spoke, when, at what confidence) — no other local account
            # has any business reading them.
            def _open_private_append():
                fd = os.open(SPEAKER_ID_SHADOW_LOG,
                             os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                # os.open's mode only applies at CREATION — a log left 0644 by
                # the earlier writer stays world-readable without this.
                os.fchmod(fd, 0o600)
                return os.fdopen(fd, "a", encoding="utf-8")
            if _needs_leading_newline(SPEAKER_ID_SHADOW_LOG):
                with _open_private_append() as f:
                    f.write("\n")
            with _open_private_append() as f:
                f.write(json.dumps(row) + "\n")
        return True
    except Exception as exc:
        # Still never costs the turn — but the caller must not then claim the
        # turn was logged. A journal line saying "logged" for a row that was
        # never written over-counts scored turns in the W5 review.
        log.warning("speaker shadow metrics write failed: %s", exc)
        return False


#: `_claim_ctx.source` values that mean the turn was actually SCORED against the
#: household profiles (as opposed to "error" / "encoder_unavailable" / never attempted).
_SCORED_SOURCES = frozenset({"local", "server"})
#: What a non-shadow, scored, nobody-matched turn hands back instead of None. Falsy
#: and empty, so a caller that only asks `if claim:` is unchanged; only
#: `_speaker_field` tells it apart from "the gate did not run".
SCORED_NO_MATCH: tuple = ()


def _speaker_claim_for_turn(wav_bytes: bytes) -> tuple[str, float] | None:
    """Identify the speaker for one turn, honouring W5 shadow mode.

    Returns the claim to ATTACH to the turn payload. While SPEAKER_ID_SHADOW is
    on (the default), identity is scored + logged — journal line + JSONL
    metrics row — but None is returned, so the server never receives (and can
    never act on) the claim: shadow-before-acting, the W5 gate.
    """
    if not SPEAKER_ID_ENABLED:
        return None
    claim = _identify_speaker_from_wav(wav_bytes)
    if SPEAKER_ID_SHADOW:
        source = getattr(_claim_ctx, "source", None)
        logged = _record_speaker_shadow(claim)
        # NOTHING below may raise. The row is already on disk, and the stream
        # path's `except: pass` would leave voice_claim at the sentinel — so its
        # fallback would re-score and write a SECOND row for one spoken turn,
        # breaking the one-turn-one-row contract. Journal formatting is not
        # worth that, so it is contained.
        try:
            # "logged" is a claim about the artifact, so only say it when the row
            # actually landed — otherwise the journal and the metrics file
            # disagree and the FA/FR review silently over-counts.
            state = "logged" if logged else "NOT logged (metrics write failed)"
            if claim:
                log.info("Speaker ID (shadow): %s (%.3f) — %s, not acted on",
                         claim[0], claim[1], state)
            elif source == "error":
                # The identify ATTEMPT failed (network/auth/malformed response)
                # — logging "no match" here would hide a broken panel<->server
                # path behind what reads as a scored result.
                log.warning(
                    "Speaker ID (shadow): identify ERROR — recorded as error, "
                    "not scored; %s, not acted on", state,
                )
            elif source == "encoder_unavailable":
                # NOT a scored non-match: nothing was embedded. Saying "no match"
                # here would read as a real result and undermine the whole point
                # of distinguishing the two during the shadow-week review.
                log.warning(
                    "Speaker ID (shadow): NOT SCORED — resemblyzer unavailable "
                    "(no encoder); %s, not acted on", state,
                )
            else:
                log.info("Speaker ID (shadow): no match — %s, not acted on", state)
        except Exception as exc:  # journal formatting must never cost a row
            log.debug("shadow journal line failed (row already written): %s", exc)
        return None
    if claim is None and getattr(_claim_ctx, "source", None) in _SCORED_SOURCES:
        # The gate RAN and nobody matched: a verdict ("not a household voice"), not
        # the absence of one. Falsy, so every `if voice_claim:` still reads "no
        # claim"; `_speaker_field` turns it into `verified: false`.
        return SCORED_NO_MATCH
    return claim


# Background shadow scoring (panel TTFA fix #1, 2026-09-28). In shadow mode the
# claim is scored and logged but NEVER attached, so nothing the server receives
# depends on it — yet it used to run before the upload POST, costing a median
# 0.54 s (max 1.12 s) of dead time before every reply
# (docs/knowledge/panel-ttfa-breakdown-2026-09-28.md). Each turn's scoring now
# runs on its own daemon thread, started just before the POST.
#
# Serialised + FIFO: each scorer joins its predecessor before touching the
# encoder, so there is only ever ONE resemblyzer inference in flight (one model
# copy, flat memory, no concurrent use of a model that was never meant to be
# shared across threads) and the metrics rows still land in turn order.
_shadow_score_state_lock = threading.Lock()
_shadow_score_last: threading.Thread | None = None


# Orderly shutdown waits this long for the last pending scorer (and, through
# its predecessor join, every earlier one) so a restart right after a turn does
# not drop that turn's row + journal line. Bounded: a stop must never hang.
_SHADOW_DRAIN_TIMEOUT_S = 3.0
# A failed thread start falls back to scoring inline; it waits at most this long
# for the in-flight scorer first. If that scorer is STILL running, the turn is
# skipped (WARNING) instead of overlapping it: one inference at a time and
# in-order rows outrank one turn's row, and a stuck predecessor never holds
# the turn forever.
_SHADOW_INLINE_WAIT_S = 10.0


def _start_shadow_scoring(wav_bytes: bytes) -> threading.Thread | None:
    """Score + log one turn's shadow claim off the caller's thread.

    Same one-turn-one-row contract as the synchronous path: this calls
    `_speaker_claim_for_turn` exactly once, which writes the metrics row and
    the `Speaker ID (shadow): …` journal line. Returns the started thread
    (tests join it), or None when the thread could not start and the turn was
    scored inline instead.
    """
    global _shadow_score_last

    with _shadow_score_state_lock:
        prev = _shadow_score_last

        def _run() -> None:
            if prev is not None:
                prev.join()
            try:
                _speaker_claim_for_turn(wav_bytes)
            except Exception as exc:  # never escapes a daemon thread silently
                log.warning("Speaker ID (shadow): background scoring failed: %s", exc)

        t = threading.Thread(target=_run, daemon=True, name="speaker-shadow")
        try:
            t.start()
        except Exception as exc:  # e.g. RuntimeError: can't start new thread
            start_error: Exception | None = exc
        else:
            # Published only AFTER a successful start: later scorers join this
            # thread, and joining one that never started raises — which would
            # silently cost every later turn its row.
            _shadow_score_last = t
            return t

    # The background thread could not start. Score this turn inline (the old,
    # pre-upload behaviour) so it still gets its one row; later turns are
    # unaffected because nothing unstarted was published.
    log.warning("Speaker ID (shadow): background scorer failed to start (%s) — scoring this turn inline",
                start_error)
    if prev is not None:
        prev.join(timeout=_SHADOW_INLINE_WAIT_S)
        if prev.is_alive():
            # Scoring anyway would put two inferences on the one encoder and
            # land this row ahead of its predecessor's. Skip this turn's row,
            # loudly, rather than overlap; later turns chain onto `prev` as normal.
            log.warning("speaker shadow: skipped (predecessor still running) — no row for this turn")
            return None
    try:
        _speaker_claim_for_turn(wav_bytes)
    except Exception as exc:
        log.warning("Speaker ID (shadow): inline scoring failed: %s", exc)
    return None


def _drain_shadow_scoring(timeout: float = _SHADOW_DRAIN_TIMEOUT_S) -> bool:
    """Wait (bounded) for pending shadow scorers; True when none is left running.

    Joining the LAST scorer drains all of them, since each joins its
    predecessor before scoring.
    """
    with _shadow_score_state_lock:
        last = _shadow_score_last
    if last is None:
        return True
    last.join(timeout=timeout)
    if last.is_alive():
        log.warning("Speaker ID (shadow): scorer still running after %.1fs at shutdown — "
                    "that turn's metrics row may be lost", timeout)
        return False
    return True


def _speaker_claim_to_attach(wav_bytes: bytes) -> tuple[str, float] | None:
    """The claim to put in the turn payload, without delaying the upload.

    Shadow mode (the default) always attaches nothing, so the score is handed
    to a background thread and None comes back at once — the POST no longer
    waits on resemblyzer. Only when shadow mode is OFF does the claim ride in
    the payload; then it must exist before the POST, so it is scored inline
    (there is no follow-up path to deliver a late claim).
    """
    if SPEAKER_ID_ENABLED and SPEAKER_ID_SHADOW:
        _start_shadow_scoring(wav_bytes)
        return None
    return _speaker_claim_for_turn(wav_bytes)


def _speaker_field(claim: object) -> dict | None:
    """The turn payload's ``speaker`` block: what the speaker gate said about THIS turn,
    for memory provenance on the server (memory_authority: a self-fact spoken by a voice
    the gate did not confirm is `user_unverified`, never the owner's own statement).

        {"verified": null,  "member": "<id>", "score": 0.8123}   a candidate; the SERVER
                                                                 judges it (its own threshold
                                                                 + the member's consent)
        {"verified": false, "member": null,   "score": null}     the gate ran, nobody matched
        (absent)                                                 the gate is off, in shadow
                                                                 mode, errored, or was not run

    The daemon never sends ``verified: true`` - acceptance is the server's call, so a panel
    cannot make itself more trusted than the server allows. The legacy flat
    ``voice_user_id`` / ``voice_score`` pair still rides beside it (older servers). Absent
    = ``speaker_verified=None`` downstream = today's behaviour exactly.
    """
    if claim is _CLAIM_UNSET or claim is None or not isinstance(claim, tuple):
        return None
    if not claim:
        return {"verified": False, "member": None, "score": None}
    if len(claim) == 2:
        member, score = claim
        try:
            score = float(score)
        except (TypeError, ValueError):
            return None
        if not member or not math.isfinite(score):
            return None
        return {"verified": None, "member": str(member), "score": round(score, 4)}
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
        log.info("Buffer phrase playing: %s", os.path.basename(chosen))
        return _PLATFORM.start_buffer_player(chosen)
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
    try:
        pcm, rate, ch, width = _pcm_from_wav(wav_bytes)
    except Exception as exc:
        log.debug("bad wav chunk: %s", exc)
        return aplay
    pcm = _trim_chunk_silence(pcm, rate, ch, width)
    if aplay is None:
        # A player that cannot start raises (Popen: FileNotFoundError; Mac: OSError /
        # NotImplementedError). _do_single_turn_stream treats that as "nothing played"
        # and falls back to the blocking turn, instead of counting the reply as heard.
        aplay = _PLATFORM.start_pcm_stream(rate, ch, width)
        _register_tts_process(aplay)
        if BARGE_DUCK_ENABLED:
            _PLAYOUT.reset()
    try:
        if aplay.stdin:
            aplay.stdin.write(pcm)
            aplay.stdin.flush()
            if BARGE_DUCK_ENABLED and rate and ch and width:
                _PLAYOUT.note(time.monotonic(), len(pcm) / float(rate * ch * width))
    except (BrokenPipeError, ValueError, OSError):
        pass
    return aplay


class _SpeculativeTurn:
    """Daemon side of the B1.1 protocol (docs/architecture/b1-speculative-turn-start.md).

    ``fire(wav)`` at the first verdict starts the speculative /turn_stream in a
    thread (the server holds the audio); ``recording_closed(resumed)`` is the
    recorder's hand-off; ``finish(final_wav)`` sends the verdict — ``commit``
    when nothing followed the first verdict, ``resolve`` + the final utterance
    when speech resumed — and joins the thread. ``cancelled`` afterwards means
    the server dropped the speculative turn and the caller must run the normal
    turn on the full recording (with the claim scored here, never re-scored).
    """

    def __init__(self, pa: pyaudio.PyAudio):
        self.pa = pa
        self.turn_id = uuid.uuid4().hex
        self.fired = False
        self.resumed = False
        self.cancelled = False
        # ``gated`` flips on the server's ``speculation: gated`` ack — the first
        # frame of every gated stream. Audio on this stream WITHOUT it proves the
        # server is not gating (its flag is off) → ``ungated``: play nothing.
        # Timing cannot prove this (a slow ungated server answers late).
        self.gated = False
        self.ungated = False
        self.voice_claim: object = _CLAIM_UNSET
        self.wait_delta_ms: float | None = None
        self._t_fire: float | None = None
        self._thread: threading.Thread | None = None
        self._played = False

    def fire(self, wav: bytes) -> None:
        self.fired = True
        self._t_fire = time.monotonic()

        def _run() -> None:
            try:
                self._played = _do_single_turn_stream(
                    self.pa, wav, prompt_on_empty=False, speculation=self)
            except Exception as exc:
                log.warning("speculation: stream thread failed (%s) — treating as cancelled", exc)
                self.cancelled = True
                self._played = False

        self._thread = threading.Thread(target=_run, daemon=True, name="spec-turn")
        self._thread.start()
        log.info("speculation: fired turn_id=%s (%.2fs of audio)", self.turn_id, len(wav) / (2.0 * SAMPLE_RATE))

    def recording_closed(self, *, resumed: bool) -> None:
        self.resumed = resumed
        if self._t_fire is not None:
            self.wait_delta_ms = (time.monotonic() - self._t_fire) * 1000.0

    def finish(self, final_wav: bytes) -> bool:
        """Send the verdict and wait for the speculative stream. Returns played."""
        if self._thread is not None and self.ungated:
            self._thread.join(timeout=90)
        if self.ungated:
            # The server ran the prefix as an ordinary turn (its flag is off):
            # there is no gate to post to (409) and nothing may be played or
            # re-POSTed — the prefix WAS processed. Silent turn; latched off.
            return False
        action = "resolve" if self.resumed else "commit"
        body: dict = {"turn_id": self.turn_id, "action": action}
        if self.resumed:
            body["audio_base64"] = base64.b64encode(final_wav).decode()
        resp = _api_post("/api/voice/turn_stream/speculation", body, timeout=15, retries=0)
        if resp.get("error") == "HTTP 409":
            # Only an ungated server (flag off) answers 409 here (a max-hold
            # close is 404). Latch off; the stream thread plays nothing without
            # the ack anyway, and nothing may be re-POSTed (prefix processed).
            log.error("speculation: verdict refused (409) — server is NOT gating turn_id=%s; "
                      "speculation disabled until restart. Set ZOE_SPECULATIVE_TURN on the server "
                      "or off on this panel.", self.turn_id)
            self.ungated = True
            _speculation_disabled.set()
        log.info("speculation: %s turn_id=%s verdict=%s wait_delta=%.0fms",
                 action, self.turn_id, resp.get("verdict", resp.get("error", "?")),
                 self.wait_delta_ms or -1.0)
        # Whatever the verdict POST returned, the STREAM is the source of truth:
        # a lost verdict ends in the server's max-hold cancel, which the thread
        # sees as a cancelled done frame. Nothing plays without a commit.
        if self._thread is not None:
            self._thread.join(timeout=90)
            if self._thread.is_alive():
                log.warning("speculation: stream thread still alive after join — not re-POSTing")
                return False
        return self._played and not self.cancelled


def _do_single_turn_stream(pa: pyaudio.PyAudio, wav: bytes, *, prompt_on_empty: bool = True,
                           conversation: bool = False, voice_claim: object = _CLAIM_UNSET,
                           speculation: "_SpeculativeTurn | None" = None) -> bool:
    """Streaming turn: POST audio to /api/voice/turn_stream and play each TTS
    sentence chunk as it arrives (no buffer phrase — first audio ~1.3s). Falls
    back to the blocking _do_single_turn on any error.

    ``voice_claim`` lets a caller hand over an already-scored claim (the B1.1
    normal-turn re-run after a cancelled speculation) — re-scoring would append
    a second shadow row for one spoken turn. ``speculation`` marks the request
    speculative (server holds audio until the verdict) and receives the outcome.
    """
    global _tts_process
    import time as _time

    audio_b64_wav = base64.b64encode(wav).decode()
    # Sentinel, not None: if scoring RAISES, the fallback must re-score rather
    # than inherit a None that looks like a completed no-match. Only a scoring
    # call that actually returned replaces it. In shadow mode the call returns
    # None at once and the background scorer it started owns this turn's one
    # row, so that None is final too — the fallback must not score again.
    if voice_claim is _CLAIM_UNSET:
        try:
            voice_claim = _speaker_claim_to_attach(wav)
            if voice_claim:
                log.info("Speaker claim: %s (%.3f)", voice_claim[0], voice_claim[1])
        except Exception:
            pass
    if speculation is not None:
        speculation.voice_claim = voice_claim
    _last_turn_flags.clear()
    payload: dict = {"audio_base64": audio_b64_wav, "panel_id": PANEL_ID}
    if speculation is not None:
        payload["speculative"] = True
        payload["turn_id"] = speculation.turn_id
    if conversation:
        # Tell the server we're inside an open conversation so ender phrases
        # ("that's all", "goodbye") are honoured; outside one they never fire.
        payload["conversation"] = True
    # The sentinel is a plain object() and therefore TRUTHY — it must never
    # reach the unpack below, so collapse "never scored" to "no claim" here.
    # The sentinel keeps its meaning for the fallback hand-off only.
    _scored_claim = None if voice_claim is _CLAIM_UNSET else voice_claim
    if _scored_claim:
        # A claim + raw score; the server applies its own threshold.
        payload["voice_user_id"], payload["voice_score"] = _scored_claim
    _speaker = _speaker_field(_scored_claim)
    if _speaker is not None:
        payload["speaker"] = _speaker

    url = f"{ZOE_URL}/api/voice/turn_stream"
    _barge_in_requested.clear()
    _barge_stream_closed.clear()
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
        _set_turn_response(r)
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
            if speculation is not None and obj.get("speculation") == "gated":
                speculation.gated = True
                continue
            if speculation is not None and ("full_audio" in obj or "chunk" in obj) \
                    and not speculation.gated:
                # Audio on a speculative stream that never carried the gated
                # ack: the server's flag is off and it answered the prefix as an
                # ordinary turn (rollback / one-sided rollout), early or late.
                # Fail closed: play nothing (the user may still be talking),
                # never re-POST (the prefix was processed), stop speculating.
                log.error("speculation: server is NOT gating (audio without ack) turn_id=%s — "
                          "playing nothing; speculation disabled until restart. Set ZOE_SPECULATIVE_TURN "
                          "on the server or off on this panel.", speculation.turn_id)
                speculation.ungated = True
                _speculation_disabled.set()
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
                if obj.get("cancelled") and speculation is not None:
                    # The server dropped the speculative turn: nothing was and
                    # nothing will be played on this stream. The caller runs
                    # the normal turn on the full recording.
                    speculation.cancelled = True
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
        _set_turn_response(None)
    except requests.exceptions.SSLError:
        raise
    except Exception as exc:
        _set_turn_response(None)
        # If we've already started speaking, re-running the blocking turn would
        # double-play (user hears a fragment, then the whole reply again). Only
        # fall back when nothing has played yet; otherwise let the partial reply
        # stand and finish draining what's queued.
        if played_any:
            if _barge_stream_closed.is_set():
                log.info("Barge-in committed: turn stream closed (%s).", type(exc).__name__)
            else:
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
        if speculation is not None:
            # Died before the server acknowledged it: nothing was processed, so
            # the FULL recording may run as a normal turn — but from the caller,
            # never a blocking re-POST of this prefix from inside the thread.
            speculation.cancelled = True
            log.warning("turn_stream (speculative) failed with no server response (%s) — cancelled", exc)
            return False
        log.warning("turn_stream failed with no server response (%s) — falling back to blocking turn", exc)
        # Pass the claim we already scored: re-scoring here would append a
        # second shadow row + journal line for this one spoken turn.
        return _do_single_turn(pa, wav, prompt_on_empty=prompt_on_empty,
                               voice_claim=voice_claim)

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

    if speculation is not None and speculation.ungated:
        return False
    if speculation is not None and speculation.cancelled:
        log.info("speculation: cancelled turn_id=%s transcript=%r — caller runs the normal turn",
                 speculation.turn_id, transcript[:80])
        return False
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


def _do_single_turn(pa: pyaudio.PyAudio, wav: bytes, *, prompt_on_empty: bool = True,
                    conversation: bool = False, voice_claim: object = _CLAIM_UNSET) -> bool:
    """Process one recorded WAV: combined STT+LLM+TTS via /api/voice/turn.

    Returns True if audio was played (eligible for follow-up listening).
    Uses a single HTTP round-trip instead of separate transcribe + command calls.

    `voice_claim` lets a caller hand over a claim it already scored. The stream
    path falls back here on error, and re-scoring would append a SECOND shadow
    metrics row (and journal line) for one spoken turn — inflating the counts
    and giving one utterance two seqs, which breaks the per-seq FA/FR labelling.
    The sentinel distinguishes "not scored yet" from a scored no-match (None).
    """
    import time as _time
    _t_pi_start = _time.monotonic()

    audio_b64_wav = base64.b64encode(wav).decode()
    _t_encode = _time.monotonic() - _t_pi_start

    _t_vid_start = _time.monotonic()
    if voice_claim is _CLAIM_UNSET:
        voice_claim = None
        try:
            voice_claim = _speaker_claim_to_attach(wav)
            if voice_claim:
                log.info("Speaker claim: %s (%.3f)", voice_claim[0], voice_claim[1])
        except Exception:
            pass
    _t_vid = _time.monotonic() - _t_vid_start

    turn_payload: dict = {"audio_base64": audio_b64_wav, "panel_id": PANEL_ID}
    if voice_claim:
        # A claim + raw score; the server applies its own threshold.
        turn_payload["voice_user_id"], turn_payload["voice_score"] = voice_claim
    _speaker = _speaker_field(voice_claim)
    if _speaker is not None:
        turn_payload["speaker"] = _speaker

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
            "Recorded follow-up: duration=%.2fs chunks=%d stop=%s endpoint=%s tail=%s silence_timeout=%.2fs",
            duration_s, len(frames), stop_reason, endpointer.mode, endpointer.tail_rule or "-",
            SILENCE_TIMEOUT_S,
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


def _follow_up_source(pa: pyaudio.PyAudio, seed: "bytes | None", *, beep: bool = True,
                      window_s: float | None = None) -> "bytes | None":
    """The next follow-up recording: a committed barge-in's seed (the words the
    user said over Zoe — no beep, no fresh listen), else beep + listen."""
    if seed is not None:
        log.info("Follow-up seeded by barge-in (%d bytes) — no beep", len(seed))
        _recording_active.set()
        return seed
    threading.Thread(target=_notify_wake_background, daemon=True, name="followup-notify").start()
    if beep:
        play_follow_up_beep()
    _recording_active.set()
    return _follow_up_listen(pa, window_s=window_s)


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
    # B1.1: only the streaming turn has the server-side gate; the endpointer
    # itself never fires unless ZOE_SPECULATIVE_TURN is on and VAD mode is up.
    _spec = _SpeculativeTurn(pa) if _speculation_available() else None
    try:
        try:
            wav = record_command(pa, stream=wake_stream, speculation=_spec)
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
            if _spec is not None and _spec.fired:
                played_audio = _spec.finish(wav)
                if _spec.cancelled:
                    # Speculation dropped: run the FULL recording as a normal
                    # turn, handing over the claim the speculative pass scored.
                    played_audio = _turn_fn(pa, wav, voice_claim=_spec.voice_claim)
            else:
                played_audio = _turn_fn(pa, wav)
        finally:
            _monitor.stop()
        seed_wav = _monitor.take_seed()

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
                conv_beep = CONV_BEEP == "every" or (CONV_BEEP == "first" and not conv_beeped)
                conv_wav = _follow_up_source(pa, seed_wav, beep=conv_beep, window_s=CONV_WINDOW_S)
                conv_beeped = conv_beeped or (conv_beep and seed_wav is None)
                seed_wav = None
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
                seed_wav = _monitor.take_seed()
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
            log.info("Follow-up listening (turn %d/%d, %.1fs window)...", follow_ups_done + 1, FOLLOW_UP_MAX_TURNS, FOLLOW_UP_LISTEN_S)
            follow_wav = _follow_up_source(pa, seed_wav)
            seed_wav = None
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
            seed_wav = _monitor.take_seed()
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
        # The playback result, not "TTS arrived": a failed player must not be
        # ACKed as heard (the poller ACKs only a True here).
        played = play_audio_b64(audio_b64, resp.get("content_type", "audio/wav"))
    finally:
        _recording_active.clear()
        _ignore_wake_until = time.monotonic() + POST_PLAY_COOLDOWN_S
    if not played:
        log.warning("announce %s: playback failed — not acknowledged", ann.get("id", "?"))
    return played


def _post_played_ack(ann_id: str) -> bool:
    """One ACK attempt; True once the server has the ACK (or will never take it)."""
    resp = _api_post(f"/api/voice/announcements/{ann_id}/played", {}, timeout=5, retries=0)
    return bool(resp.get("ok"))


def _ack_announcement(ann: dict) -> None:
    """Tell zoe-data the claimed announcement was PLAYED (its claim alone is not
    proof: TTS or playback can fail after the claim).

    Bounded retries (``zoe_voice_announce.ACK_RETRY_DELAYS_S``: 3 attempts over
    ~10 s) on a background thread, so a slow or restarting zoe-data never delays
    the next announcement. Never raises."""
    ann_id = str(ann.get("id") or "").strip()
    if not ann_id or _announce_logic is None:
        return
    threading.Thread(
        target=_announce_logic.ack_with_retries,
        kwargs={"post": _post_played_ack, "ann_id": ann_id, "logger": log},
        daemon=True, name=f"announce-ack-{ann_id[:8]}",
    ).start()


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
        ack=_ack_announcement,
    )
    log.info("Announce poll thread started (interval=%.1fs)", ANNOUNCE_POLL_S)
    poller.run(_shutdown.wait)
    log.info("Announce poll thread stopped.")


_daemon_started_at = time.time()


# Shared Event that orb-tap /activate can set to trigger a wake sequence.
_orb_activate_event = threading.Event()


def _activate_request_allowed(host_header: "str | None", origin_header: "str | None") -> bool:
    """May this request start a recording via POST /activate?

    Pi: always (byte-identical to before; the panel's LAN is trusted). Mac: the
    daemon listens on loopback, but ANY web page can POST there (the touch page does
    exactly that, mode:no-cors), and a rebinding page can reach it under its own
    name. So require a loopback Host, and, when the browser sent an Origin, the
    configured ZOE_URL origin. No Origin (curl, the shell) is allowed: a page cannot
    omit it on a cross-origin POST."""
    if PANEL_PLATFORM != "mac":
        return True
    from urllib.parse import urlsplit
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        hostname = host[1:].split("]")[0]
    elif host.count(":") == 1:
        hostname = host.split(":")[0]
    else:
        hostname = host
    if hostname not in ("127.0.0.1", "localhost", "::1"):
        return False
    if origin_header is None:
        return True
    want = urlsplit(ZOE_URL)
    got = urlsplit(origin_header.strip())
    return bool(want.scheme and want.netloc and (got.scheme, got.netloc.lower()) == (want.scheme, want.netloc.lower()))


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
            if not _activate_request_allowed(self.headers.get("Host"), self.headers.get("Origin")):
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
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

        srv = _ReuseAddrTcp((HEALTH_BIND, HEALTH_PORT), _HealthHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        log.info("Health endpoint: http://%s:%d/health", HEALTH_BIND or "0.0.0.0", HEALTH_PORT)
    except Exception as e:
        log.warning("Could not start health server on port %d: %s", HEALTH_PORT, e)


def main():
    global _INPUT_DEVICE_INDEX, _last_wake_at

    if _CF_ACCESS_REFUSED:
        log.error("Refusing to start: fix ZOE_URL (public https tunnel host) or unset CF_ACCESS_CLIENT_ID/SECRET.")
        sys.exit(1)
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
    oww_framework = _PLATFORM.wakeword_framework()  # "onnx" on the Pi, always
    # The custom model is a framework-specific file: hey_zoe.onnx for ONNX (the Pi),
    # hey_zoe.tflite when the Mac runs the TFLite backend.
    custom_name = "hey_zoe.onnx" if oww_framework == "onnx" else "hey_zoe." + oww_framework
    custom_model = os.path.join(os.path.dirname(__file__), custom_name)
    wake_phrase = "Hey Zoe"
    # Optional Speex NS support: only enable if dependency is available.
    oww_kwargs = {"inference_framework": oww_framework}
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
            "Custom %s not found — using bundled 'hey_jarvis'. "
            "Say clearly: **Hey Jarvis** (not Hey Zoe). Place %s in %s to change.",
            custom_name, custom_name, os.path.dirname(__file__),
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

    # Pre-warm resemblyzer end-to-end in background so first command isn't
    # delayed, and pull the speaker-profile cache (disk copy first so a server
    # outage doesn't blind local matching, then a fresh sync).
    if SPEAKER_ID_ENABLED:
        threading.Thread(target=_speaker_id_warmup, daemon=True, name="resemblyzer-warmup").start()

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
                # Stamped with the chunk's capture START so the barge thread can
                # drop anything captured before playback began.
                _BARGE_QUEUE.put_nowait((time.monotonic() - _CHUNK_S, audio_chunk))
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
        # Background shadow scorers are daemon threads: without this bounded
        # drain, a restart right after a turn would drop its row + journal line.
        _drain_shadow_scoring()
        log.info("Voice daemon stopped.")


if __name__ == "__main__":
    main()
