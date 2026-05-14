"""voice_livekit.py — Server-side LiveKit agent for voice.html?mode=livekit.

Joins the "zoe-voice" LiveKit room as the "zoe-agent" participant, then:
  1. Waits for PTT data messages ({"type":"ptt_start"} / {"type":"ptt_stop"}) from browser clients
  2. On ptt_stop: buffers the audio frames that arrived during PTT → WAV → STT → LLM → TTS
  3. Sends the TTS audio back via data channel as {"type":"audio","audio_base64":...}
     which voice.html handles via playAudioBase64() — same as the local WS path
  4. Sends state/transcript/done events so the orb and transcript panel stay in sync

API notes (livekit Python SDK v1.x):
  - Room events: "participant_connected"(p), "participant_disconnected"(p),
    "track_subscribed"(track, pub, p), "data_received"(data_packet)
  - data_packet.data: bytes, data_packet.participant: RemoteParticipant|None
  - Audio collection: rtc.AudioStream(track) as async iterator → AudioFrameEvent
  - LocalParticipant.publish_data(payload: bytes|str, *, reliable: bool)

Requires: pip install livekit>=0.14
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import tempfile
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# ── Agent configuration ───────────────────────────────────────────────────────
# Use the internal host:port — the server agent connects server-to-server,
# not through nginx/Cloudflare. LIVEKIT_URL is the browser-facing WSS URL.
_LIVEKIT_INTERNAL_URL = "ws://127.0.0.1:7880"
_ROOM_NAME = "zoe-voice"
_AGENT_IDENTITY = "zoe-agent"

# Per-participant PTT state: sid → state dict
_ptt_state: dict[str, dict] = {}


def _mint_agent_token() -> str:
    """Mint a LiveKit JWT for the server-side agent (no livekit-server-sdk needed)."""
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


def _pcm_frames_to_wav(frames: list[bytes], sample_rate: int = 48000, channels: int = 1) -> bytes:
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


async def _handle_ptt_stop(local_participant, sender_sid: str) -> None:
    """STT → LLM → TTS pipeline triggered when PTT is released."""
    state = _ptt_state.get(sender_sid)
    if not state or not state.get("frames"):
        await _send_data(local_participant, {"type": "state", "state": "ambient"})
        return

    frames = list(state["frames"])
    state["frames"] = []
    user_id = state.get("user_id", "family-admin")
    session_id = state.get("session_id") or f"livekit-{sender_sid[:8]}"

    await _send_data(local_participant, {"type": "state", "state": "thinking"})

    # ── STT ──────────────────────────────────────────────────────────────────
    transcript = ""
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
        logger.warning("LiveKit STT failed: %s", exc)
        await _send_data(local_participant, {"type": "state", "state": "ambient"})
        return

    if not transcript:
        await _send_data(local_participant, {"type": "state", "state": "ambient"})
        return

    await _send_data(local_participant, {"type": "transcript", "role": "user", "text": transcript})

    # ── LLM ──────────────────────────────────────────────────────────────────
    response = "Sorry, I had trouble with that."
    try:
        from zoe_agent import run_zoe_agent
        response = await run_zoe_agent(transcript, session_id, user_id, voice_mode=True)
    except Exception as exc:
        logger.error("LiveKit LLM error: %s", exc)

    await _send_data(local_participant, {"type": "state", "state": "responding"})
    await _send_data(local_participant, {"type": "transcript", "role": "zoe", "text": response})

    # ── TTS → send audio via data channel ────────────────────────────────────
    # voice.html handleVoiceEvent handles type="audio" with playAudioBase64()
    try:
        from routers.voice_tts import synthesize as _synth
        tts_resp = await _synth({"text": response}, caller={"source": "livekit", "user_id": user_id})
        await _send_data(local_participant, {
            "type": "audio",
            "audio_base64": base64.b64encode(tts_resp.body).decode("ascii"),
            "content_type": tts_resp.media_type,
        })
    except Exception as exc:
        logger.warning("LiveKit TTS failed: %s", exc)
        await _send_data(local_participant, {"type": "text", "content": response})

    await _send_data(local_participant, {"type": "done"})
    await _send_data(local_participant, {"type": "state", "state": "ambient"})


async def _collect_audio_stream(track, sid: str) -> None:
    """Background task: read AudioStream frames and buffer them during active PTT."""
    try:
        from livekit import rtc as lk_rtc  # type: ignore
        audio_stream = lk_rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        async for frame_event in audio_stream:
            state = _ptt_state.get(sid)
            if state and state.get("buffering"):
                # frame_event.frame.data is a memoryview of int16-LE PCM samples
                state["frames"].append(bytes(frame_event.frame.data))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("LiveKit audio stream ended for %s: %s", sid, exc)


async def _agent_loop() -> None:
    """Main loop: connect to LiveKit room and process PTT/audio events."""
    try:
        from livekit import rtc as lk_rtc  # type: ignore
    except ImportError:
        logger.warning(
            "livekit Python SDK not installed — LiveKit agent disabled. "
            "Run: pip install livekit>=0.14"
        )
        return

    backoff = 2.0
    audio_tasks: dict[str, asyncio.Task] = {}

    while True:
        room: Optional[lk_rtc.Room] = None
        try:
            token = _mint_agent_token()
            room = lk_rtc.Room()

            @room.on("participant_connected")
            def on_participant_connected(participant: lk_rtc.RemoteParticipant) -> None:
                logger.info("LiveKit: participant joined %s", participant.identity)
                _ptt_state[participant.sid] = {
                    "buffering": False,
                    "frames": [],
                    "user_id": "family-admin",
                    "session_id": f"livekit-{participant.sid[:8]}",
                }

            @room.on("participant_disconnected")
            def on_participant_disconnected(participant: lk_rtc.RemoteParticipant) -> None:
                _ptt_state.pop(participant.sid, None)
                task = audio_tasks.pop(participant.sid, None)
                if task and not task.done():
                    task.cancel()

            @room.on("track_subscribed")
            def on_track_subscribed(
                track: lk_rtc.RemoteAudioTrack,
                publication: lk_rtc.RemoteTrackPublication,
                participant: lk_rtc.RemoteParticipant,
            ) -> None:
                if track.kind != lk_rtc.TrackKind.KIND_AUDIO:
                    return
                # Cancel any existing stream task for this participant
                old = audio_tasks.pop(participant.sid, None)
                if old and not old.done():
                    old.cancel()
                audio_tasks[participant.sid] = asyncio.ensure_future(
                    _collect_audio_stream(track, participant.sid)
                )

            @room.on("data_received")
            def on_data_received(data_packet) -> None:
                # data_packet.data: bytes, data_packet.participant: RemoteParticipant|None
                participant = data_packet.participant
                if participant is None or participant.identity == _AGENT_IDENTITY:
                    return
                try:
                    msg = json.loads(data_packet.data.decode())
                except Exception:
                    return

                msg_type = msg.get("type", "")
                sid = participant.sid

                if msg_type == "ptt_start":
                    if sid not in _ptt_state:
                        _ptt_state[sid] = {
                            "buffering": False, "frames": [],
                            "user_id": "family-admin",
                            "session_id": f"livekit-{sid[:8]}",
                        }
                    _ptt_state[sid]["buffering"] = True
                    _ptt_state[sid]["frames"] = []

                elif msg_type == "ptt_stop":
                    if sid in _ptt_state:
                        _ptt_state[sid]["buffering"] = False
                        asyncio.ensure_future(
                            _handle_ptt_stop(room.local_participant, sid)
                        )

                elif msg_type == "identify":
                    if sid in _ptt_state:
                        _ptt_state[sid]["user_id"] = msg.get("user_id", "family-admin")
                        _ptt_state[sid]["session_id"] = (
                            msg.get("session_id") or _ptt_state[sid]["session_id"]
                        )

            logger.info("LiveKit agent connecting to %s room=%s", _LIVEKIT_INTERNAL_URL, _ROOM_NAME)
            await room.connect(_LIVEKIT_INTERNAL_URL, token)
            logger.info("LiveKit agent connected as '%s'", _AGENT_IDENTITY)
            backoff = 2.0

            # Seed state for any participants already in the room
            for participant in room.remote_participants.values():
                if participant.sid not in _ptt_state:
                    _ptt_state[participant.sid] = {
                        "buffering": False, "frames": [],
                        "user_id": "family-admin",
                        "session_id": f"livekit-{participant.sid[:8]}",
                    }

            # Block until disconnected
            while room.connection_state == lk_rtc.ConnectionState.CONN_CONNECTED:
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("LiveKit agent error: %s — reconnecting in %.0fs", exc, backoff)
        finally:
            # Cancel all audio collection tasks
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


async def start_livekit_agent() -> None:
    """Entry point called from main.py lifespan."""
    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    if not api_key:
        logger.info("LIVEKIT_API_KEY not set — LiveKit agent not started")
        return
    logger.info("Starting LiveKit voice agent")
    await _agent_loop()
