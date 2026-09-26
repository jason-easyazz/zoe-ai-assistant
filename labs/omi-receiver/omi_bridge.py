#!/usr/bin/env python3
"""Omi pendant → BLE → Opus decode → rolling WAV. Lab receiver for program item B9.1.

LAB ONLY. Runs on a laptop or the Pi panel, never on the Orin; nothing in
``services/`` imports this. Plan: ``docs/architecture/omi-integration-plan.md`` §2.1
(protocol) and §6 (P0 gate).

Wire format (verified against the firmware, not the plan's paraphrase):
``omi/firmware/omi/src/lib/core/transport.c`` :: ``push_to_gatt()`` builds every
audio notification as::

    pusher_temp_data[0] = id & 0xFF;          // u16 packet id, little-endian
    pusher_temp_data[1] = (id >> 8) & 0xFF;
    pusher_temp_data[2] = index;              // u8 fragment index within one codec frame
    memcpy(pusher_temp_data + 3, buffer + offset, packet_size);

with ``static uint16_t packet_next_index`` (wraps at 65536) and ``uint32_t id =
packet_next_index++`` **inside the fragment loop** — so the id advances per
NOTIFICATION, not per frame, and a frame split over the MTU arrives as consecutive
ids with fragment index 0, 1, 2… There is no end-of-frame marker: a frame is
complete when the next fragment-0 arrives (or the stream ends). At the MTU BlueZ
normally negotiates (247) a 20 ms Opus frame (≤ ``CODEC_OUTPUT_MAX_BYTES`` = 160
bytes) is a single fragment, so packet gaps == frame gaps in practice.

Codec ids (``sdks/device/PROTOCOL.md``): ``0`` PCM16 16 kHz, ``1`` PCM16 8 kHz,
``20`` Opus 10 ms frames (DevKit), ``21`` Opus FS320 20 ms frames (Omi CV1 —
``omi/firmware/omi/src/lib/core/config.h`` ``#define CODEC_ID 21``). Both Opus ids
decode to 16 kHz mono PCM16; they differ only in frame duration. ``--codec`` is the
assumption until the pendant answers ``19b10002``: a different supported id rebuilds
the decoder before audio flows, an unsupported one (1, 10, 11) aborts the run.

Decoding: ``opuslib`` (pure-Python ctypes binding to the system ``libopus``; the
wheel is ``py3-none-any`` so it runs on aarch64/Pi with ``apt install libopus0``).
``pyogg`` was rejected: its Linux wheels ship no aarch64 binary and it also falls
back to the system library, so it adds nothing. Import is lazy so the framer and the
tests run without it.

Disconnects: the official SDK never observes them (BasedHardware/omi #13290);
this bridge registers bleak's ``disconnected_callback`` itself and reconnects with
backoff when ``--reconnect`` is set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import struct
import sys
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

log = logging.getLogger("omi_bridge")

# --- UUIDs (sdks/device/dart/lib/uuids.dart, sdks/python/omi/constants.py) ---------
OMI_SERVICE_UUID = "19b10000-e8f2-537e-4f6c-d104768a1214"
AUDIO_DATA_UUID = "19b10001-e8f2-537e-4f6c-d104768a1214"
AUDIO_CODEC_UUID = "19b10002-e8f2-537e-4f6c-d104768a1214"
BUTTON_TRIGGER_UUID = "23ba7925-0000-1000-7450-346eac492e92"
STORAGE_SERVICE_UUID = "30295780-4301-eabd-2904-2849adfeae43"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
DIS_MODEL_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
DIS_FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
DIS_HARDWARE_UUID = "00002a27-0000-1000-8000-00805f9b34fb"

# --- wire constants ---------------------------------------------------------------
HEADER_BYTES = 3            # u16 LE packet id + u8 fragment index
INDEX_MOD = 1 << 16         # packet_next_index is uint16_t
SAMPLE_RATE = 16000
OPUS_MAX_FRAME_SAMPLES = 960  # decode buffer bound (SDK OPUS_FRAME_SAMPLES), not the wire size

CODEC_PCM16_16K = 0
CODEC_PCM16_8K = 1
CODEC_OPUS_10MS = 20
CODEC_OPUS_FS320 = 21
CODEC_NAMES = {
    CODEC_PCM16_16K: "pcm16-16k",
    CODEC_PCM16_8K: "pcm16-8k",
    10: "ulaw-16k",
    11: "ulaw-8k",
    CODEC_OPUS_10MS: "opus-10ms",
    CODEC_OPUS_FS320: "opus-fs320-20ms",
}
FRAME_SAMPLES_BY_CODEC = {CODEC_OPUS_10MS: 160, CODEC_OPUS_FS320: 320}
SUPPORTED_CODECS = (CODEC_PCM16_16K, CODEC_OPUS_10MS, CODEC_OPUS_FS320)


# --- packet parsing + frame reassembly ------------------------------------------
@dataclass(frozen=True)
class Packet:
    packet_id: int
    fragment: int
    payload: bytes


def parse_packet(data: bytes) -> Optional[Packet]:
    """Split one notification into header + payload. ``None`` if shorter than the header."""
    if len(data) < HEADER_BYTES:
        return None
    packet_id = data[0] | (data[1] << 8)
    return Packet(packet_id=packet_id, fragment=data[2], payload=bytes(data[HEADER_BYTES:]))


def build_packet(packet_id: int, fragment: int, payload: bytes) -> bytes:
    """Inverse of ``parse_packet`` — the firmware's byte layout, for fixtures and tests."""
    return bytes((packet_id & 0xFF, (packet_id >> 8) & 0xFF, fragment & 0xFF)) + bytes(payload)


