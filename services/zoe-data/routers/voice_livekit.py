"""voice_livekit.py — Server-side LiveKit agent for voice.html?mode=livekit.

Joins the "zoe-voice" LiveKit room as the "zoe-agent" participant, then:
  1. Waits for PTT data messages ({"type":"ptt_start"} / {"type":"ptt_stop"}) from browser clients
  2. On ptt_stop: buffers the audio frames that arrived during PTT → WAV → STT → LLM → TTS
  3. Publishes the TTS audio as a local audio source so browsers subscribing to the room hear it
  4. Sends JSON data messages back (state, transcript, agui) that voice.html handles via handleVoiceEvent

The agent connects using the internal LiveKit URL (ws://127.0.0.1:7880) with the server-side
API key/secret from .env, independent of the public-facing LIVEKIT_URL.

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

# ── LiveKit internal URL (server-to-server; NOT the browser-facing wss:// URL) ──
_LIVEKIT_INTERNAL_URL = "ws://127.0.0.1:7880"
_ROOM_NAME = "zoe-voice"
_AGENT_IDENTITY = "zoe-agent"

# Per-participant PTT state: sid → {"buffering": bool, "frames": list[bytes], "user_id": str, "session_id": str}
_ptt_state: dict[str, dict] = {}


def _mint_agent_token() -> str:
    """Mint a LiveKit JWT for the server-side agent participant (no livekit-server-sdk needed)."""
    import jwt as _jwt

    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set in .env")

    now = int(time.time())
    payload = {
        "exp": now + 86400,  # 24h; agent reconnects with a fresh token on expiry
        "iss": api_key,
        "sub": _AGENT_IDENTITY,
        "jti": uuid.uuid4().hex,
        "video": {
            "roomJoin": True,
            "room": _ROOM_NAME,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
            "hidden": False,
        },
    }
    return _jwt.encode(payload, api_secret, algorithm="HS256")


def _pcm_frames_to_wav(frames: list[bytes], sample_rate: int = 48000, channels: int = 1) -> bytes:
    """Concatenate raw PCM frames and wrap in a minimal WAV header."""
    raw = b"".join(frames)
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(raw)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,           # chunk size
        1,            # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + raw


async def _send_data(room, payload: dict) -> None:
    """Broadcast a JSON data message to all browser participants in the room."""
    try:
        encoded = json.dumps(payload).encode()
        await room.local_participant.publish_data(encoded, reliable=True)
    except Exception as exc:
        logger.debug("LiveKit data send error: %s", exc)


async def _handle_ptt_stop(room, sender_sid: str) -> None:
    """Process buffered audio after PTT release: STT → LLM → TTS → publish audio."""
    state = _ptt_state.get(sender_sid)
    if not state or not state.get("frames"):
        await _send_data(room, {"type": "state", "state": "ambient"})
        return

    frames = state.pop("frames", [])
    state["frames"] = []
    user_id = state.get("user_id", "family-admin")
    session_id = state.get("session_id") or f"livekit-{sender_sid[:8]}"

    await _send_data(room, {"type": "state", "state": "thinking"})

    # ── Phase 1: STT ──────────────────────────────────────────────────────
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
        await _send_data(room, {"type": "state", "state": "ambient"})
        return

    if not transcript:
        await _send_data(room, {"type": "state", "state": "ambient"})
        return

    await _send_data(room, {"type": "transcript", "role": "user", "text": transcript})

    # ── Phase 2: LLM ──────────────────────────────────────────────────────
    response = "Sorry, I had trouble with that."
    try:
        from zoe_agent import run_zoe_agent
        response = await run_zoe_agent(transcript, session_id, user_id, voice_mode=True)
    except Exception as exc:
        logger.error("LiveKit LLM error: %s", exc)

    await _send_data(room, {"type": "state", "state": "responding"})
    await _send_data(room, {"type": "transcript", "role": "zoe", "text": response})

    # ── Phase 3: TTS → publish audio track ───────────────────────────────
    try:
        from routers.voice_tts import synthesize as _synth
        tts_resp = await _synth({"text": response}, caller={"source": "livekit", "user_id": user_id})
        audio_bytes = tts_resp.body
        content_type = tts_resp.media_type

        # Also send audio as base64 data message so voice.html can play via <Audio> if track fails
        await _send_data(room, {
            "type": "audio",
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "content_type": content_type,
        })

        # Publish as a LiveKit audio source so subscribed browsers receive it natively
        try:
            from livekit import rtc as lk_rtc  # type: ignore

            # Write audio to a temp file and read as PCM for publishing
            # For now use the data-channel audio path (above); native track publish
            # requires converting the TTS WAV/MP3 to raw PCM frames which is
            # best done with soundfile or librosa — optional enhancement.
            pass
        except ImportError:
            pass  # Fall back to data-channel audio_base64 (already sent above)

    except Exception as exc:
        logger.warning("LiveKit TTS failed: %s", exc)
        await _send_data(room, {"type": "text", "content": response})

    await _send_data(room, {"type": "done"})
    await _send_data(room, {"type": "state", "state": "ambient"})


async def _agent_loop() -> None:
    """Main agent loop: connect to the LiveKit room and process events."""
    try:
        from livekit import api as lk_api, rtc as lk_rtc  # type: ignore
    except ImportError:
        logger.warning(
            "livekit Python SDK not installed — LiveKit agent disabled. "
            "Run: pip install livekit>=0.14"
        )
        return

    backoff = 2.0

    while True:
        room: Optional[lk_rtc.Room] = None
        try:
            token = _mint_agent_token()
            room = lk_rtc.Room()

            @room.on("participant_connected")
            def on_participant_connected(participant: lk_rtc.RemoteParticipant) -> None:
                logger.info("LiveKit: participant joined %s (%s)", participant.identity, participant.sid)
                _ptt_state[participant.sid] = {
                    "buffering": False,
                    "frames": [],
                    "user_id": "family-admin",
                    "session_id": f"livekit-{participant.sid[:8]}",
                }

            @room.on("participant_disconnected")
            def on_participant_disconnected(participant: lk_rtc.RemoteParticipant) -> None:
                _ptt_state.pop(participant.sid, None)

            @room.on("data_received")
            def on_data_received(data: bytes, participant: lk_rtc.RemoteParticipant, *args) -> None:
                if participant.identity == _AGENT_IDENTITY:
                    return
                try:
                    msg = json.loads(data.decode())
                except Exception:
                    return

                msg_type = msg.get("type", "")
                sid = participant.sid

                if msg_type == "ptt_start":
                    if sid not in _ptt_state:
                        _ptt_state[sid] = {
                            "buffering": False,
                            "frames": [],
                            "user_id": "family-admin",
                            "session_id": f"livekit-{sid[:8]}",
                        }
                    _ptt_state[sid]["buffering"] = True
                    _ptt_state[sid]["frames"] = []

                elif msg_type == "ptt_stop":
                    if sid in _ptt_state:
                        _ptt_state[sid]["buffering"] = False
                        asyncio.ensure_future(_handle_ptt_stop(room, sid))

                elif msg_type == "identify":
                    # Browser can send its user_id/session_id for context
                    if sid in _ptt_state:
                        _ptt_state[sid]["user_id"] = msg.get("user_id", "family-admin")
                        _ptt_state[sid]["session_id"] = msg.get("session_id") or _ptt_state[sid]["session_id"]

            @room.on("track_subscribed")
            def on_track_subscribed(
                track: lk_rtc.Track,
                publication: lk_rtc.RemoteTrackPublication,
                participant: lk_rtc.RemoteParticipant,
            ) -> None:
                if track.kind != lk_rtc.TrackKind.KIND_AUDIO:
                    return

                @track.on("audio_frame_received")
                def on_frame(frame: lk_rtc.AudioFrame) -> None:
                    sid = participant.sid
                    state = _ptt_state.get(sid)
                    if state and state.get("buffering"):
                        # Store raw PCM bytes (int16 LE, typically 48000Hz mono)
                        state["frames"].append(bytes(frame.data))

            logger.info("LiveKit agent connecting to %s room=%s", _LIVEKIT_INTERNAL_URL, _ROOM_NAME)
            await room.connect(_LIVEKIT_INTERNAL_URL, token)
            logger.info("LiveKit agent connected as '%s'", _AGENT_IDENTITY)
            backoff = 2.0  # reset on successful connect

            # Populate initial participants
            for participant in room.remote_participants.values():
                if participant.sid not in _ptt_state:
                    _ptt_state[participant.sid] = {
                        "buffering": False,
                        "frames": [],
                        "user_id": "family-admin",
                        "session_id": f"livekit-{participant.sid[:8]}",
                    }

            # Keep alive until disconnected
            while room.connection_state == lk_rtc.ConnectionState.CONN_CONNECTED:
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            if room:
                try:
                    await room.disconnect()
                except Exception:
                    pass
            return
        except Exception as exc:
            logger.warning("LiveKit agent error: %s — reconnecting in %.0fs", exc, backoff)

        finally:
            if room:
                try:
                    await room.disconnect()
                except Exception:
                    pass

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


async def start_livekit_agent() -> None:
    """Entry point called from main.py lifespan. Runs the agent loop with structured logging."""
    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    if not api_key:
        logger.info("LIVEKIT_API_KEY not set — LiveKit agent not started")
        return
    logger.info("Starting LiveKit voice agent")
    await _agent_loop()
