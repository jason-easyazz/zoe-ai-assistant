"""The Omi pendant's SD-ring storage protocol, as pure functions over bytes.

No BLE, no I/O, no clock: everything here is testable on fixture bytes. The wire
facts were read from Omi's MIT-licensed source (2026-10-12, files under
``BasedHardware/omi``):

  * ``omi/firmware/omi/src/lib/core/storage.c``  - the GATT service, the commands
    (``0x10`` INFO, ``0x11`` READ, ``0x12`` ADVANCE, ``0x13`` CLEAR, ``0x03`` STOP)
    and the notifications (``0x01`` ACK, ``0x02`` INFO, ``0x03`` DATA, ``0x04`` DONE,
    ``0x05`` READ_BEGIN). UUIDs ``30295780/81/82-4301-eabd-2904-2849adfeae43``.
  * ``app/lib/services/devices/ring_protocol.dart`` - the app-side codec of the same
    bytes; this module mirrors its layouts (big-endian ints, 444-byte records =
    4-byte timestamp + 440-byte payload packed ``[size:1][frame:size]...``).
  * ``app/lib/services/wals/PENDANT_RING_CUSTODY.md`` - the custody contract
    (capability flags in INFO, ``ring_id``, ``ADVANCE_ID 0x15``).

Omi's code is MIT-licensed (Copyright (c) 2024 Based Hardware Contributors; the
notice is kept in ``services/omi-sync/THIRD_PARTY_NOTICES.md``); the byte layouts
and the ``parse_payload`` boundary rule below are a Python port of the format as
that Dart/C defines it.

DELIBERATELY ABSENT: an encoder for ``CMD_RING_CLEAR`` (``0x13``). Clearing the
ring deletes audio the pendant holds; the sync service has no business sending it,
and not having the encoder makes "we never clear" structural rather than a
convention (the engine test also asserts it on the wire).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

# --- GATT (storage.c) -----------------------------------------------------------
STORAGE_SERVICE_UUID = "30295780-4301-eabd-2904-2849adfeae43"
#: write (commands) + notify (acks, INFO, READ data): BT_GATT_CHRC_WRITE | NOTIFY
STORAGE_DATA_UUID = "30295781-4301-eabd-2904-2849adfeae43"
#: read (16-byte status) + notify
STORAGE_STATUS_UUID = "30295782-4301-eabd-2904-2849adfeae43"
TIME_SYNC_WRITE_UUID = "19b10031-e8f2-537e-4f6c-d104768a1214"   # write u32 LE epoch seconds
TIME_SYNC_READ_UUID = "19b10032-e8f2-537e-4f6c-d104768a1214"    # read  u32 LE epoch seconds
AUDIO_CODEC_UUID = "19b10002-e8f2-537e-4f6c-d104768a1214"
DIS_MODEL_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
DIS_FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

# --- commands -------------------------------------------------------------------
CMD_STOP = 0x03
CMD_INFO = 0x10
CMD_READ = 0x11
CMD_ADVANCE = 0x12
CMD_CLEAR = 0x13          # documented so the engine test can assert it is NEVER sent; no encoder
CMD_ADVANCE_ID = 0x15

# --- notifications --------------------------------------------------------------
NOTIFY_ACK = 0x01
NOTIFY_INFO = 0x02
NOTIFY_DATA = 0x03
NOTIFY_DONE = 0x04
NOTIFY_READ_BEGIN = 0x05
NOTIFY_LIVE_MARK = 0x06

ACK_OK = 0
ACK_INVALID_COMMAND = 6
ACK_STORAGE_NOT_READY = 9
ACK_SEQ_OUT_OF_RANGE = 10
ACK_RING_ID_MISMATCH = 11

# --- custody capability flags (INFO byte 31; PENDANT_RING_CUSTODY.md section 1) -----
CAP_APP_ACK_RECLAIM = 0x01
CAP_LIVE_PERSIST = 0x02
CAP_ADVANCE_IDEMPOTENT = 0x04
CAP_RING_ID = 0x08

# --- records --------------------------------------------------------------------
RECORD_BYTES = 444
TIMESTAMP_BYTES = 4
PAYLOAD_BYTES = RECORD_BYTES - TIMESTAMP_BYTES
#: bit 31 of a record timestamp: the low 31 bits are seconds since boot (RTC unset), not epoch
UPTIME_FLAG = 0x80000000

#: first firmware with the ring protocol (app/lib/services/devices/ring_protocol.dart isRingBufferFirmware)
MIN_RING_FIRMWARE = (3, 0, 20)

CODEC_OPUS_10MS = 20      # DevKit
CODEC_OPUS_FS320 = 21     # Omi CV1: 320 samples / 20 ms at 16 kHz
FRAME_SAMPLES_BY_CODEC = {CODEC_OPUS_10MS: 160, CODEC_OPUS_FS320: 320}
SAMPLE_RATE = 16000


class ProtocolError(ValueError):
    """A notification that does not parse as what its opcode promises."""


# --- firmware gate --------------------------------------------------------------
def parse_firmware(text: object) -> Optional[tuple[int, int, int]]:
    """``"3.0.21"`` / ``"v3.0.21\\x00"`` / ``b"3.0.21"`` -> ``(3, 0, 21)``; ``None`` if not a x.y.z version."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("ascii", "ignore")
    if not isinstance(text, str):
        return None
    cleaned = text.strip().strip("\x00").strip().lstrip("vV")
    parts = cleaned.split(".")
    if len(parts) < 3:
        return None
    nums = []
    for part in parts[:3]:
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            return None
        nums.append(int(digits))
    return (nums[0], nums[1], nums[2])