@dataclass
class FramerStats:
    packets_received: int = 0
    packets_lost: int = 0        # sum of forward jumps in the packet id
    gap_events: int = 0
    largest_gap: int = 0
    silence_capped_events: int = 0  # gap fills truncated by AudioPipeline's bound (see max_gap_fill_s)
    reordered: int = 0           # id behind the expected one (duplicate / late) — dropped
    truncated: int = 0           # shorter than the 3-byte header
    empty: int = 0               # header only, no payload
    orphan_fragments: int = 0    # fragment > 0 with no frame in progress
    wraps: int = 0               # packet id wrapped 65535 → 0
    resyncs: int = 0             # expected id re-anchored (reconnect, or the pendant's counter restarted)
    frames_complete: int = 0
    frames_dropped: int = 0      # a fragment went missing mid-frame

    @property
    def packets_expected(self) -> int:
        return self.packets_received + self.packets_lost

    @property
    def gap_pct(self) -> float:
        return 100.0 * self.packets_lost / self.packets_expected if self.packets_expected else 0.0


class OmiFramer:
    """Reassemble Opus frames from Omi audio notifications and account for gaps.

    ``detect_gaps=False`` exists ONLY as the negative control for the test-suite:
    it makes the framer follow whatever id arrives, so a lossy stream reports zero
    loss — the gap test must go red under it. Never use it for a real capture.
    """

    BEHIND_STREAK_RESYNC = 8  # this many consecutive "behind" ids = the counter restarted, not reordering

    def __init__(self, *, detect_gaps: bool = True) -> None:
        self.stats = FramerStats()
        self.detect_gaps = detect_gaps
        self._expected: Optional[int] = None
        self._last_id: Optional[int] = None
        self._partial: Optional[bytearray] = None
        self._partial_start = -1    # packet id of the pending frame's fragment 0
        self._partial_fragment = -1
        self._partial_broken = False
        self._lost_since_last_call = 0
        self._fragmenting = False   # set once any fragment index > 0 is seen (an MTU property)
        self._behind_streak = 0

    def resync(self, *, new_connection: bool = False) -> None:
        """Forget the expected id (call on every (re)connect: the firmware counter may restart).

        ``new_connection`` also forgets whether the stream fragments: the MTU — and with
        it fragmentation — is negotiated per connection, so an earlier fragmented session
        must not make a later unfragmented one drop whole frames on a gap.
        """
        if self._expected is not None:
            self.stats.resyncs += 1
        self._expected = self._last_id = None
        self._behind_streak = 0
        if new_connection:
            self._fragmenting = False

    def push(self, data: bytes) -> list[bytes]:
        """Feed one notification; return the frames it completed (0, 1).

        A completed frame always PRECEDES any gap this notification revealed (a frame
        is only ever finished by the packet after it), so callers writing gap silence
        must write the returned frames first.
        """
        st = self.stats
        pkt = parse_packet(data)
        if pkt is None:
            st.truncated += 1
            return []
        st.packets_received += 1
        if not pkt.payload:
            st.empty += 1

        gap = False
        prev_last = self._last_id
        if self._expected is not None and self.detect_gaps:
            delta = (pkt.packet_id - self._expected) % INDEX_MOD
            if delta == 0:
                pass
            elif delta < INDEX_MOD // 2:
                st.packets_lost += delta
                st.gap_events += 1
                st.largest_gap = max(st.largest_gap, delta)
                self._lost_since_last_call += delta
                gap = True
                log.warning("packet gap: expected %d got %d (%d lost)", self._expected, pkt.packet_id, delta)
            elif self._behind_streak + 1 >= self.BEHIND_STREAK_RESYNC:
                log.warning("%d consecutive packets behind the expected id — counter restarted; resyncing at %d",
                            self.BEHIND_STREAK_RESYNC, pkt.packet_id)
                self.resync()
            else:
                st.reordered += 1
                self._behind_streak += 1
                return []
        self._behind_streak = 0
        if self._last_id is not None and pkt.packet_id < self._last_id:
            st.wraps += 1
        self._last_id = pkt.packet_id
        self._expected = (pkt.packet_id + 1) % INDEX_MOD

        completed: list[bytes] = []
        if pkt.fragment == 0:
            # A frame is complete only when the next fragment-0 arrives, so a gap right
            # before a fragment-0 is ambiguous: the lost ids may have been the pending
            # frame's trailing fragments or whole frames. Fragmentation is fixed by the
            # MTU for the connection: if this stream has never fragmented the pending
            # frame is whole and is kept; if it has, it may be incomplete and is dropped.
            if gap and self._partial is not None and self._fragmenting:
                self._partial_broken = True
            done = self._finish_partial()
            if done is not None:
                completed.append(done)
            self._partial = bytearray(pkt.payload)
            self._partial_start = pkt.packet_id
            self._partial_fragment = 0
            self._partial_broken = False
        else:
            self._fragmenting = True
            # Fragment indices count from 0 inside one frame with consecutive ids, so the
            # index says which id carried this frame's fragment 0.
            frame_start = (pkt.packet_id - pkt.fragment) % INDEX_MOD
            if self._partial is None:
                st.orphan_fragments += 1
            elif frame_start == self._partial_start:
                if pkt.fragment == self._partial_fragment + 1:
                    self._partial.extend(pkt.payload)
                    self._partial_fragment = pkt.fragment
                else:
                    self._partial_broken = True   # a fragment of THIS frame went missing
            else:
                # A new frame began at frame_start inside the gap, so the pending frame
                # ended at frame_start-1: it is whole iff its last fragment was the last
                # id received. Emit it; the new frame has lost its fragment 0 and is
                # carried as a broken partial so its later fragments are not orphans.
                if prev_last != (frame_start - 1) % INDEX_MOD:
                    self._partial_broken = True
                done = self._finish_partial()
                if done is not None:
                    completed.append(done)
                self._partial = bytearray()
                self._partial_start = frame_start
                self._partial_fragment = pkt.fragment
                self._partial_broken = True
        return completed

    def flush(self) -> list[bytes]:
        """Emit the frame in progress (end of stream / disconnect)."""
        done = self._finish_partial()
        self._partial = None
        return [done] if done is not None else []

    def take_lost(self) -> int:
        """Packets lost since the previous call (for gap concealment)."""
        n, self._lost_since_last_call = self._lost_since_last_call, 0
        return n

    def _finish_partial(self) -> Optional[bytes]:
        if self._partial is None:
            return None
        frame = bytes(self._partial)
        broken = self._partial_broken or not frame
        self._partial = None
        if broken:
            self.stats.frames_dropped += 1
            return None
        self.stats.frames_complete += 1
        return frame


