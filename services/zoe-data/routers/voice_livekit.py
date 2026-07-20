"""voice_livekit.py — Server-side LiveKit agent for voice.html?mode=livekit.

Joins the "zoe-voice" LiveKit room as the "zoe-agent" participant, then runs
server-side energy VAD on the incoming audio track to detect when the user has
finished speaking.  No button press required — always listening.

Pipeline per participant:
  IDLE → (speech energy detected) → LISTENING → (600ms silence) → PROCESSING
  → (STT → LLM → TTS, audio sent via data channel) → COOLDOWN
  → (playback_done from browser OR 3s timeout) → IDLE

PTT fallback: if the browser sends ptt_start / ptt_stop the old PTT path is
still honoured — VAD only fires when no ptt_start has been received for this
participant since their last IDLE state.

Barge-in (ZOE_VOICE_BARGE_IN=1, default OFF): frames keep flowing through a
per-participant Silero VAD during PROCESSING/COOLDOWN; sustained speech cancels
the in-flight pipeline, sends {"type": "stop_playback"} to the browser, and
seeds LISTENING with the interrupting speech. When the flag is off (or the
Silero model is unavailable) the legacy RMS energy VAD runs unchanged.

Data channel messages sent to browser:
  {"type": "state",      "state": "listening"|"thinking"|"responding"|"ambient"}
  {"type": "transcript", "role": "user"|"zoe", "text": "..."}
  {"type": "audio",      "audio_base64": "...", "content_type": "audio/wav"}
  {"type": "stop_playback"}   — barge-in: stop TTS playback immediately
  {"type": "done"}

Data channel messages accepted from browser:
  {"type": "identify",       "user_id": "...", "session_id": "..."}
  {"type": "ptt_start"}       — explicit PTT start (disables VAD for this turn)
  {"type": "ptt_stop"}        — explicit PTT stop (triggers processing)
  {"type": "playback_done"}   — browser finished playing audio, re-enable listening

Requires: pip install livekit>=0.14  (livekit-api is separate, not needed here)
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import math
import os
import struct
import tempfile
import time
import uuid
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ── Agent configuration ───────────────────────────────────────────────────────
# Internal host:port — the agent connects server-to-server, not through nginx.
_LIVEKIT_INTERNAL_URL = "ws://127.0.0.1:7880"
_ROOM_NAME = "zoe-voice"
_AGENT_IDENTITY = "zoe-agent"

# ── VAD configuration (tunable via env) ──────────────────────────────────────
# RMS energy threshold on int16 samples.  Ambient silence is typically 50–200;
# soft speech ~600–1200; normal speech ~1000–4000.
_VAD_ENERGY_THRESHOLD = int(os.environ.get("ZOE_LK_ENERGY_THRESHOLD", "400"))
# How many consecutive below-threshold frames (each ~30ms) trigger end-of-speech.
_VAD_SILENCE_FRAMES = int(os.environ.get("ZOE_LK_SILENCE_FRAMES", "20"))  # ~600ms
# Minimum number of above-threshold frames to count as real speech (not noise).
_VAD_MIN_SPEECH_FRAMES = int(os.environ.get("ZOE_LK_MIN_SPEECH_FRAMES", "5"))  # ~150ms
# Seconds to wait in COOLDOWN before auto-returning to IDLE if no playback_done.
_COOLDOWN_TIMEOUT_S = float(os.environ.get("ZOE_LK_COOLDOWN_TIMEOUT_S", "4.0"))

# ── Barge-in / Silero VAD (flag-gated, default OFF) ──────────────────────────
# Silero hop size is 512 samples @16kHz — 32ms per completed hop (voice_vad.HOP_MS;
# duplicated here so this module never imports voice_vad/numpy at import time).
_SILERO_HOP_MS = 32.0


def _barge_in_enabled() -> bool:
    """ZOE_VOICE_BARGE_IN — interruptible conversation. Read from os.environ on
    every frame (like the other opt-in flags here) so a service restart after an
    env flip is all it takes; default OFF keeps today's half-duplex behaviour."""
    return os.environ.get("ZOE_VOICE_BARGE_IN", "0").strip().lower() in ("1", "true", "yes", "on")


def _barge_min_hops() -> int:
    """Sustained-speech gate: consecutive 32ms Silero hops ≥ threshold required to
    barge in (ZOE_BARGE_MIN_MS, default 192ms ≈ 6 hops — set from live replay of real voice: best 12-hop speech windows measured 6-7 hops). This duration gate — plus
    browser echoCancellation — is what stops Zoe's own TTS residual from
    triggering an interruption."""
    try:
        ms = float(os.environ.get("ZOE_BARGE_MIN_MS", "192"))
    except (TypeError, ValueError):
        ms = 192.0
    return max(1, math.ceil(ms / _SILERO_HOP_MS))


def _barge_speech_threshold() -> float:
    """Silero prob at/above which a hop counts as speech FOR BARGE COUNTING
    (ZOE_BARGE_SPEECH_THRESHOLD, default 0.30). Deliberately lower than the
    IDLE/LISTENING threshold (0.5): live replay of Jason's real voice showed
    natural speech dips below 0.5 between syllables (per-hop p90 ≈ 0.4-0.6),
    while the measured noise floor maxes ≈ 0.31 — and the windowed duration
    gate below still requires ~200ms of such hops, which noise never sustains."""
    try:
        return float(os.environ.get("ZOE_BARGE_SPEECH_THRESHOLD", "0.30"))
    except (TypeError, ValueError):
        return 0.30


def _barge_window_hops() -> int:
    """Rolling-window size for barge counting: 2x the required speech hops
    (~384ms at defaults), so speech with natural micro-dips still accumulates."""
    return _barge_min_hops() * 2