def supports_ring(firmware: object) -> bool:
    """True iff the DIS firmware string names a version >= 3.0.20. Unparseable = False (refuse)."""
    ver = parse_firmware(firmware)
    return ver is not None and ver >= MIN_RING_FIRMWARE


# --- parsed notifications -------------------------------------------------------
@dataclass(frozen=True)
class RingInfo:
    read_seq: int
    write_seq: int
    capacity_packets: int
    dropped_packets: int
    packet_size: int
    caps: int = 0
    contract_version: int = 0
    ring_id: Optional[int] = None

    @property
    def unread(self) -> int:
        return max(0, self.write_seq - self.read_seq)

    @property
    def has_ring_id(self) -> bool:
        """The ring_id is only trustworthy when the firmware advertises CAP_RING_ID."""
        return self.ring_id is not None and bool(self.caps & CAP_RING_ID)


@dataclass(frozen=True)
class ReadBegin:
    start_seq: int
    packet_count: int


@dataclass(frozen=True)
class Done:
    status: int
    next_seq: int

    @property
    def ok(self) -> bool:
        return self.status == ACK_OK


@dataclass(frozen=True)
class Ack:
    status: int

    @property
    def ok(self) -> bool:
        return self.status == ACK_OK


@dataclass(frozen=True)
class RingStatus:
    """The 16-byte read of ``30295782`` - four little-endian u32 (the only LE structure here)."""
    used_bytes: int
    unread_packets: int
    free_bytes: int
    rtc_valid: bool


def opcode(data: bytes) -> Optional[int]:
    return data[0] if data else None


def parse_info(data: bytes) -> RingInfo:
    """``[0x02][read:u64][write:u64][cap:u32][dropped:u64][pkt:u16]`` (31 bytes) + optional custody tail
    (``[caps:u8][version:u8][ring_id:u64]`` = 41 bytes). A shorter payload is legacy firmware: caps 0."""
    if not data or data[0] != NOTIFY_INFO or len(data) < 31:
        raise ProtocolError("INFO truncated or wrong opcode")
    read_seq, write_seq, cap, dropped, pkt = struct.unpack_from(">QQIQH", data, 1)
    caps = data[31] if len(data) >= 32 else 0
    version = data[32] if len(data) >= 33 else 0
    ring_id = struct.unpack_from(">Q", data, 33)[0] if len(data) >= 41 else None
    return RingInfo(read_seq, write_seq, cap, dropped, pkt, caps, version, ring_id)


def parse_read_begin(data: bytes) -> ReadBegin:
    if not data or data[0] != NOTIFY_READ_BEGIN or len(data) < 13:
        raise ProtocolError("READ_BEGIN truncated or wrong opcode")
    start, count = struct.unpack_from(">QI", data, 1)
    return ReadBegin(start, count)


def parse_done(data: bytes) -> Done:
    if not data or data[0] != NOTIFY_DONE or len(data) < 10:
        raise ProtocolError("DONE truncated or wrong opcode")
    return Done(data[1], struct.unpack_from(">Q", data, 2)[0])