# --- decoders --------------------------------------------------------------------
class Decoder(Protocol):
    frame_samples: int

    def decode(self, frame: bytes) -> bytes:  # PCM16 LE mono @ 16 kHz, b"" on failure
        ...


class OpusDecoder16k:
    """opuslib-backed decoder. ``frame_samples`` is only used for gap concealment."""

    def __init__(self, frame_samples: int = 320) -> None:
        import opuslib  # lazy: the framer/tests must import without it

        self._opuslib = opuslib
        self._dec = opuslib.Decoder(SAMPLE_RATE, 1)
        self.frame_samples = frame_samples
        self.errors = 0

    def decode(self, frame: bytes) -> bytes:
        try:
            return self._dec.decode(frame, OPUS_MAX_FRAME_SAMPLES, decode_fec=False)
        except self._opuslib.OpusError as exc:  # pragma: no cover - needs a corrupt frame
            self.errors += 1
            log.warning("opus decode error: %s", exc)
            return b""


class PcmPassthroughDecoder:
    """Codec 0: the payload already is PCM16 16 kHz."""

    frame_samples = 320

    def decode(self, frame: bytes) -> bytes:
        return frame[: len(frame) - (len(frame) % 2)]


class StubDecoder:
    """Test double: every frame becomes ``frame_samples`` samples of a constant."""

    def __init__(self, frame_samples: int = 320, value: int = 1000) -> None:
        self.frame_samples = frame_samples
        self.value = value
        self.frames: list[bytes] = []

    def decode(self, frame: bytes) -> bytes:
        self.frames.append(frame)
        return struct.pack(f"<{self.frame_samples}h", *([self.value] * self.frame_samples))


def make_decoder(codec_id: int) -> Decoder:
    if codec_id == CODEC_PCM16_16K:
        return PcmPassthroughDecoder()
    if codec_id in FRAME_SAMPLES_BY_CODEC:
        return OpusDecoder16k(frame_samples=FRAME_SAMPLES_BY_CODEC[codec_id])
    raise ValueError(f"unsupported codec id {codec_id} ({CODEC_NAMES.get(codec_id, 'unknown')})")


