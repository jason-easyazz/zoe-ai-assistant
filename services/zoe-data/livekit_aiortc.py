"""livekit_aiortc.py — LiveKit room participant using pure-Python aiortc.

Replaces the livekit-ffi native WebRTC library for platforms where the bundled
libwebrtc binary cannot initialise a PeerConnection (e.g. Jetson ARM64 Tegra).

Implements the minimum subset of the livekit.rtc Room API used by voice_livekit.py:
  - Room.connect(url, token)
  - Room.on("participant_connected" | "participant_disconnected"
           | "track_subscribed" | "data_received")
  - Room.remote_participants           → dict[sid → RemoteParticipant]
  - Room.local_participant.publish_data(payload, reliable=True)
  - Room.connection_state              → CONN_CONNECTED or CONN_DISCONNECTED
  - RemoteParticipant.identity / .sid
  - RemoteAudioTrack (passed to track_subscribed handler)
  - RemoteAudioTrack.kind == TrackKind.KIND_AUDIO
  - AudioStream(track, sample_rate=16000, num_channels=1)  → async iterable of frames
  - frame_event.frame.data  → bytes  (int16-LE PCM, mono, 16 kHz)

Signalling is LiveKit's binary WebSocket protobuf protocol (protocol version 7).
WebRTC media is handled by aiortc (pure Python — no native libwebrtc needed).
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Any, Callable, Dict, List, Optional

import av                              # PyAV — for AudioFrame resampling
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from livekit.protocol import rtc as lk_rtc

log = logging.getLogger(__name__)

# Suppress the very verbose aioice candidate-pair logs at INFO level
logging.getLogger("aioice").setLevel(logging.WARNING)
logging.getLogger("aioice.ice").setLevel(logging.WARNING)

# ── Sentinel connection states (mirror livekit.rtc.ConnectionState) ──────────
class _ConnState(IntEnum):
    CONN_DISCONNECTED = 0
    CONN_CONNECTED    = 1


class _TrackKind(IntEnum):
    KIND_AUDIO = 1
    KIND_VIDEO = 2


# ── Lightweight stand-ins for the livekit.rtc data types ─────────────────────

class _TrackKindProxy:
    KIND_AUDIO = _TrackKind.KIND_AUDIO
    KIND_VIDEO = _TrackKind.KIND_VIDEO


class _RemoteAudioTrack:
    """Wraps an aiortc MediaStreamTrack received from a remote participant."""
    kind = _TrackKind.KIND_AUDIO

    def __init__(self, track, participant: "_RemoteParticipant"):
        self._track   = track           # aiortc MediaStreamTrack
        self.sid      = track.id        # track SID (from SDP msid)
        self._participant = participant

    async def _recv(self):
        """Await the next AudioFrame from the underlying aiortc track."""
        return await self._track.recv()


@dataclass
class _FrameEvent:
    """Mimics the livekit AudioFrameEvent used in _collect_audio_stream."""
    frame: "_Frame"


@dataclass
class _Frame:
    """Mimics livekit AudioFrame — exposes .data as int16-LE PCM bytes."""
    data: bytes


class _AudioStream:
    """
    Async iterable that drains an aiortc audio track, resampling to
    16 kHz / mono / int16 as required by the energy VAD in voice_livekit.py.
    """
    def __init__(self, track: _RemoteAudioTrack, sample_rate: int = 16000,
                 num_channels: int = 1):
        self._track   = track
        self._rate    = sample_rate
        self._ch      = num_channels
        self._resampler: Optional[av.audio.resampler.AudioResampler] = None

    def __aiter__(self):
        return self

    async def __anext__(self) -> _FrameEvent:
        while True:
            try:
                av_frame: av.AudioFrame = await self._track._recv()
            except Exception:
                raise StopAsyncIteration

            # Lazy-init the resampler once we know the source format
            if self._resampler is None:
                self._resampler = av.AudioResampler(
                    format="s16",
                    layout="mono",
                    rate=self._rate,
                )

            resampled = self._resampler.resample(av_frame)
            for out_frame in resampled:
                raw = bytes(out_frame.planes[0])
                return _FrameEvent(frame=_Frame(data=raw))


@dataclass
class _RemoteParticipant:
    identity: str
    sid: str
    tracks: Dict[str, _RemoteAudioTrack] = field(default_factory=dict)


@dataclass
class _DataPacket:
    data: bytes
    participant: Optional[_RemoteParticipant]


class _LocalParticipant:
    """Proxy for Room.local_participant — wraps the data channel send."""

    def __init__(self, room: "AiortcRoom"):
        self._room = room

    async def publish_data(self, payload: bytes, reliable: bool = True,
                            **_kwargs) -> None:
        await self._room._send_data(payload)


# ── Main room class ───────────────────────────────────────────────────────────

class AiortcRoom:
    """
    Drop-in replacement for livekit.rtc.Room using aiortc + WebSocket signalling.
    Only the subset of the API used by voice_livekit.py is implemented.
    """

    # Expose enum aliases so callers can do room.ConnectionState.CONN_CONNECTED
    ConnectionState = _ConnState
    TrackKind       = _TrackKindProxy()

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._sub_pc: Optional[RTCPeerConnection] = None
        self._ws = None
        self._ws_lock = asyncio.Lock()
        self._signal_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._conn_state = _ConnState.CONN_DISCONNECTED
        self._remote_participants: Dict[str, _RemoteParticipant] = {}
        # msid → (participant_sid, track_sid)
        self._msid_to_participant: Dict[str, str] = {}
        self.local_participant = _LocalParticipant(self)
        self._data_channel = None
        self._ice_gathering_complete = asyncio.Event()
        self._last_pong_at: Optional[float] = None

    # ── Event API ─────────────────────────────────────────────────────────────
    def on(self, event: str):
        """Decorator: @room.on("event_name")"""
        def decorator(fn: Callable):
            self._handlers.setdefault(event, []).append(fn)
            return fn
        return decorator

    def _emit(self, event: str, *args, **kwargs):
        for fn in self._handlers.get(event, []):
            result = fn(*args, **kwargs)
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)

    # ── Public API ────────────────────────────────────────────────────────────
    @property
    def connection_state(self) -> _ConnState:
        return self._conn_state

    @property
    def remote_participants(self) -> Dict[str, _RemoteParticipant]:
        return self._remote_participants

    async def connect(self, url: str, token: str, **_options) -> None:
        """Connect to a LiveKit room using WebSocket signalling + aiortc."""
        ws_url = (
            f"{url.rstrip('/')}/rtc"
            f"?sdk=python&protocol=7&auto_subscribe=1&adaptive_stream=0"
            f"&access_token={token}"
        )
        log.info("[aiortc] Connecting to %s", ws_url.split("?")[0])
        self._ws = await websockets.connect(ws_url)
        self._conn_state = _ConnState.CONN_CONNECTED
        self._signal_task = asyncio.create_task(
            self._signal_loop(), name="livekit_aiortc_signal"
        )
        log.info("[aiortc] Room connected")

    async def disconnect(self) -> None:
        self._conn_state = _ConnState.CONN_DISCONNECTED
        current = asyncio.current_task()
        if self._ping_task and self._ping_task is not current:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        self._ping_task = None
        if self._sub_pc:
            await self._sub_pc.close()
            self._sub_pc = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._signal_task and self._signal_task is not current:
            self._signal_task.cancel()
            try:
                await self._signal_task
            except asyncio.CancelledError:
                pass
        self._signal_task = None

    # ── Signalling loop ───────────────────────────────────────────────────────
    async def _signal_loop(self) -> None:
        try:
            async for raw in self._ws:
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                resp = lk_rtc.SignalResponse()
                try:
                    resp.ParseFromString(bytes(raw))
                except Exception as exc:
                    log.debug("[aiortc] Proto parse error: %s", exc)
                    continue
                msg_type = resp.WhichOneof("message")
                await self._dispatch(msg_type, resp)
        except Exception as exc:
            log.warning("[aiortc] Signal loop ended: %s", exc)
        finally:
            self._conn_state = _ConnState.CONN_DISCONNECTED
            if self._ping_task:
                self._ping_task.cancel()

    async def _dispatch(self, msg_type: str, resp: lk_rtc.SignalResponse) -> None:
        if msg_type == "join":
            await self._on_join(resp.join)
        elif msg_type == "offer":
            await self._on_offer(resp.offer)
        elif msg_type == "answer":
            await self._on_answer(resp.answer)
        elif msg_type == "trickle":
            await self._on_trickle(resp.trickle)
        elif msg_type == "update":
            await self._on_participant_update(resp.update)
        elif msg_type == "track_published":
            log.debug("[aiortc] track_published: %s", resp.track_published)
        elif msg_type in ("pong", "pong_resp"):
            self._last_pong_at = time.monotonic()

    async def _on_join(self, join) -> None:
        log.info("[aiortc] Joined room '%s' as '%s'",
                 join.room.name, join.participant.identity)
        ping_interval = max(1, int(join.ping_interval or 5))
        ping_timeout = max(ping_interval + 1, int(join.ping_timeout or 15))
        if self._ping_task:
            self._ping_task.cancel()
        self._ping_task = asyncio.create_task(
            self._ping_loop(ping_interval, ping_timeout),
            name="livekit_aiortc_ping",
        )
        await self._ensure_sub_pc()
        for p in join.other_participants:
            self._upsert_participant(p.identity, p.sid)
            self._emit("participant_connected",
                       self._remote_participants[p.sid])

    async def _ping_loop(self, interval_s: int, timeout_s: int) -> None:
        """Keep the LiveKit signalling session alive.

        LiveKit advertises its heartbeat interval in JoinResponse. The native
        SDK handles this automatically; this aiortc adapter must send protocol
        pings itself or the server expires the participant after ~15 seconds.
        """
        self._last_pong_at = time.monotonic()
        try:
            while self._conn_state == _ConnState.CONN_CONNECTED:
                await asyncio.sleep(interval_s)
                timestamp_ms = int(time.time() * 1000)
                legacy_req = lk_rtc.SignalRequest()
                legacy_req.ping = timestamp_ms
                await self._ws_send(legacy_req)
                structured_req = lk_rtc.SignalRequest()
                structured_req.ping_req.timestamp = timestamp_ms
                structured_req.ping_req.rtt = 0
                await self._ws_send(structured_req)
                if (
                    self._last_pong_at is not None
                    and time.monotonic() - self._last_pong_at > timeout_s
                ):
                    raise TimeoutError(
                        f"LiveKit signalling pong timeout after {timeout_s}s"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("[aiortc] Heartbeat failed: %s", exc)
            self._conn_state = _ConnState.CONN_DISCONNECTED
            if self._ws:
                await self._ws.close()

    async def _on_offer(self, offer) -> None:
        """Handle SDP re-offer from LiveKit server (subscriber PC)."""
        log.debug("[aiortc] Received SDP offer (len=%d)", len(offer.sdp))
        pc = await self._ensure_sub_pc()

        # Parse the SDP to find msid → participant SID mappings before
        # we give it to aiortc (aiortc discards the msid a= lines).
        self._parse_msid_from_sdp(offer.sdp)

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=offer.sdp, type=offer.type)
        )
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        req = lk_rtc.SignalRequest()
        req.answer.sdp  = pc.localDescription.sdp
        req.answer.type = pc.localDescription.type
        await self._ws_send(req)
        log.debug("[aiortc] Sent SDP answer")

    async def _on_answer(self, answer) -> None:
        pass  # Not using a publisher PC for now

    async def _on_trickle(self, trickle) -> None:
        """Add an ICE candidate received from the server."""
        pc = self._sub_pc
        candidate_init = getattr(trickle, "candidate_init", None)
        if candidate_init is None:
            candidate_init = getattr(trickle, "candidateInit", None)
        if not pc or not candidate_init:
            return
        try:
            init = json.loads(candidate_init)
            cand_sdp = init.get("candidate", "")
            if not cand_sdp:
                return
            sdp_mid         = init.get("sdpMid", "0")
            sdp_m_line      = init.get("sdpMLineIndex", 0)

            # aiortc candidate_from_sdp expects the part after "candidate:"
            from aiortc.sdp import candidate_from_sdp
            tail = cand_sdp.split("candidate:")[-1]
            cand = candidate_from_sdp(tail)
            cand.sdpMid          = sdp_mid
            cand.sdpMLineIndex   = sdp_m_line
            await pc.addIceCandidate(cand)
        except Exception as exc:
            log.debug("[aiortc] ICE add error: %s", exc)

    async def _on_participant_update(self, update) -> None:
        from livekit.protocol import models as lk_models
        for p in update.participants:
            sid = p.sid
            identity = p.identity
            # State 3 = DISCONNECTED in livekit proto
            if p.state == 3:
                participant = self._remote_participants.pop(sid, None)
                if participant:
                    self._emit("participant_disconnected", participant)
            else:
                if sid not in self._remote_participants:
                    self._upsert_participant(identity, sid)
                    self._emit("participant_connected",
                               self._remote_participants[sid])

    # ── PC management ─────────────────────────────────────────────────────────
    async def _ensure_sub_pc(self) -> RTCPeerConnection:
        if self._sub_pc:
            return self._sub_pc

        pc = RTCPeerConnection()
        self._sub_pc = pc

        @pc.on("track")
        def on_track(track):
            log.info("[aiortc] Track received: kind=%s id=%s", track.kind, track.id)
            if track.kind == "audio":
                asyncio.ensure_future(self._handle_audio_track(track))

        @pc.on("datachannel")
        def on_dc(channel):
            log.info("[aiortc] Data channel received: %s", channel.label)
            self._data_channel = channel

            @channel.on("message")
            def on_msg(msg):
                raw = msg.encode() if isinstance(msg, str) else msg
                participant = self._find_participant_for_data()
                pkt = _DataPacket(data=raw, participant=participant)
                self._emit("data_received", pkt)

        @pc.on("icecandidate")
        async def on_ice_candidate(candidate):
            if candidate is None:
                return
            req = lk_rtc.SignalRequest()
            req.trickle.candidate_init = json.dumps({
                "candidate": f"candidate:{candidate.to_sdp()}",
                "sdpMid": candidate.sdpMid or "0",
                "sdpMLineIndex": candidate.sdpMLineIndex or 0,
            })
            req.trickle.target = lk_rtc.SignalTarget.SUBSCRIBER
            await self._ws_send(req)

        return pc

    async def _handle_audio_track(self, track) -> None:
        """Called when aiortc delivers an audio track — map to participant."""
        # Try to find participant via SDP msid mapping (track.id == track SID in msid)
        p_sid = self._msid_to_participant.get(track.id)
        participant = (
            self._remote_participants.get(p_sid)
            if p_sid
            else None
        )

        # Fallback: use the first non-agent remote participant
        if participant is None:
            for p in self._remote_participants.values():
                participant = p
                break

        if participant is None:
            # Create a synthetic participant
            participant = self._upsert_participant("user", f"synth-{track.id[:8]}")

        remote_track = _RemoteAudioTrack(track, participant)
        participant.tracks[remote_track.sid] = remote_track

        # Mimic the livekit track_subscribed callback signature:
        #   (track, publication, participant)
        self._emit("track_subscribed", remote_track, None, participant)
        log.info("[aiortc] Audio track subscribed from '%s'", participant.identity)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _upsert_participant(self, identity: str, sid: str) -> _RemoteParticipant:
        if sid not in self._remote_participants:
            self._remote_participants[sid] = _RemoteParticipant(
                identity=identity, sid=sid
            )
        return self._remote_participants[sid]

    def _find_participant_for_data(self) -> Optional[_RemoteParticipant]:
        for p in self._remote_participants.values():
            return p
        return None

    def _parse_msid_from_sdp(self, sdp: str) -> None:
        """Extract track SID → participant SID from SDP msid attributes.

        LiveKit encodes this as:   a=msid:<participant_sid> <track_sid>
        """
        for line in sdp.splitlines():
            line = line.strip()
            if line.startswith("a=msid:"):
                parts = line[len("a=msid:"):].split()
                if len(parts) >= 2:
                    p_sid, t_sid = parts[0], parts[1]
                    self._msid_to_participant[t_sid] = p_sid
                    log.debug("[aiortc] msid: track %s → participant %s", t_sid, p_sid)

    async def _send_data(self, payload: bytes) -> None:
        """Send bytes over the WebRTC data channel."""
        if self._data_channel and self._data_channel.readyState == "open":
            self._data_channel.send(payload)
        else:
            log.debug("[aiortc] Data channel not ready, dropping message")

    async def _ws_send(self, req: lk_rtc.SignalRequest) -> None:
        if self._ws:
            async with self._ws_lock:
                await self._ws.send(req.SerializeToString())


# ── Public factory functions (mirrors livekit.rtc API surface) ────────────────

def make_room() -> AiortcRoom:
    """Create a new LiveKit room using the aiortc backend."""
    return AiortcRoom()


def make_audio_stream(track: _RemoteAudioTrack,
                      sample_rate: int = 16000,
                      num_channels: int = 1) -> _AudioStream:
    """Create an AudioStream compatible with voice_livekit._collect_audio_stream."""
    return _AudioStream(track, sample_rate=sample_rate, num_channels=num_channels)