def parse_ack(data: bytes) -> Ack:
    if not data or data[0] != NOTIFY_ACK or len(data) < 2:
        raise ProtocolError("ACK truncated or wrong opcode")
    return Ack(data[1])


def parse_status(data: bytes) -> RingStatus:
    if len(data) < 16:
        raise ProtocolError("status read shorter than 16 bytes")
    used, unread, free, rtc = struct.unpack_from("<IIII", data, 0)
    return RingStatus(used, unread, free, bool(rtc))


# --- commands -------------------------------------------------------------------
def encode_info() -> bytes:
    return bytes([CMD_INFO])


def encode_stop() -> bytes:
    return bytes([CMD_STOP])


def encode_read(start_seq: int, packet_count: Optional[int] = None) -> bytes:
    """``[0x11][start:u64 BE]`` (+ ``[count:u32 BE]``; 0/None streams everything from ``start``)."""
    if start_seq < 0:
        raise ValueError("start_seq must be >= 0")
    if packet_count:
        return struct.pack(">BQI", CMD_READ, start_seq, packet_count)
    return struct.pack(">BQ", CMD_READ, start_seq)


def encode_advance(new_read_seq: int) -> bytes:
    """``[0x12][seq:u64 BE]`` - free every record below ``seq``."""
    if new_read_seq < 0:
        raise ValueError("seq must be >= 0")
    return struct.pack(">BQ", CMD_ADVANCE, new_read_seq)


def encode_advance_id(ring_id: int, new_read_seq: int) -> bytes:
    """``[0x15][ring_id:u64 BE][seq:u64 BE]`` - the incarnation-scoped ADVANCE (custody firmware)."""
    if new_read_seq < 0 or ring_id <= 0:
        raise ValueError("seq must be >= 0 and ring_id non-zero")
    return struct.pack(">BQQ", CMD_ADVANCE_ID, ring_id, new_read_seq)


def encode_time(epoch_s: int) -> bytes:
    """The time-sync write: ``uint32`` little-endian epoch seconds (transport.c ``time_sync_write_handler``)."""
    return struct.pack("<I", int(epoch_s) & 0xFFFFFFFF)


def parse_time(data: bytes) -> int:
    if len(data) < 4:
        raise ProtocolError("time read shorter than 4 bytes")
    return struct.unpack_from("<I", data, 0)[0]


# --- records --------------------------------------------------------------------
class RecordReassembler:
    """NOTIFY_DATA payloads are NOT aligned to record boundaries: buffer and cut 444-byte records."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def append(self, chunk: bytes) -> None:
        self._buf += chunk

    def drain(self) -> list[bytes]:
        out = []
        while len(self._buf) >= RECORD_BYTES:
            out.append(bytes(self._buf[:RECORD_BYTES]))
            del self._buf[:RECORD_BYTES]
        return out

    @property
    def pending(self) -> int:
        return len(self._buf)


def record_timestamp(record: bytes) -> Optional[int]:
    """Epoch seconds from the 4-byte big-endian prefix, or ``None`` when the uptime flag is set (no wall clock)."""
    if len(record) < TIMESTAMP_BYTES:
        raise ProtocolError("record shorter than its timestamp")
    raw = struct.unpack_from(">I", record, 0)[0]
    if raw & UPTIME_FLAG:
        return None
    return raw


def parse_payload(payload: bytes) -> list[bytes]:
    """Split a 440-byte payload into Opus frames: ``[size:1][frame:size]...``, zero bytes are padding.

    The boundary uses ``>=`` exactly like the firmware's overflow rule (transport.c
    ``write_to_storage``): at the edge it writes a trailing size byte WITHOUT its frame
    (the frame goes to the next block) and what follows is stale - it must not be parsed.
    """
    frames: list[bytes] = []
    i = 0
    n = len(payload)
    while i < n - 1:
        size = payload[i]
        if size == 0:
            i += 1
            continue
        if i + 1 + size >= n:
            break
        frames.append(bytes(payload[i + 1:i + 1 + size]))
        i += size + 1
    return frames


def split_record(record: bytes) -> tuple[Optional[int], list[bytes]]:
    """``(epoch-or-None, opus frames)`` of one 444-byte record."""
    if len(record) != RECORD_BYTES:
        raise ProtocolError(f"record is {len(record)} bytes, expected {RECORD_BYTES}")
    return record_timestamp(record), parse_payload(record[TIMESTAMP_BYTES:])