def _smart_turn_enabled() -> bool:
    """ZOE_SMART_TURN_ENABLED — end-of-turn model instead of a bare silence
    window (V2 endpointing). Only consulted on the barge-in (Silero) path; when
    OFF, or when the model is unavailable, the silence window ends the turn
    exactly as before."""
    return os.environ.get("ZOE_SMART_TURN_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _smart_turn_threshold() -> float:
    """End-of-turn probability at/above which the turn ends (ZOE_SMART_TURN_THRESHOLD)."""
    try:
        return float(os.environ.get("ZOE_SMART_TURN_THRESHOLD", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def _smart_turn_max_checks() -> int:
    """Bound on incomplete-verdict extensions per turn (ZOE_SMART_TURN_MAX_CHECKS,
    default 2). After this many "still mid-thought" verdicts the turn ends anyway
    — the model can extend a pause, never hang a conversation."""
    try:
        return max(1, int(os.environ.get("ZOE_SMART_TURN_MAX_CHECKS", "2")))
    except (TypeError, ValueError):
        return 2

_VOICE_HEALTH: dict = {
    "status": "starting",
    "backend": None,
    "connected": False,
    "participant_identity": _AGENT_IDENTITY,
    "connection_count": 0,
    "reconnect_count": 0,
    "audio_tracks": 0,
    "pipeline_successes": 0,
    "pipeline_failures": 0,
    "playback_completions": 0,
    "barge_ins": 0,
    "last_stage": "startup",
    "last_connected_at": None,
    "last_disconnected_at": None,
    "last_error": None,
    "stage_latency_ms": {},
}
_INITIAL_VOICE_HEALTH = copy.deepcopy(_VOICE_HEALTH)
_agent_running = False

# ── On-demand lifecycle ───────────────────────────────────────────────────────
# When ZOE_LIVEKIT_ONDEMAND=true (default) the LiveKit container is left stopped
# at boot and started on the first /livekit-token request, then stopped again
# after ZOE_LIVEKIT_IDLE_TIMEOUT_S of no participants.  This keeps the ~560MB
# WebRTC server out of memory except while a voice page is actually in use.
_CONTAINER_NAME = os.environ.get("ZOE_LIVEKIT_CONTAINER", "livekit").strip() or "livekit"
_agent_task: Optional["asyncio.Task"] = None
_idle_task: Optional["asyncio.Task"] = None
_cooldown_task: Optional["asyncio.Task"] = None
_lifecycle_lock: Optional["asyncio.Lock"] = None
_last_activity: float = 0.0
_active_participant_sids: set = set()
# Flipped True once the native livekit-ffi AudioStream is proven broken on this
# host (e.g. the Jetson Tegra FFI backend), so the agent loop switches permanently
# to the aiortc backend — sticky even across agent-loop restarts in this process.
_force_aiortc: bool = False


def _ondemand_enabled() -> bool:
    return os.environ.get("ZOE_LIVEKIT_ONDEMAND", "true").strip().lower() == "true"


def _idle_timeout_s() -> float:
    try:
        return float(os.environ.get("ZOE_LIVEKIT_IDLE_TIMEOUT_S", "300"))
    except (TypeError, ValueError):
        return 300.0


def _get_lifecycle_lock() -> "asyncio.Lock":
    global _lifecycle_lock
    if _lifecycle_lock is None:
        _lifecycle_lock = asyncio.Lock()
    return _lifecycle_lock


def note_voice_activity() -> None:
    """Mark that voice is in use, so the idle reaper holds off."""
    global _last_activity
    _last_activity = time.monotonic()


async def _docker_cmd(*args: str, timeout: float = 20.0) -> tuple:
    """Run `docker <args>` off the event loop; returns (returncode, combined_output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:  # docker not on PATH, etc.
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return 124, "timeout"
    return proc.returncode or 0, (out or b"").decode(errors="replace").strip()


async def _container_running() -> bool:
    rc, out = await _docker_cmd("inspect", "-f", "{{.State.Running}}", _CONTAINER_NAME, timeout=10)
    return rc == 0 and out.strip() == "true"


async def _wait_port(host: str, port: int, timeout: float) -> bool:
    """Poll a TCP port until it accepts connections or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            await asyncio.sleep(0.25)
    return False


async def ensure_livekit_started(wait_ready: float = 8.0) -> bool:
    """Start the LiveKit container + agent on demand. Returns True if :7880 is reachable.

    Idempotent and safe to call on every /livekit-token request.  No-op (just a
    reachability probe) when on-demand mode is disabled — boot already started the
    always-on agent in that case.
    """
    note_voice_activity()
    if not os.environ.get("LIVEKIT_API_KEY", "").strip():
        return False
    if not _ondemand_enabled():
        return await _wait_port("127.0.0.1", 7880, 1.0)

    global _agent_task, _idle_task
    async with _get_lifecycle_lock():
        if not await _container_running():
            logger.info("LiveKit on-demand: starting container '%s'", _CONTAINER_NAME)
            rc, out = await _docker_cmd("start", _CONTAINER_NAME, timeout=20)
            if rc != 0:
                logger.warning(
                    "LiveKit on-demand: docker start '%s' failed rc=%s out=%s",
                    _CONTAINER_NAME, rc, out,
                )
                return False
        if _agent_task is None or _agent_task.done():
            _agent_task = asyncio.create_task(_agent_loop(), name="livekit_agent")
            logger.info("LiveKit on-demand: agent task started")
        if _idle_task is None or _idle_task.done():
            _idle_task = asyncio.create_task(_idle_monitor(), name="livekit_idle_monitor")
    ready = await _wait_port("127.0.0.1", 7880, wait_ready)
    note_voice_activity()
    return ready


async def stop_livekit_ondemand(reason: str = "idle") -> None:
    """Cancel the agent loop and stop the LiveKit container."""
    global _agent_task, _idle_task, _cooldown_task
    async with _get_lifecycle_lock():
        task = _agent_task
        _agent_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=8.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass
        # Cancel the cooldown watchdog too — it's a forever `while True: sleep(1)`
        # loop that the agent-task cancel above does NOT reach (separate task).
        cooldown = _cooldown_task
        _cooldown_task = None
        if cooldown is not None and not cooldown.done():
            cooldown.cancel()
        # Tear the idle monitor down too, so a later ensure_livekit_started()
        # recreates it. Skip when we're being called *from* the monitor itself
        # (the normal _reap_if_idle path) — that task returns naturally and must
        # not cancel itself mid-await.
        idle = _idle_task
        if idle is not None and idle is not asyncio.current_task() and not idle.done():
            idle.cancel()
            _idle_task = None
        _active_participant_sids.clear()
        logger.info("LiveKit on-demand: stopping container '%s' (reason=%s)", _CONTAINER_NAME, reason)
        await _docker_cmd("stop", _CONTAINER_NAME, timeout=30)
        _health_update(status="stopped", connected=False)


_IDLE_CHECK_INTERVAL_S = 30.0


async def _reap_if_idle() -> bool:
    """One idle-check tick.  Returns True when the monitor should stop looping
    (either it reaped the container or the container is already gone)."""
    if not _ondemand_enabled():
        return False
    idle_for = time.monotonic() - _last_activity
    if _active_participant_sids or idle_for < _idle_timeout_s():
        return False
    if await _container_running():
        # Re-check after the await: _container_running() yields to the event loop,
        # and a /livekit-token request could have started the container + bumped
        # activity (or a participant could have connected) in that window. Don't
        # reap a now-active room.
        idle_for = time.monotonic() - _last_activity
        if _active_participant_sids or idle_for < _idle_timeout_s():
            return False
        logger.info(
            "LiveKit on-demand: idle %.0fs (no participants) → stopping", idle_for
        )
        await stop_livekit_ondemand(reason="idle")
    return True


async def _idle_monitor() -> None:
    """Reap the LiveKit container after ZOE_LIVEKIT_IDLE_TIMEOUT_S of no participants.

    Runs only while a container is up; exits after stopping it (ensure_livekit_started
    restarts the monitor on the next request).
    """
    while True:
        try:
            await asyncio.sleep(_IDLE_CHECK_INTERVAL_S)
            if await _reap_if_idle():
                return
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("LiveKit idle monitor error (non-fatal): %s", exc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _health_update(**changes) -> None:
    _VOICE_HEALTH.update(changes)


def _record_voice_connected() -> None:
    _VOICE_HEALTH["connection_count"] += 1
    if _VOICE_HEALTH["connection_count"] > 1:
        _VOICE_HEALTH["reconnect_count"] += 1
    _health_update(
        status="connected",
        connected=True,
        last_connected_at=_utc_now(),
        last_error=None,
    )


def get_voice_health() -> dict:
    health = copy.deepcopy(_VOICE_HEALTH)
    idle_age = (time.monotonic() - _last_activity) if _last_activity else None
    health["ondemand"] = {
        "enabled": _ondemand_enabled(),
        "idle_timeout_s": _idle_timeout_s(),
        "idle_age_s": round(idle_age, 1) if idle_age is not None else None,
        "active_participants": sorted(_active_participant_sids),
        "agent_task_running": _agent_task is not None and not _agent_task.done(),
        "idle_monitor_running": _idle_task is not None and not _idle_task.done(),
        "cooldown_watchdog_running": _cooldown_task is not None and not _cooldown_task.done(),
        "force_aiortc": _force_aiortc,
    }
    return health


def reset_voice_health_for_tests() -> None:
    _VOICE_HEALTH.clear()
    _VOICE_HEALTH.update(copy.deepcopy(_INITIAL_VOICE_HEALTH))


class _ParticipantState(Enum):
    IDLE = auto()        # waiting for speech to start
    LISTENING = auto()   # speech detected, accumulating frames
    PROCESSING = auto()  # STT → LLM → TTS running
    COOLDOWN = auto()    # audio sent, waiting for playback_done


def _rms(frame_data: bytes) -> float:
    """Compute RMS energy of a raw int16-LE PCM frame."""
    if len(frame_data) < 2:
        return 0.0
    n = len(frame_data) // 2
    # Unpack all int16 samples at once
    samples = struct.unpack_from(f"<{n}h", frame_data)
    return math.sqrt(sum(s * s for s in samples) / n)


def _mint_agent_token() -> str:
    """Mint a LiveKit JWT for the server-side agent."""
    import jwt as _jwt

    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set in .env")

    now = int(time.time())
    payload = {
        "exp": now + 86400,
        "iss": api_key,
        "sub": _AGENT_IDENTITY,
        "jti": uuid.uuid4().hex,
        "video": {
            "roomJoin": True,
            "room": _ROOM_NAME,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    return _jwt.encode(payload, api_secret, algorithm="HS256")


def _pcm_frames_to_wav(frames: list[bytes], sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Concatenate raw int16-LE PCM frames and wrap in a WAV header."""
    raw = b"".join(frames)
    bits = 16
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_len = len(raw)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_len, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate,
        byte_rate, block_align, bits,
        b"data", data_len,
    )
    return header + raw


async def _send_data(local_participant, payload: dict) -> None:
    """Broadcast a JSON data message to all participants in the room."""
    try:
        await local_participant.publish_data(json.dumps(payload).encode(), reliable=True)
    except Exception as exc:
        logger.debug("LiveKit data send error: %s", exc)


def _livekit_fast_tiers_enabled() -> bool:
    """LiveKit's opt-in deterministic fast tier — OFF unless explicitly enabled.

    LiveKit is conversation-mode (plan §4.5): brain-first is the default so it
    keeps feeling like a real person. Enabling this answers only the unambiguous
    factual subset via the shared core.
    """
    return os.environ.get("ZOE_LIVEKIT_FAST_TIERS", "0").strip().lower() in ("1", "true", "yes", "on")


def _livekit_stream_tts_enabled() -> bool:
    """P-W1.3 sentence-streamed TTS — OFF unless explicitly enabled.

    When ON, the reply is split into sentences and each is synthesized and sent
    as its own data message (same payload shape as today plus ``seq``/``final``)
    so the browser gets first-audio at first-sentence, like the /ws/voice/ lane.
    """
    return os.environ.get("ZOE_LIVEKIT_STREAM_TTS", "0").strip().lower() in ("1", "true", "yes", "on")


async def _stream_sentence_audio(local_participant, text: str, user_id: str) -> None:
    """Synthesize `text` sentence-by-sentence and send one audio data message
    per sentence, tagged with ``seq`` (0-based) and ``final``.

    Cancellation (#1051 barge-in cancels the pipeline task) is honoured BETWEEN
    sentences: the explicit ``asyncio.sleep(0)`` checkpoint at the top of each
    iteration guarantees a pending cancel lands before the next synthesis even
    if ``_synth`` itself never suspends. Raises on synthesis failure so the
    caller's existing text-fallback path handles it.
    """
    from routers.voice_tts import _split_sentences, synthesize as _synth

    sentences = _split_sentences(text)
    last = len(sentences) - 1
    sent_any = False
    for seq, sentence in enumerate(sentences):
        await asyncio.sleep(0)  # cancel-token checkpoint between sentences
        try:
            tts_resp = await _synth({"text": sentence}, caller={"source": "livekit", "user_id": user_id})
        except Exception:
            if not sent_any:
                raise  # nothing spoken yet — caller's existing text fallback handles it
            # Mid-stream failure with audio already played: do NOT let the
            # caller re-send the whole reply as text (it would duplicate what
            # was heard). Send only the unspoken remainder as text, plus an
            # empty final-flagged marker so the client reconciles the turn.
            logger.warning(
                "LiveKit streamed TTS failed mid-reply (sentence %d/%d)", seq + 1, last + 1
            )
            await _send_data(local_participant, {
                "type": "text", "content": " ".join(sentences[seq:]),
            })
            await _send_data(local_participant, {
                "type": "audio", "audio_base64": "", "content_type": "audio/wav",
                "seq": seq, "final": True,
            })
            return
        await _send_data(local_participant, {
            "type": "audio",
            "audio_base64": base64.b64encode(tts_resp.body).decode("ascii"),
            "content_type": tts_resp.media_type,
            "seq": seq,
            "final": seq == last,
        })
        sent_any = True


async def _maybe_fast_tier(transcript: str, user_id: str, session_id: str) -> Optional[str]:
    """Return a sub-second fast-tier reply for `transcript`, or None → the brain.

    Returns None (defer to the brain) when the fast tier is disabled, finds no
    confident match, or errors — so a conversational/contextual turn always reaches
    the brain. Never raises.
    """
    if not _livekit_fast_tiers_enabled():
        return None
    try:
        import fast_tiers

        result = await fast_tiers.resolve(transcript, user_id, session_id, channel="livekit")
        if result is not None and getattr(result, "reply", ""):
            return result.reply
    except Exception as exc:  # never let the fast tier break a turn
        logger.debug("LiveKit fast-tier resolve failed (non-fatal): %s", exc)
    return None


async def _prewarm_brain(user_id: str, session_id: str) -> None:
    """Spawn the brain worker on speech-start (VAD IDLE→LISTENING) so it's warm by
    end-of-speech — LiveKit's analogue of the panel's wake-word prewarm. Best-effort.
    """
    if os.environ.get("ZOE_BRAIN_PREWARM_ON_WAKE", "1").strip().lower() not in ("1", "true", "yes", "on"):
        return
    try:
        import zoe_core_client
        await zoe_core_client.prewarm(user_id or "guest", session_id or "default")
    except Exception as exc:
        logger.debug("LiveKit brain prewarm failed (non-fatal): %s", exc)


async def _run_pipeline(local_participant, frames: list[bytes], user_id: str, session_id: str) -> None:
    """STT → LLM → TTS pipeline, called once end-of-speech is detected.

    Cancellation-safe (barge-in cancels this task): every ``except Exception``
    below deliberately lets ``asyncio.CancelledError`` (a BaseException)
    propagate, the STT temp file is unlinked in a ``finally``, and no counters
    are mutated on the cancel path — so a cancelled turn leaks no state.
    """
    pipeline_started = time.monotonic()
    await _send_data(local_participant, {"type": "state", "state": "thinking"})

    # ── STT ──────────────────────────────────────────────────────────────────
    transcript = ""
    stage_started = time.monotonic()
    _health_update(last_stage="stt", last_error=None)
    try:
        wav_bytes = _pcm_frames_to_wav(frames)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(wav_bytes)
            tmp_path = tf.name
        try:
            from routers.voice_tts import _transcribe_audio
            transcript = await _transcribe_audio(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        _VOICE_HEALTH["pipeline_failures"] += 1
        _health_update(last_stage="stt_failed", last_error=str(exc)[:240])
        logger.warning("LiveKit STT failed: %s", exc)
        await _send_data(local_participant, {"type": "state", "state": "ambient"})
        return
    _VOICE_HEALTH["stage_latency_ms"]["stt"] = round(
        (time.monotonic() - stage_started) * 1000, 1
    )

    if not transcript:
        _VOICE_HEALTH["pipeline_failures"] += 1
        _health_update(last_stage="stt_empty", last_error="empty transcript")
        # P-F3: audible feedback instead of a silent return to ambient. Uses the
        # same synthesize path as the reply TTS below; any failure degrades to
        # today's behaviour (silent ambient) — never crash the loop.
        try:
            canned = "Sorry, I didn't catch that."
            from routers.voice_tts import synthesize as _synth
            tts_resp = await _synth(
                {"text": canned}, caller={"source": "livekit", "user_id": user_id}
            )
            await _send_data(local_participant, {"type": "transcript", "role": "zoe", "text": canned})
            await _send_data(local_participant, {
                "type": "audio",
                "audio_base64": base64.b64encode(tts_resp.body).decode("ascii"),
                "content_type": tts_resp.media_type,
            })
        except Exception as exc:
            logger.debug("LiveKit empty-transcript feedback failed: %s", exc)
        await _send_data(local_participant, {"type": "state", "state": "ambient"})
        return

    await _send_data(local_participant, {"type": "transcript", "role": "user", "text": transcript})

    try:
        from voice_presence import processing_ack_event

        ack_event = processing_ack_event()
        if ack_event:
            ack_text = str(ack_event.get("text") or "").strip()
            if ack_text:
                await _send_data(local_participant, {"type": "transcript", "role": "zoe", "text": ack_text})
            if ack_event.get("audio_base64"):
                await _send_data(local_participant, ack_event)
    except Exception as exc:
        logger.debug("LiveKit processing acknowledgement failed: %s", exc)

    # ── Deterministic fast tier (opt-in, OFF by default) ─────────────────────
    # LiveKit is a *conversation* mode (plan §4.5/§8.3): brain-first is the default
    # so it keeps feeling like a real person. Only when ZOE_LIVEKIT_FAST_TIERS is
    # enabled does the shared core answer the unambiguous factual subset; logic
    # lives in `_maybe_fast_tier` (unit-tested). Its own latency is tracked under
    # the "fast_tier" stage so "llm" is never mislabelled as ~0ms.
    fast_started = time.monotonic()
    _fast_reply = await _maybe_fast_tier(transcript, user_id, session_id)
    if _fast_reply is not None:
        _VOICE_HEALTH["stage_latency_ms"]["fast_tier"] = round(
            (time.monotonic() - fast_started) * 1000, 1
        )

    # ── LLM (skipped when the fast tier answered) ────────────────────────────
    response = "Sorry, I had trouble with that."
    llm_ok = True
    if _fast_reply is not None:
        response = _fast_reply
        _health_update(last_stage="fast_tier")
    else:
        stage_started = time.monotonic()
        _health_update(last_stage="llm")
        try:
            from brain_dispatch import brain_oneshot
            # P-F3: a hung brain must never wedge the pipeline — bound the call
            # and route a timeout through the same apology path as any LLM error.
            response = await asyncio.wait_for(
                brain_oneshot(transcript, session_id, user_id, voice_mode=True),
                timeout=float(os.environ.get("ZOE_LIVEKIT_BRAIN_TIMEOUT_S", "20")),
            )
        except asyncio.TimeoutError:
            llm_ok = False
            _VOICE_HEALTH["pipeline_failures"] += 1
            _health_update(last_stage="llm_timeout", last_error="brain_oneshot timed out")
            logger.error("LiveKit LLM timed out after ZOE_LIVEKIT_BRAIN_TIMEOUT_S")
        except Exception as exc:
            llm_ok = False
            _VOICE_HEALTH["pipeline_failures"] += 1
            _health_update(last_stage="llm_failed", last_error=str(exc)[:240])
            logger.error("LiveKit LLM error: %s", exc)
        _VOICE_HEALTH["stage_latency_ms"]["llm"] = round(
            (time.monotonic() - stage_started) * 1000, 1
        )

    await _send_data(local_participant, {"type": "state", "state": "responding"})
    await _send_data(local_participant, {"type": "transcript", "role": "zoe", "text": response})

    # ── TTS → send audio via data channel ────────────────────────────────────
    stage_started = time.monotonic()
    _health_update(last_stage="tts")
    try:
        if _livekit_stream_tts_enabled():
            await _stream_sentence_audio(local_participant, response, user_id)
        else:
            from routers.voice_tts import synthesize as _synth
            tts_resp = await _synth({"text": response}, caller={"source": "livekit", "user_id": user_id})
            await _send_data(local_participant, {
                "type": "audio",
                "audio_base64": base64.b64encode(tts_resp.body).decode("ascii"),
                "content_type": tts_resp.media_type,
            })
    except Exception as exc:
        _VOICE_HEALTH["pipeline_failures"] += 1
        _health_update(last_stage="tts_failed", last_error=str(exc)[:240])
        logger.warning("LiveKit TTS failed: %s", exc)
        await _send_data(local_participant, {"type": "text", "content": response})
    else:
        _VOICE_HEALTH["stage_latency_ms"]["tts"] = round(
            (time.monotonic() - stage_started) * 1000, 1
        )
        _VOICE_HEALTH["stage_latency_ms"]["total"] = round(
            (time.monotonic() - pipeline_started) * 1000, 1
        )
        if llm_ok:
            _VOICE_HEALTH["pipeline_successes"] += 1
            _health_update(last_stage="playback_pending", last_error=None)

    await _send_data(local_participant, {"type": "done"})


async def _run_text_pipeline(local_participant, message: str, user_id: str, session_id: str) -> None:
    """LLM -> TTS pipeline for text commands sent over the LiveKit data channel."""
    message = (message or "").strip()
    if not message:
        await _send_data(local_participant, {"type": "state", "state": "ambient"})
        return

    pipeline_started = time.monotonic()
    await _send_data(local_participant, {"type": "state", "state": "thinking"})
    await _send_data(local_participant, {"type": "transcript", "role": "user", "text": message})

    response = "Sorry, I had trouble with that."
    llm_ok = True
    stage_started = time.monotonic()
    _health_update(last_stage="llm", last_error=None)
    try:
        from brain_dispatch import brain_oneshot
        response = await brain_oneshot(message, session_id, user_id, voice_mode=True)
    except Exception as exc:
        llm_ok = False
        _VOICE_HEALTH["pipeline_failures"] += 1
        _health_update(last_stage="llm_failed", last_error=str(exc)[:240])
        logger.error("LiveKit text LLM error: %s", exc)
    _VOICE_HEALTH["stage_latency_ms"]["llm"] = round(
        (time.monotonic() - stage_started) * 1000, 1
    )

    await _send_data(local_participant, {"type": "state", "state": "responding"})
    await _send_data(local_participant, {"type": "transcript", "role": "zoe", "text": response})

    stage_started = time.monotonic()
    _health_update(last_stage="tts")
    try:
        if _livekit_stream_tts_enabled():
            await _stream_sentence_audio(local_participant, response, user_id)
        else:
            from routers.voice_tts import synthesize as _synth
            tts_resp = await _synth({"text": response}, caller={"source": "livekit", "user_id": user_id})
            await _send_data(local_participant, {
                "type": "audio",
                "audio_base64": base64.b64encode(tts_resp.body).decode("ascii"),
                "content_type": tts_resp.media_type,
            })
    except Exception as exc:
        _VOICE_HEALTH["pipeline_failures"] += 1
        _health_update(last_stage="tts_failed", last_error=str(exc)[:240])
        logger.warning("LiveKit text TTS failed: %s", exc)
        await _send_data(local_participant, {"type": "text", "content": response})
    else:
        _VOICE_HEALTH["stage_latency_ms"]["tts"] = round(
            (time.monotonic() - stage_started) * 1000, 1
        )
        _VOICE_HEALTH["stage_latency_ms"]["total"] = round(
            (time.monotonic() - pipeline_started) * 1000, 1
        )
        if llm_ok:
            _VOICE_HEALTH["pipeline_successes"] += 1
            _health_update(last_stage="playback_pending", last_error=None)

    await _send_data(local_participant, {"type": "done"})


def _schedule_pipeline(sid: str, ps: dict, local_participant, frames_snapshot: list) -> None:
    """Kick off the STT→LLM→TTS pipeline task for an utterance and track its
    handle in ``ps["pipeline_task"]`` (so barge-in can cancel it)."""
    task = asyncio.ensure_future(
        _run_pipeline(
            local_participant,
            frames_snapshot,
            ps.get("user_id", "guest"),
            ps.get("session_id", f"livekit-{sid[:8]}"),
        )
    )
    ps["pipeline_task"] = task
    task.add_done_callback(
        lambda _t, _sid=sid, _ps=ps: _on_pipeline_done(_sid, _ps)
    )


def _ensure_participant_vad(ps: dict):
    """Lazily create the per-participant Silero VAD stream. Returns None (and
    remembers the failure) when the model is unavailable → caller falls back to
    the legacy RMS path. Never raises."""
    if ps.get("vad_failed"):
        return None
    vad = ps.get("vad")
    if vad is None:
        try:
            import voice_vad
            vad = voice_vad.create_vad()
        except Exception as exc:  # onnxruntime/numpy missing, etc.
            logger.debug("Silero VAD init failed (non-fatal): %s", exc)
            vad = None
        if vad is None:
            ps["vad_failed"] = True
        else:
            ps["vad"] = vad
    return vad


def _end_turn(sid: str, ps: dict, local_participant) -> None:
    """LISTENING → PROCESSING: snapshot the utterance and launch the pipeline.
    Shared by the plain silence endpoint and the smart-turn-approved endpoint."""
    logger.debug(
        "LiveKit VAD [%s]: LISTENING → PROCESSING (silero, frames=%d)",
        sid[:8], len(ps["frames"]),
    )
    ps["state"] = _ParticipantState.PROCESSING
    frames_snapshot = list(ps["frames"])
    ps["frames"] = []
    ps["speech_count"] = 0
    ps["silence_count"] = 0
    ps["turn_checks"] = 0
    ps["turn_check_task"] = None
    _schedule_pipeline(sid, ps, local_participant, frames_snapshot)


async def _smart_turn_check(sid: str, ps: dict, local_participant) -> None:
    """Score end-of-turn on the buffered utterance (smart-turn v3, ~200ms on one
    CPU thread via to_thread) and either end the turn or extend the listen.

    Discards its verdict if the world moved on while scoring: a barge-in/reset
    (state left LISTENING) or resumed speech (silence_count went back to 0).
    Fail-open on any error — the turn ends, matching legacy behaviour."""
    import numpy as np

    import voice_turn

    prob = 1.0
    try:
        det = voice_turn.get_smart_turn()
        if det is not None:
            pcm = np.frombuffer(b"".join(ps["frames"]), dtype=np.int16)
            prob = await asyncio.to_thread(det.end_of_turn_prob, pcm)
    except Exception as exc:  # never take down the frame loop
        logger.warning("smart-turn [%s]: check failed (%s) — ending turn", sid[:8], exc)
    finally:
        ps["turn_check_task"] = None
    if ps.get("state") != _ParticipantState.LISTENING:
        return  # barged / reset while scoring — verdict is moot
    if ps.get("silence_count", 0) == 0:
        return  # speech resumed while scoring — keep listening
    if prob >= _smart_turn_threshold() or ps.get("turn_checks", 0) + 1 >= _smart_turn_max_checks():
        logger.debug("smart-turn [%s]: p=%.2f → end of turn", sid[:8], prob)
        _end_turn(sid, ps, local_participant)
    else:
        ps["turn_checks"] = ps.get("turn_checks", 0) + 1
        ps["silence_count"] = max(1, _VAD_SILENCE_FRAMES // 2)
        logger.debug(
            "smart-turn [%s]: p=%.2f → still mid-thought, extending listen (check %d/%d)",
            sid[:8], prob, ps["turn_checks"], _smart_turn_max_checks(),
        )


async def _handle_frame_barge_in(raw: bytes, sid: str, ps: dict, local_participant, vad) -> None:
    """Silero-VAD state machine for one incoming frame (ZOE_VOICE_BARGE_IN=1).

    Mirrors the legacy RMS state machine for IDLE/LISTENING (same frame/hop-count
    knobs → same ~150ms speech-start and ~600ms end-of-speech timing), and adds
    the full-duplex part: during PROCESSING and COOLDOWN frames keep flowing
    through Silero, and sustained speech (≥ _barge_min_hops consecutive 32ms hops
    at ≥ threshold) interrupts Zoe — cancel the pipeline, tell the browser to stop
    playback, and seed LISTENING with the interrupting speech so it isn't lost.
    """
    import voice_vad

    threshold = voice_vad.speech_threshold()
    probs = vad.process_hops(raw)
    state = ps["state"]

    if state == _ParticipantState.IDLE:
        # Buffer tentatively (a frame may complete no hop); silent hops discard.
        ps["frames"].append(raw)
        if not probs:
            return
        if max(probs) >= threshold:
            ps["speech_count"] = ps.get("speech_count", 0) + 1
            if ps["speech_count"] >= _VAD_MIN_SPEECH_FRAMES:
                ps["state"] = _ParticipantState.LISTENING
                ps["silence_count"] = 0
                ps["turn_checks"] = 0
                logger.debug(
                    "LiveKit VAD [%s]: IDLE → LISTENING (silero p=%.2f)",
                    sid[:8], max(probs),
                )
                await _send_data(local_participant, {"type": "state", "state": "listening"})
                # Warm the brain worker now so it's ready by end-of-speech.
                asyncio.ensure_future(_prewarm_brain(ps.get("user_id") or "guest", ps.get("session_id") or ""))
        else:
            ps["speech_count"] = 0
            ps["frames"] = []  # discard sub-threshold noise

    elif state == _ParticipantState.LISTENING:
        ps["frames"].append(raw)
        if not probs:
            return
        if max(probs) >= threshold:
            ps["silence_count"] = 0
        else:
            ps["silence_count"] = ps.get("silence_count", 0) + 1
            if ps["silence_count"] >= _VAD_SILENCE_FRAMES:
                if ps.get("turn_check_task") is not None:
                    return  # smart-turn verdict in flight — keep buffering
                # V2 endpointing: before ending the turn on silence alone, ask
                # the end-of-turn model whether the speaker is actually done
                # (a mid-thought pause extends the listen instead of cutting off).
                if _smart_turn_enabled():
                    import voice_turn
                    if voice_turn.get_smart_turn() is not None:
                        ps["turn_check_task"] = asyncio.ensure_future(
                            _smart_turn_check(sid, ps, local_participant)
                        )
                        return
                _end_turn(sid, ps, local_participant)

    elif state in (_ParticipantState.PROCESSING, _ParticipantState.COOLDOWN):
        # Full-duplex: keep listening while Zoe thinks/speaks. Count speech hops
        # in a ROLLING WINDOW (live replay of real voice proved a strictly-
        # consecutive counter never fires: natural speech dips between syllables
        # reset it — 0/6 real clips triggered). The duration gate stays: ≥
        # _barge_min_hops speech hops within a ~2x window (~200ms of speech in
        # ~384ms), which echo/TTS residual and the ≤0.31 noise floor never sustain.
        barge_th = _barge_speech_threshold()
        window = ps.setdefault("barge_window", [])
        for p in probs:
            window.append(p >= barge_th)
        max_window = _barge_window_hops()
        if len(window) > max_window:
            del window[: len(window) - max_window]
        # Buffer the rolling window of candidate frames (speech + hop-incomplete)
        # so the interruption seeds the next utterance; cap to the window span.
        ps["barge_frames"].append(raw)
        max_frames = max_window * 2  # 20ms frames vs 32ms hops → keep a bit extra
        if len(ps["barge_frames"]) > max_frames:
            del ps["barge_frames"][: len(ps["barge_frames"]) - max_frames]
        speech_hops = sum(window)

        if speech_hops >= _barge_min_hops():
            speech_ms = int(speech_hops * _SILERO_HOP_MS)
            # LISTENING first: _on_pipeline_done fires when the cancelled task
            # settles and must not flip us into COOLDOWN.
            ps["state"] = _ParticipantState.LISTENING
            ps["frames"] = list(ps["barge_frames"])
            ps["speech_count"] = 0
            ps["silence_count"] = 0
            ps["turn_checks"] = 0
            ps["barge_window"] = []
            ps["barge_frames"] = []
            ps["cooldown_deadline"] = 0.0
            pipeline_task = ps.get("pipeline_task")
            if state == _ParticipantState.PROCESSING and pipeline_task is not None and not pipeline_task.done():
                pipeline_task.cancel()
            _VOICE_HEALTH["barge_ins"] = _VOICE_HEALTH.get("barge_ins", 0) + 1
            logger.info(
                "LiveKit BARGE-IN [%s]: %s interrupted after %dms speech",
                sid[:8], state.name, speech_ms,
            )
            await _send_data(local_participant, {"type": "stop_playback"})
            await _send_data(local_participant, {"type": "state", "state": "listening"})
            asyncio.ensure_future(_prewarm_brain(ps.get("user_id") or "guest", ps.get("session_id") or ""))


async def _collect_audio_stream(
    track,
    sid: str,
    participant_state: dict,
    local_participant,
) -> None:
    """Background task: run energy VAD on incoming audio frames.

    Maintains per-participant state in `participant_state[sid]`:
      state        — _ParticipantState
      frames       — accumulated speech frames (cleared on PROCESSING)
      speech_count — consecutive above-threshold frames (resets on silence)
      silence_count— consecutive below-threshold frames (resets on speech)
      ptt_active   — True while a ptt_start has been received but not ptt_stop
      pipeline_task— the currently running asyncio.Task for the pipeline
      vad          — per-participant SileroVAD stream (barge-in flag only)
      barge_window — rolling per-hop speech verdicts while PROCESSING/COOLDOWN
      barge_frames — buffered interrupting-speech frames (seed the next turn)
    """
    global _force_aiortc
    audio_stream = None
    # Are we draining a native livekit-ffi track (vs the aiortc stand-in)? This makes
    # the native→aiortc fallback one-way: only a NATIVE failure forces a switch, so an
    # aiortc-track error can never force a redundant fallback or flap the backend.
    is_native = None  # resolved once the backend/track type is known
    try:
        # Support both livekit.rtc tracks (livekit-ffi) and aiortc tracks
        from livekit_aiortc import _RemoteAudioTrack as _AiortcTrack, make_audio_stream
        is_native = not isinstance(track, _AiortcTrack)
        if not is_native:
            audio_stream = make_audio_stream(track, sample_rate=16000, num_channels=1)
        else:
            # Native livekit-ffi AudioStream. On a half-broken FFI backend (e.g. the
            # Jetson Tegra kernel) this constructor itself can crash deep in the SDK
            # ("'NoneType' object has no attribute 'add_done_callback'"). Treat ANY
            # construction failure as "native audio is broken on this host": flip the
            # agent loop to the aiortc backend (instead of silently dying with the
            # room connected but no audio) and stop here.
            from livekit import rtc as lk_rtc  # type: ignore
            try:
                audio_stream = lk_rtc.AudioStream(track, sample_rate=16000, num_channels=1)
            except Exception as exc:
                _force_aiortc = True
                logger.warning(
                    "LiveKit native AudioStream construction failed for %s (%s) — "
                    "switching to the aiortc backend", sid, exc,
                )
                return
        async for frame_event in audio_stream:
            ps = participant_state.get(sid)
            if ps is None:
                break

            raw = bytes(frame_event.frame.data)

            # ── PTT override path ─────────────────────────────────────────
            if ps.get("ptt_active"):
                # Old PTT logic: just buffer frames, pipeline triggered by ptt_stop
                ps["frames"].append(raw)
                continue

            # ── Barge-in path (ZOE_VOICE_BARGE_IN=1 + Silero available) ───
            # Flag OFF (default) or model unavailable → the legacy RMS path
            # below runs exactly as before (frames ignored while busy).
            if _barge_in_enabled():
                vad = _ensure_participant_vad(ps)
                if vad is not None:
                    await _handle_frame_barge_in(raw, sid, ps, local_participant, vad)
                    continue

            energy = _rms(raw)
            state = ps["state"]

            # ── VAD path ──────────────────────────────────────────────────
            if state == _ParticipantState.IDLE:
                if energy >= _VAD_ENERGY_THRESHOLD:
                    ps["speech_count"] = ps.get("speech_count", 0) + 1
                    ps["frames"].append(raw)
                    if ps["speech_count"] >= _VAD_MIN_SPEECH_FRAMES:
                        ps["state"] = _ParticipantState.LISTENING
                        ps["silence_count"] = 0
                        logger.debug("LiveKit VAD [%s]: IDLE → LISTENING (energy=%.0f)", sid[:8], energy)
                        await _send_data(local_participant, {"type": "state", "state": "listening"})
                        # Warm the brain worker now so it's ready by end-of-speech.
                        asyncio.ensure_future(_prewarm_brain(ps.get("user_id") or "guest", ps.get("session_id") or ""))
                else:
                    ps["speech_count"] = 0
                    ps["frames"] = []  # discard sub-threshold noise

            elif state == _ParticipantState.LISTENING:
                ps["frames"].append(raw)
                if energy >= _VAD_ENERGY_THRESHOLD:
                    ps["silence_count"] = 0
                else:
                    ps["silence_count"] = ps.get("silence_count", 0) + 1
                    if ps["silence_count"] >= _VAD_SILENCE_FRAMES:
                        # End of speech — kick off pipeline
                        logger.debug(
                            "LiveKit VAD [%s]: LISTENING → PROCESSING (frames=%d)",
                            sid[:8], len(ps["frames"]),
                        )
                        ps["state"] = _ParticipantState.PROCESSING
                        frames_snapshot = list(ps["frames"])
                        ps["frames"] = []
                        ps["speech_count"] = 0
                        ps["silence_count"] = 0
                        _schedule_pipeline(sid, ps, local_participant, frames_snapshot)

            # PROCESSING and COOLDOWN: ignore incoming frames (no buffering)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A native AudioStream that fails DURING iteration/use (not just construction)
        # leaves the room connected but DEAF. Treat it exactly like the ctor crash:
        # force the one-way, sticky native→aiortc switch and let the agent loop
        # reconnect. Guarded on is_native so an aiortc-track failure never forces a
        # redundant fallback (the switch is idempotent and cannot flap/loop). Raised
        # from debug → warning either way so the backend failure is visible.
        if is_native and not _force_aiortc:
            _force_aiortc = True
            logger.warning(
                "LiveKit native audio stream failed mid-use for %s (%s) — "
                "switching to the aiortc backend", sid, exc,
            )
        else:
            logger.warning("LiveKit audio stream error for %s: %s", sid, exc)
    finally:
        # The native AudioStream owns an FFI queue subscription + an internal asyncio
        # task; without aclose() each participant disconnect / track re-subscribe /
        # teardown leaks them. The aiortc stand-in has no aclose() — guard via getattr.
        aclose = getattr(audio_stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception as exc:
                logger.debug("LiveKit AudioStream aclose failed for %s: %s", sid, exc)


def _on_pipeline_done(sid: str, ps: dict) -> None:
    """Called when the pipeline task finishes — move to COOLDOWN."""
    if ps.get("state") == _ParticipantState.PROCESSING:
        ps["state"] = _ParticipantState.COOLDOWN
        ps["cooldown_deadline"] = time.monotonic() + _COOLDOWN_TIMEOUT_S
        logger.debug("LiveKit VAD [%s]: PROCESSING → COOLDOWN", sid[:8])


async def _cooldown_watchdog(participant_state: dict) -> None:
    """Periodic task that auto-expires COOLDOWN states whose deadline has passed."""
    while True:
        await asyncio.sleep(1.0)
        now = time.monotonic()
        for sid, ps in list(participant_state.items()):
            if ps.get("state") == _ParticipantState.COOLDOWN:
                if now >= ps.get("cooldown_deadline", now):
                    ps["state"] = _ParticipantState.IDLE
                    ps["speech_count"] = 0
                    ps["silence_count"] = 0
                    logger.debug("LiveKit VAD [%s]: COOLDOWN → IDLE (timeout)", sid[:8])


def _make_participant_state(sid: str) -> dict:
    return {
        "state": _ParticipantState.IDLE,
        "frames": [],
        "speech_count": 0,
        "silence_count": 0,
        "ptt_active": False,
        "pipeline_task": None,
        "cooldown_deadline": 0.0,
        # Barge-in (ZOE_VOICE_BARGE_IN): per-participant Silero stream + counters
        "vad": None,
        "vad_failed": False,
        "barge_window": [],
        "barge_frames": [],
        "user_id": "guest",
        "session_id": f"livekit-{sid[:8]}",
    }


def _build_room_handlers(room, participant_state: dict, audio_tasks: dict) -> None:
    """Attach event handlers to a room object (works with both livekit.rtc and aiortc rooms)."""

    @room.on("participant_connected")
    def on_participant_connected(participant) -> None:
        logger.info("LiveKit: participant joined %s (%s)", participant.identity, participant.sid[:8])
        participant_state[participant.sid] = _make_participant_state(participant.sid)
        # Only real (non-agent) participants hold the idle reaper open.  The
        # agent's own identity must never count, or the container never reaps.
        if getattr(participant, "identity", None) != _AGENT_IDENTITY:
            _active_participant_sids.add(participant.sid)
        note_voice_activity()

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant) -> None:
        logger.info("LiveKit: participant left %s", participant.identity)
        _active_participant_sids.discard(participant.sid)
        note_voice_activity()
        participant_state.pop(participant.sid, None)
        task = audio_tasks.pop(participant.sid, None)
        if task and not task.done():
            task.cancel()

    @room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant) -> None:
        from livekit_aiortc import _TrackKind
        kind = getattr(track, "kind", None)
        # Accept both livekit.rtc.TrackKind.KIND_AUDIO (int 1) and our _TrackKind
        if kind not in (1, _TrackKind.KIND_AUDIO):
            try:
                from livekit import rtc as lk_rtc
                if kind != lk_rtc.TrackKind.KIND_AUDIO:
                    return
            except Exception:
                return
        old = audio_tasks.pop(participant.sid, None)
        if old and not old.done():
            old.cancel()
        if participant.sid not in participant_state:
            participant_state[participant.sid] = _make_participant_state(participant.sid)
        audio_tasks[participant.sid] = asyncio.ensure_future(
            _collect_audio_stream(
                track,
                participant.sid,
                participant_state,
                room.local_participant,
            )
        )
        _VOICE_HEALTH["audio_tracks"] += 1
        _health_update(last_stage="audio_track_active")
        logger.info("LiveKit: subscribed to audio track for %s (native WebRTC active)", participant.identity)

    @room.on("data_received")
    def on_data_received(data_packet) -> None:
        participant = data_packet.participant
        if participant is None or participant.identity == _AGENT_IDENTITY:
            return
        try:
            msg = json.loads(data_packet.data.decode())
        except Exception:
            return

        msg_type = msg.get("type", "")
        sid = participant.sid

        if sid not in participant_state:
            participant_state[sid] = _make_participant_state(sid)
        ps = participant_state[sid]

        if msg_type == "identify":
            ps["user_id"] = msg.get("user_id") or "guest"
            ps["session_id"] = msg.get("session_id") or ps["session_id"]

        elif msg_type == "ptt_start":
            ps["ptt_active"] = True
            ps["frames"] = []
            ps["state"] = _ParticipantState.LISTENING
            logger.debug("LiveKit: PTT start from %s", participant.identity)

        elif msg_type == "ptt_stop":
            if not ps.get("ptt_active"):
                return
            ps["ptt_active"] = False
            frames_snapshot = list(ps["frames"])
            ps["frames"] = []
            if not frames_snapshot:
                ps["state"] = _ParticipantState.IDLE
                return
            ps["state"] = _ParticipantState.PROCESSING
            task = asyncio.ensure_future(
                _run_pipeline(
                    room.local_participant,
                    frames_snapshot,
                    ps.get("user_id", "guest"),
                    ps.get("session_id", f"livekit-{sid[:8]}"),
                )
            )
            ps["pipeline_task"] = task
            task.add_done_callback(
                lambda _t, _sid=sid, _ps=ps: _on_pipeline_done(_sid, _ps)
            )
            logger.debug("LiveKit: PTT stop from %s, processing %d frames",
                         participant.identity, len(frames_snapshot))

        elif msg_type == "playback_done":
            _VOICE_HEALTH["playback_completions"] += 1
            _health_update(last_stage="idle")
            if ps["state"] in (_ParticipantState.COOLDOWN, _ParticipantState.PROCESSING):
                ps["state"] = _ParticipantState.IDLE
                ps["speech_count"] = 0
                ps["silence_count"] = 0
                logger.debug("LiveKit VAD [%s]: playback_done → IDLE", sid[:8])

        elif msg_type == "text":
            message = str(msg.get("message") or msg.get("text") or "").strip()
            if not message:
                return
            ps["state"] = _ParticipantState.PROCESSING
            task = asyncio.ensure_future(
                _run_text_pipeline(
                    room.local_participant,
                    message,
                    ps.get("user_id", "guest"),
                    msg.get("session_id") or ps.get("session_id", f"livekit-{sid[:8]}"),
                )
            )
            ps["pipeline_task"] = task
            task.add_done_callback(
                lambda _t, _sid=sid, _ps=ps: _on_pipeline_done(_sid, _ps)
            )


async def _agent_loop() -> None:
    """Main loop: connect to LiveKit room and manage VAD/audio for all participants.

    Tries livekit-ffi (native WebRTC) first.  On platforms where livekit-ffi
    cannot initialise a PeerConnection (e.g. Jetson ARM64 Tegra kernel), falls
    back automatically to the pure-Python aiortc backend.
    """
    global _cooldown_task
    participant_state: dict[str, dict] = {}
    audio_tasks: dict[str, asyncio.Task] = {}
    backoff = 2.0
    # ZOE_LK_USE_AIORTC=1 skips livekit-ffi entirely (set in .env on Jetson).
    # _force_aiortc is sticky: once the native backend is proven broken (here or in
    # _collect_audio_stream) even a fresh agent loop goes straight to aiortc.
    use_aiortc = os.environ.get("ZOE_LK_USE_AIORTC", "0") == "1" or _force_aiortc

    # Track the cooldown watchdog so it doesn't leak as a forever `while True:
    # sleep(1)` task per start→idle cycle. Cancel any stale one first (a prior
    # cycle whose agent task was cancelled out from under it) before replacing it;
    # stop_livekit_ondemand cancels it on teardown.
    if _cooldown_task is not None and not _cooldown_task.done():
        _cooldown_task.cancel()
    cooldown_task = asyncio.ensure_future(_cooldown_watchdog(participant_state))
    _cooldown_task = cooldown_task

    try:
        while True:
            room = None
            try:
                token = _mint_agent_token()

                if use_aiortc:
                    # ── aiortc backend (pure Python, no livekit-ffi) ──────────
                    from livekit_aiortc import make_room, _ConnState
                    room = make_room()
                    _build_room_handlers(room, participant_state, audio_tasks)
                    logger.info("LiveKit agent connecting via aiortc backend to %s room=%s",
                                _LIVEKIT_INTERNAL_URL, _ROOM_NAME)
                    _health_update(status="connecting", backend="aiortc", connected=False)
                    await room.connect(_LIVEKIT_INTERNAL_URL, token)
                    logger.info("LiveKit agent connected (aiortc) as '%s'", _AGENT_IDENTITY)
                    _record_voice_connected()
                    backoff = 2.0

                    for p in room.remote_participants.values():
                        if p.sid not in participant_state:
                            participant_state[p.sid] = _make_participant_state(p.sid)

                    while room.connection_state == _ConnState.CONN_CONNECTED:
                        await asyncio.sleep(5)

                else:
                    # ── livekit-ffi native backend (preferred) ─────────────────
                    try:
                        from livekit import rtc as lk_rtc  # type: ignore
                    except ImportError:
                        logger.warning(
                            "livekit SDK not installed — falling back to aiortc backend"
                        )
                        use_aiortc = True
                        continue

                    room = lk_rtc.Room()
                    _build_room_handlers(room, participant_state, audio_tasks)
                    logger.info("LiveKit agent connecting via livekit-ffi to %s room=%s",
                                _LIVEKIT_INTERNAL_URL, _ROOM_NAME)
                    _health_update(status="connecting", backend="livekit-ffi", connected=False)
                    await room.connect(_LIVEKIT_INTERNAL_URL, token)
                    logger.info("LiveKit agent connected (livekit-ffi) as '%s'", _AGENT_IDENTITY)
                    _record_voice_connected()
                    backoff = 2.0

                    for p in room.remote_participants.values():
                        if p.sid not in participant_state:
                            participant_state[p.sid] = _make_participant_state(p.sid)

                    while room.connection_state == lk_rtc.ConnectionState.CONN_CONNECTED:
                        await asyncio.sleep(5)
                        if _force_aiortc:
                            # _collect_audio_stream proved native audio is broken on this
                            # host — drop the native room and reconnect via aiortc so the
                            # agent actually receives audio instead of staying deaf.
                            use_aiortc = True
                            logger.warning(
                                "LiveKit: native audio backend broken — reconnecting "
                                "via the aiortc backend"
                            )
                            break

            except asyncio.CancelledError:
                break
            except Exception as exc:
                err_str = str(exc)
                if not use_aiortc and (
                    "internal webrtc failure" in err_str.lower()
                    or "failed to initialize pc" in err_str.lower()
                ):
                    # Native livekit-ffi can't initialise WebRTC on this platform.
                    # Switch permanently to the aiortc backend — no more log spam.
                    use_aiortc = True
                    backoff = 2.0
                    logger.warning(
                        "LiveKit: livekit-ffi WebRTC failed (%s). "
                        "Switching to aiortc pure-Python backend permanently.",
                        exc,
                    )
                else:
                    _health_update(status="degraded", connected=False, last_error=err_str[:240])
                    logger.warning("LiveKit agent error: %s — reconnecting in %.0fs", exc, backoff)
            finally:
                if room is not None:
                    _health_update(
                        connected=False,
                        last_disconnected_at=_utc_now(),
                    )
                    # Room torn down → drop stale participant tracking so the idle
                    # reaper isn't held open by sids that can no longer disconnect.
                    _active_participant_sids.clear()
                for task in list(audio_tasks.values()):
                    if not task.done():
                        task.cancel()
                audio_tasks.clear()
                if room:
                    try:
                        await room.disconnect()
                    except Exception:
                        pass

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
    finally:
        # Cancel the cooldown watchdog on ANY loop teardown (incl. self-exit
        # outside stop_livekit_ondemand) so it can't leak as a forever
        # `while True: sleep(1)` task. Clear the module ref if it's still ours.
        if _cooldown_task is not None and not _cooldown_task.done():
            _cooldown_task.cancel()
        if _cooldown_task is cooldown_task:
            _cooldown_task = None


async def start_livekit_agent() -> None:
    """Entry point called from main.py lifespan via asyncio.create_task()."""
    global _agent_running
    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    if not api_key:
        _health_update(status="disabled", connected=False, last_error="LIVEKIT_API_KEY not set")
        logger.info("LIVEKIT_API_KEY not set — LiveKit agent not started")
        return
    if _ondemand_enabled():
        # On-demand mode: leave the container stopped; ensure_livekit_started()
        # spins it up (and the agent loop) on the first /livekit-token request.
        # Start the idle monitor now so a container left running across a service
        # restart (orphaned, with no agent/monitor) still gets reaped.
        global _idle_task
        note_voice_activity()
        _health_update(status="stopped", connected=False, last_error=None)
        if _idle_task is None or _idle_task.done():
            _idle_task = asyncio.create_task(_idle_monitor(), name="livekit_idle_monitor")
        logger.info(
            "LiveKit on-demand mode: agent will start on first /livekit-token request"
        )
        return
    if _agent_running:
        logger.warning("LiveKit voice agent already running; duplicate start ignored")
        return
    _agent_running = True
    try:
        logger.info("Starting LiveKit voice agent (VAD mode)")
        await _agent_loop()
    finally:
        _agent_running = False
        _health_update(status="stopped", connected=False)


# ── HTTP fallback endpoints ───────────────────────────────────────────────────
# These handle the browser-side VAD + HTTP upload path used when the native
# WebRTC agent cannot join the room (e.g. platform incompatibility).
# Also used by touch/voice.html which uses this HTTP approach by design.

router = APIRouter(prefix="/api/voice")


@router.get("/livekit-health")
async def livekit_health() -> dict:
    return get_voice_health()

# In-flight cancel tokens: "user_id:session_id" → True
_pending_cancel: set[str] = set()


async def _get_current_user_soft(request: Request) -> dict:
    """Resolve user from request without hard-failing (returns guest on error)."""
    try:
        from auth import get_current_user
        from fastapi.security.utils import get_authorization_scheme_param
        async def _gen():
            yield request
        gen = _gen()
        db_gen = None
        return await get_current_user(request)
    except Exception:
        return {"user_id": "guest", "role": "guest"}


@router.post("/livekit-audio")
async def livekit_audio(
    request: Request,
    audio: UploadFile = File(...),
    session_id: str = Form(""),
) -> JSONResponse:
    """Browser-side VAD HTTP upload endpoint.

    Receives a recorded audio blob from voice.html (LiveKit mode), runs the
    full STT → LLM → TTS pipeline, and returns the result as JSON.
    Also used by touch/voice.html.
    """
    user = await _get_current_user_soft(request)
    user_id = user.get("user_id", "guest")
    sid = session_id or f"lk-http-{user_id}"
    cancel_key = f"{user_id}:{sid}"

    audio_bytes = await audio.read()
    if not audio_bytes:
        return JSONResponse({"ok": False, "error": "empty audio"})

    content_type = audio.content_type or ""
    suffix = ".webm" if "webm" in content_type else (".ogg" if "ogg" in content_type else ".wav")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(audio_bytes)
        tmp_path = tf.name

    try:
        from routers.voice_tts import _transcribe_audio
        transcript = await _transcribe_audio(tmp_path)
    except Exception as exc:
        logger.warning("LiveKit HTTP STT failed: %s", exc)
        return JSONResponse({"ok": False, "error": "STT failed"})
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not transcript:
        return JSONResponse({"ok": True, "transcript": "", "audio_base64": None})

    if cancel_key in _pending_cancel:
        _pending_cancel.discard(cancel_key)
        return JSONResponse({"ok": True, "cancelled": True})

    try:
        from brain_dispatch import brain_oneshot
        response_text = await brain_oneshot(transcript, sid, user_id, voice_mode=True)
    except Exception as exc:
        logger.error("LiveKit HTTP LLM error: %s", exc)
        response_text = "Sorry, I had trouble processing that."

    if cancel_key in _pending_cancel:
        _pending_cancel.discard(cancel_key)
        return JSONResponse({"ok": True, "cancelled": True})

    audio_b64 = None
    resp_content_type = "audio/wav"
    try:
        from routers.voice_tts import synthesize as _synth
        tts_resp = await _synth(
            {"text": response_text},
            caller={"source": "livekit-http", "user_id": user_id},
        )
        audio_b64 = base64.b64encode(tts_resp.body).decode("ascii")
        resp_content_type = tts_resp.media_type
    except Exception as exc:
        logger.warning("LiveKit HTTP TTS failed: %s", exc)

    return JSONResponse({
        "ok": True,
        "transcript": transcript,
        "response_text": response_text,
        "audio_base64": audio_b64,
        "content_type": resp_content_type,
    })


@router.post("/livekit-cancel")
async def livekit_cancel(request: Request) -> JSONResponse:
    """Cancel a pending livekit-audio pipeline request."""
    user = await _get_current_user_soft(request)
    user_id = user.get("user_id", "guest")
    try:
        body = await request.json()
        sid = body.get("session_id", f"lk-http-{user_id}")
    except Exception:
        sid = f"lk-http-{user_id}"
    _pending_cancel.add(f"{user_id}:{sid}")
    return JSONResponse({"ok": True})