# --- WAV sink --------------------------------------------------------------------
class WavSink:
    """Rolling 16 kHz mono PCM16 WAV writer: a new file every ``roll_seconds``."""

    def __init__(self, out_dir: Path, *, roll_seconds: float = 600.0, prefix: str = "omi",
                 sample_rate: int = SAMPLE_RATE) -> None:
        self.out_dir = Path(out_dir)
        self.roll_samples = max(1, int(roll_seconds * sample_rate))
        self.prefix = prefix
        self.sample_rate = sample_rate
        self.files: list[Path] = []
        self.total_samples = 0
        self._wav: Optional[wave.Wave_write] = None
        self._file_samples = 0

    def write(self, pcm: bytes) -> None:
        """Append PCM16, splitting it across files so no file exceeds ``roll_samples``."""
        offset = 0
        while offset < len(pcm):
            if self._wav is None or self._file_samples >= self.roll_samples:
                self._open_next()
            assert self._wav is not None
            room = 2 * (self.roll_samples - self._file_samples)
            chunk = pcm[offset:offset + room]
            self._wav.writeframes(chunk)
            n = len(chunk) // 2
            self._file_samples += n
            self.total_samples += n
            offset += len(chunk)

    def _open_next(self) -> None:
        self.close()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.out_dir / f"{self.prefix}_{stamp}_{len(self.files):03d}.wav"
        w = wave.open(str(path), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(self.sample_rate)
        self._wav = w
        self._file_samples = 0
        self.files.append(path)
        log.info("writing %s", path)

    def close(self) -> None:
        if self._wav is not None:
            self._wav.close()
            self._wav = None

    @property
    def seconds(self) -> float:
        return self.total_samples / self.sample_rate


# --- pipeline: packets → frames → PCM → WAV -------------------------------------
class AudioPipeline:
    """Hardware-free core: feed notifications in, WAV comes out.

    ``fill_gaps`` writes one frame of silence per lost packet so the WAV keeps
    wall-clock length across drops (exact when frames are unfragmented, which is
    the CV1 at the normal 247-byte MTU; an approximation otherwise).

    The fill is BOUNDED, because the packet id is untrusted input: a notification
    that jumps the id by 32767 would otherwise write ~21 MB of silence, and a device
    repeating that fills the disk. ``max_gap_fill_s`` caps one gap's fill, and the
    total silence may never run ahead of the wall clock by more than that same
    allowance (legitimate loss takes real time, so real fills stay under it). A
    truncated fill is counted in ``FramerStats.silence_capped_events``; the packet
    accounting (``packets_lost``, gap %) is untouched. ``None`` disables the bound
    (tests only).
    """

    def __init__(self, framer: OmiFramer, decoder: Decoder, sink: WavSink, *, fill_gaps: bool = True,
                 max_gap_fill_s: Optional[float] = 5.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.framer = framer
        self.decoder = decoder
        self.sink = sink
        self.fill_gaps = fill_gaps
        self.max_gap_fill_s = max_gap_fill_s
        self.silence_samples = 0
        self._clock = clock
        self._started: Optional[float] = None

    def on_packet(self, data: bytes) -> None:
        if self._started is None:
            self._started = self._clock()
        frames = self.framer.push(data)
        lost = self.framer.take_lost()
        for frame in frames:            # a completed frame always precedes the gap
            self.sink.write(self.decoder.decode(frame))
        if lost and self.fill_gaps:
            n = self._bounded_fill(lost * self.decoder.frame_samples, lost)
            if n:
                self.sink.write(bytes(2 * n))
                self.silence_samples += n

    def _bounded_fill(self, samples: int, lost: int) -> int:
        if self.max_gap_fill_s is None:
            return samples
        rate = self.sink.sample_rate
        cap = int(self.max_gap_fill_s * rate)
        elapsed = self._clock() - (self._started or 0.0)
        budget = int(elapsed * rate) + cap - self.silence_samples
        allowed = max(0, min(samples, cap, budget))
        if allowed < samples:
            self.framer.stats.silence_capped_events += 1
            log.warning("gap fill capped: %d lost packets = %.1f s of silence, wrote %.2f s (bound %.1f s/gap, total ≤ wall clock + bound)",
                        lost, samples / rate, allowed / rate, self.max_gap_fill_s)
        return allowed

    def flush(self) -> None:
        for frame in self.framer.flush():
            self.sink.write(self.decoder.decode(frame))


# --- BLE transport seam ------------------------------------------------------------
NotifyCallback = Callable[[bytes], None]


class Transport(Protocol):
    """The whole BLE surface the bridge needs; ``BleakTransport`` is the real one."""

    async def connect(self, on_disconnect: Callable[[], None]) -> None: ...
    async def disconnect(self) -> None: ...
    async def read(self, uuid: str) -> Optional[bytes]: ...
    async def start_notify(self, uuid: str, callback: NotifyCallback) -> None: ...
    async def stop_notify(self, uuid: str) -> None: ...
    @property
    def is_connected(self) -> bool: ...
    @property
    def identity(self) -> str: ...
    def has_service(self, uuid: str) -> Optional[bool]: ...  # None = discovery unavailable


class BleakTransport:
    """bleak 0.22.3 on BlueZ. Registers ``disconnected_callback`` itself (SDK bug #13290)."""

    def __init__(self, *, name: Optional[str] = None, address: Optional[str] = None,
                 adapter: Optional[str] = None, scan_timeout: float = 15.0) -> None:
        if not (name or address):
            raise ValueError("need --device-name or --address")
        self.name, self.address, self.adapter, self.scan_timeout = name, address, adapter, scan_timeout
        self._client = None
        self._identity = address or name or "?"

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def is_connected(self) -> bool:
        return bool(self._client is not None and self._client.is_connected)

    async def connect(self, on_disconnect: Callable[[], None]) -> None:
        from bleak import BleakClient, BleakScanner  # lazy: tests never import bleak

        scan_kwargs = {"timeout": self.scan_timeout}
        if self.adapter:
            scan_kwargs["adapter"] = self.adapter
        if self.address:
            device = await BleakScanner.find_device_by_address(self.address, **scan_kwargs)
        else:
            device = await BleakScanner.find_device_by_name(self.name, **scan_kwargs)
        if device is None:
            raise ConnectionError(f"pendant not found (name={self.name!r} address={self.address!r})")
        self._identity = f"{device.name} [{device.address}]"
        client_kwargs = {"adapter": self.adapter} if self.adapter else {}
        self._client = BleakClient(device, disconnected_callback=lambda _c: on_disconnect(), **client_kwargs)
        await self._client.connect()

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    def has_service(self, uuid: str) -> Optional[bool]:
        try:
            return self._client.services.get_service(uuid) is not None
        except Exception as exc:  # services not resolved yet
            log.debug("service lookup %s: %s", uuid, exc)
            return None

    async def read(self, uuid: str) -> Optional[bytes]:
        try:
            return bytes(await self._client.read_gatt_char(uuid))
        except Exception as exc:  # characteristic absent / not readable on this firmware
            log.info("read %s failed: %s", uuid, exc)
            return None

    async def start_notify(self, uuid: str, callback: NotifyCallback) -> None:
        await self._client.start_notify(uuid, lambda _char, data: callback(bytes(data)))

    async def stop_notify(self, uuid: str) -> None:
        try:
            await self._client.stop_notify(uuid)
        except Exception as exc:  # already gone
            log.debug("stop_notify %s: %s", uuid, exc)


# --- the bridge --------------------------------------------------------------------
@dataclass
class BridgeSummary:
    device: str = ""
    model: Optional[str] = None
    firmware: Optional[str] = None
    hardware: Optional[str] = None
    storage_service_seen: Optional[bool] = None
    codec_id: Optional[int] = None
    codec: Optional[str] = None
    duration_s: float = 0.0
    connected_s: float = 0.0
    wav_seconds: float = 0.0
    packets_received: int = 0
    packets_lost: int = 0
    packets_expected: int = 0
    gap_pct: float = 0.0
    gap_events: int = 0
    largest_gap: int = 0
    silence_capped_events: int = 0
    frames_complete: int = 0
    frames_dropped: int = 0
    truncated: int = 0
    reordered: int = 0
    wraps: int = 0
    resyncs: int = 0
    reconnects: int = 0
    connect_failures: int = 0
    sessions: int = 0                      # successful connections
    disconnected_s: float = 0.0            # wall time spent in outages (drop → next successful connect)
    longest_connected_s: float = 0.0       # longest single uninterrupted session
    max_disconnected_s: float = 0.0        # the outage budget the verdict used
    interrupted: bool = False              # Ctrl-C ended the run (the summary is still complete)
    battery_start: Optional[int] = None
    battery_end: Optional[int] = None
    battery_drop_per_hour: Optional[float] = None
    button_events: list[int] = field(default_factory=list)
    wav_files: list[str] = field(default_factory=list)
    gap_gate_pass: Optional[bool] = None   # P0 gate: packet gaps < 1 % AND outages within max_disconnected_s
    gate_fail_reasons: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


class Bridge:
    def __init__(self, transport: Transport, pipeline: AudioPipeline, *, reconnect: bool = True,
                 max_seconds: Optional[float] = None, backoff: tuple[float, ...] = (1, 2, 4, 8, 16, 30),
                 expected_codec: Optional[int] = None,
                 decoder_factory: Optional[Callable[[int], Decoder]] = None,
                 max_disconnected_s: float = 2.0) -> None:
        self.transport = transport
        self.pipeline = pipeline
        self.reconnect = reconnect
        self.max_seconds = max_seconds
        self.backoff = backoff
        # Every reconnect resyncs the packet id, so packets lost DURING an outage never
        # reach gap_pct; the verdict therefore also bounds the total outage wall time.
        self.max_disconnected_s = max_disconnected_s
        self._outage_started: Optional[float] = None
        # ``expected_codec`` is what ``pipeline.decoder`` was built for; when the pendant
        # reports a different supported codec the decoder is rebuilt with this factory
        # before any audio flows (an unsupported one aborts the run).
        self.expected_codec = expected_codec
        self.decoder_factory = decoder_factory or make_decoder
        self.summary = BridgeSummary(device=transport.identity)
        self._disconnected: Optional[asyncio.Event] = None

    def _remaining(self, start: float) -> Optional[float]:
        if self.max_seconds is None:
            return None
        return max(0.0, self.max_seconds - (time.monotonic() - start))

    async def run(self) -> BridgeSummary:
        start = time.monotonic()
        loop = asyncio.get_running_loop()
        attempt = 0
        probed = False   # identity + codec actually read on a live link (a probe a drop cut short is repeated)
        try:
            while True:
                remaining = self._remaining(start)
                if remaining is not None and remaining <= 0:
                    break
                self._disconnected = asyncio.Event()
                ev = self._disconnected
                try:
                    await self.transport.connect(lambda: loop.call_soon_threadsafe(ev.set))
                except Exception as exc:
                    self.summary.connect_failures += 1
                    if not self.reconnect:
                        log.error("connect failed: %s", exc)
                        break
                    delay = self.backoff[min(attempt, len(self.backoff) - 1)]
                    attempt += 1
                    log.warning("connect failed (%s); retry in %.0fs", exc, delay)
                    await asyncio.sleep(delay)
                    continue
                attempt = 0
                self._end_outage()
                self.summary.sessions += 1
                self.pipeline.framer.resync(new_connection=True)
                self.summary.device = self.transport.identity
                log.info("connected to %s", self.summary.device)
                session_start = time.monotonic()
                timed_out = fatal = False
                try:
                    if not probed:
                        probed = await self._read_identity()
                    await self.transport.start_notify(AUDIO_DATA_UUID, self.pipeline.on_packet)
                    await self._subscribe_optional(BUTTON_TRIGGER_UUID, self._on_button)
                    await self._subscribe_optional(BATTERY_LEVEL_UUID, self._on_battery)
                    timed_out = await self._wait_for_drop(ev, self._remaining(start))
                except Exception as exc:
                    # The link dying during setup (identity reads / start_notify) is a DROP
                    # and follows the reconnect path; an error raised while the link is
                    # still up (unsupported codec, decoder build) is configuration → fatal.
                    dropped = ev.is_set() or not self.transport.is_connected
                    if dropped and self.reconnect:
                        log.warning("link dropped during setup (%s)", exc)
                    else:
                        log.error("session aborted: %s", exc)
                        fatal = True
                finally:
                    session_s = time.monotonic() - session_start
                    self.summary.connected_s += session_s
                    self.summary.longest_connected_s = max(self.summary.longest_connected_s, session_s)
                    self.pipeline.flush()
                if fatal:
                    await self.transport.disconnect()
                    break
                if timed_out:
                    await self._read_battery(final=True)
                    await self.transport.stop_notify(AUDIO_DATA_UUID)
                    await self.transport.disconnect()
                    break
                log.warning("disconnected from %s", self.summary.device)
                await self.transport.disconnect()
                if not self.reconnect:
                    break
                self._outage_started = time.monotonic()
                self.summary.reconnects += 1
                delay = self.backoff[0]
                log.info("reconnecting in %.0fs", delay)
                await asyncio.sleep(delay)
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("interrupted; finishing")
            self.summary.interrupted = True
            self.pipeline.flush()
            if self.transport.is_connected:
                await self._read_battery(final=True)
                await self.transport.disconnect()
        finally:
            self.pipeline.sink.close()
        self._finish(start)
        return self.summary

    def _end_outage(self) -> None:
        if self._outage_started is not None:
            self.summary.disconnected_s += time.monotonic() - self._outage_started
            self._outage_started = None

    async def _wait_for_drop(self, ev: asyncio.Event, timeout: Optional[float]) -> bool:
        """True when the run time expired, False when the pendant dropped."""
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
            return False
        except asyncio.TimeoutError:
            return True

    async def _subscribe_optional(self, uuid: str, cb: NotifyCallback) -> None:
        try:
            await self.transport.start_notify(uuid, cb)
        except Exception as exc:
            log.info("no notify on %s: %s", uuid, exc)

    def _on_button(self, data: bytes) -> None:
        code = data[0] if data else -1
        self.summary.button_events.append(code)
        log.info("button event %d (1 tap, 2 double, 3 long, 4 down, 5 up)", code)

    def _on_battery(self, data: bytes) -> None:
        if data:
            self.summary.battery_end = data[0]
            log.info("battery %d%%", data[0])

    async def _read_text(self, uuid: str) -> Optional[str]:
        raw = await self.transport.read(uuid)
        return raw.decode("utf-8", "replace").strip("\x00 ") if raw else None

    async def _read_identity(self) -> bool:
        """Read DIS + codec + battery. Returns True only when the codec was actually read on
        a link that is still up: ``Transport.read`` answers ``None`` for a failed GATT read,
        so a probe cut short by a drop would otherwise leave the assumed ``--codec`` in place
        for the whole recovered capture. An incomplete probe is repeated on the next connect."""
        s = self.summary
        s.model = await self._read_text(DIS_MODEL_UUID) or s.model
        s.firmware = await self._read_text(DIS_FIRMWARE_UUID) or s.firmware
        s.hardware = await self._read_text(DIS_HARDWARE_UUID) or s.hardware
        s.storage_service_seen = self.transport.has_service(STORAGE_SERVICE_UUID)
        codec_raw = await self.transport.read(AUDIO_CODEC_UUID)
        if codec_raw:
            s.codec_id = codec_raw[0]
            s.codec = CODEC_NAMES.get(s.codec_id, "unknown")
            if s.codec_id not in SUPPORTED_CODECS:
                raise RuntimeError(f"pendant reports codec {s.codec_id} ({s.codec}); this bridge decodes {SUPPORTED_CODECS}")
            if self.expected_codec is not None and s.codec_id != self.expected_codec:
                log.warning("pendant reports codec %d (%s) but --codec was %d (%s): rebuilding the decoder for %d",
                            s.codec_id, s.codec, self.expected_codec, CODEC_NAMES.get(self.expected_codec, "?"), s.codec_id)
                self.pipeline.decoder = self.decoder_factory(s.codec_id)
                self.expected_codec = s.codec_id
        log.info("model=%s fw=%s hw=%s codec=%s", s.model, s.firmware, s.hardware, s.codec)
        await self._read_battery(final=False)
        complete = bool(codec_raw) and self.transport.is_connected
        if not complete:
            log.warning("identity probe incomplete (codec %s, connected %s); will re-read on the next connect",
                        "read" if codec_raw else "unread", self.transport.is_connected)
        return complete

    async def _read_battery(self, *, final: bool) -> None:
        raw = await self.transport.read(BATTERY_LEVEL_UUID)
        if raw:
            if final:
                self.summary.battery_end = raw[0]
            elif self.summary.battery_start is None:   # a repeated probe keeps the first reading
                self.summary.battery_start = raw[0]
            log.info("battery %d%%", raw[0])

    def _finish(self, start: float) -> None:
        s, st, sink = self.summary, self.pipeline.framer.stats, self.pipeline.sink
        s.duration_s = round(time.monotonic() - start, 1)
        s.wav_seconds = round(sink.seconds, 2)
        s.wav_files = [str(p) for p in sink.files]
        s.packets_received, s.packets_lost, s.packets_expected = st.packets_received, st.packets_lost, st.packets_expected
        s.gap_pct = round(st.gap_pct, 3)
        s.gap_events, s.largest_gap = st.gap_events, st.largest_gap
        s.silence_capped_events = st.silence_capped_events
        s.frames_complete, s.frames_dropped = st.frames_complete, st.frames_dropped
        s.truncated, s.reordered, s.wraps, s.resyncs = st.truncated, st.reordered, st.wraps, st.resyncs
        if s.battery_start is not None and s.battery_end is not None and s.connected_s > 0:
            s.battery_drop_per_hour = round((s.battery_start - s.battery_end) * 3600.0 / s.connected_s, 2)
        self._end_outage()   # a run that ends mid-outage still counts that outage
        s.connected_s = round(s.connected_s, 2)
        s.disconnected_s = round(s.disconnected_s, 3)
        s.longest_connected_s = round(s.longest_connected_s, 2)
        s.max_disconnected_s = self.max_disconnected_s
        reasons = []
        if st.packets_expected == 0:
            reasons.append("no packets received")
        elif st.gap_pct >= 1.0:
            reasons.append(f"packet gaps {st.gap_pct:.3f}% ≥ 1%")
        if s.disconnected_s > self.max_disconnected_s:
            reasons.append(f"disconnected {s.disconnected_s:.1f}s over {s.reconnects} outage(s) > {self.max_disconnected_s:g}s budget")
        s.gate_fail_reasons = reasons
        s.gap_gate_pass = not reasons


def format_summary(s: BridgeSummary) -> str:
    gate = "PASS" if s.gap_gate_pass else ("FAIL" if s.gap_gate_pass is False else "n/a")
    bat = f"{s.battery_start}% → {s.battery_end}%" if s.battery_start is not None else "unreadable"
    if s.battery_drop_per_hour is not None:
        bat += f" ({s.battery_drop_per_hour:+.1f} pts/h drop)"
    capped = f", {s.silence_capped_events} fill(s) capped" if s.silence_capped_events else ""
    why = f" ({'; '.join(s.gate_fail_reasons)})" if s.gate_fail_reasons else ""
    return (
        f"device={s.device} model={s.model} fw={s.firmware} codec={s.codec}\n"
        f"run {s.duration_s:.0f}s{' (interrupted)' if s.interrupted else ''}, connected {s.connected_s:.0f}s, "
        f"audio {s.wav_seconds:.1f}s in {len(s.wav_files)} file(s)\n"
        f"packets {s.packets_received} received / {s.packets_lost} lost = {s.gap_pct:.3f}% gaps "
        f"({s.gap_events} events, largest {s.largest_gap}{capped}) → P0 gap gate {gate}{why}\n"
        f"frames {s.frames_complete} ok / {s.frames_dropped} dropped; truncated {s.truncated}, reordered {s.reordered}, wraps {s.wraps}\n"
        f"sessions {s.sessions}, reconnects {s.reconnects} (connect failures {s.connect_failures}), "
        f"disconnected {s.disconnected_s:.1f}s (budget {s.max_disconnected_s:g}s), longest uninterrupted {s.longest_connected_s:.0f}s; "
        f"battery {bat}; buttons {s.button_events}\n"
        f"storage service {STORAGE_SERVICE_UUID[:8]}… present: {s.storage_service_seen} (stock CV1 firmware records to SD whenever powered — see B9.0)"
    )


# --- CLI -----------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--device-name", help="advertised name (usually 'Omi')")
    who.add_argument("--address", help="BLE address, e.g. AA:BB:CC:DD:EE:FF")
    ap.add_argument("--adapter", default=None, help="BlueZ adapter, e.g. hci0")
    ap.add_argument("--out", type=Path, default=Path("./omi-capture"), help="directory for WAVs + summary.json")
    ap.add_argument("--max-minutes", type=float, default=None, help="stop after this long (default: until Ctrl-C)")
    ap.add_argument("--roll-minutes", type=float, default=10.0, help="start a new WAV every N minutes")
    ap.add_argument("--reconnect", action="store_true", help="reconnect with backoff when the pendant drops")
    ap.add_argument("--scan-timeout", type=float, default=15.0)
    ap.add_argument("--codec", type=int, default=CODEC_OPUS_FS320, choices=sorted(SUPPORTED_CODECS),
                    help="codec assumed until the pendant answers (CV1 reports 21); a different supported "
                         "answer rebuilds the decoder, an unsupported one aborts")
    ap.add_argument("--max-disconnected-seconds", type=float, default=2.0,
                    help="gate FAILS if the run spent longer than this in outages (reconnects resync the "
                         "packet id, so outage loss never shows in the gap %%)")
    ap.add_argument("--no-fill-gaps", action="store_true", help="do not write silence for lost packets")
    ap.add_argument("--max-gap-fill-seconds", type=float, default=5.0,
                    help="most silence written for one packet gap; total silence never leads the wall clock by more")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


async def amain(args: argparse.Namespace) -> BridgeSummary:
    transport = BleakTransport(name=args.device_name, address=args.address, adapter=args.adapter,
                               scan_timeout=args.scan_timeout)
    pipeline = AudioPipeline(OmiFramer(), make_decoder(args.codec),
                             WavSink(args.out, roll_seconds=args.roll_minutes * 60), fill_gaps=not args.no_fill_gaps,
                             max_gap_fill_s=args.max_gap_fill_seconds)
    bridge = Bridge(transport, pipeline, reconnect=args.reconnect,
                    max_seconds=args.max_minutes * 60 if args.max_minutes else None, expected_codec=args.codec,
                    max_disconnected_s=args.max_disconnected_seconds)
    # Ctrl-C must end the run with a summary. Before Python 3.11 ``asyncio.run`` installs
    # no SIGINT handler, so the KeyboardInterrupt would escape the loop without the bridge
    # ever seeing it; route it into a cancellation the bridge already handles instead.
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    handler_installed = False
    try:
        loop.add_signal_handler(signal.SIGINT, task.cancel)
        handler_installed = True
    except (NotImplementedError, RuntimeError):  # non-Unix loop / not the main thread
        pass
    try:
        return await bridge.run()
    finally:
        if handler_installed:
            loop.remove_signal_handler(signal.SIGINT)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        summary = asyncio.run(amain(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.error("interrupted again before the summary could be finished; no summary.json")
        return 130
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(summary.to_json())
    print(format_summary(summary))
    print(f"summary → {args.out / 'summary.json'}")
    return 0 if summary.gap_gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
