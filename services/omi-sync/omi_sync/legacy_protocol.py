"""The pre-ring "multi-file" SD protocol of firmware 3.0.18 (the firmware the owner's pendant actually runs), as pure functions.

Read from Omi's MIT source at the commit that shipped 3.0.18 (``5cee930fbb``, 2026-05-04; ``CONFIG_BT_DIS_FW_REV_STR="3.0.18"``
in ``omi/firmware/omi/omi.conf`` at that commit): ``omi/firmware/omi/src/lib/core/storage.c`` and the app side
``app/lib/services/devices/connectors/omi_connection.dart`` (``performListStorageFiles`` ...) +
``app/lib/services/wals/storage_sync.dart``. 3.0.19 changes ``storage.c``; it is NOT covered here.

Same GATT service as the ring protocol (``30295780`` / ``..81`` write+notify / ``..82`` read), a DIFFERENT command set that
re-uses the same opcode bytes with different meanings - which is why the firmware gate matters:

  0x10  LIST_FILES   -> ONE notification ``[count:u8]([timestamp:u32 BE][size:u32 BE]) * count`` (timestamp = the file's hex name)
  0x11  READ_FILE    ``[0x11][index:u8][offset:u32 BE]`` -> notifications ``[timestamp:u32 BE][up to 440 file bytes]``
                     (one 440-byte block per notification when the MTU allows), then a single byte ``100``
  0x12  DELETE_FILE  ``[0x12][index:u8]``  <-- on the ring protocol the SAME byte is ADVANCE.  NEVER SENT HERE: no encoder.
  0x03  STOP_SYNC
  single-byte notifications: 0 ack, 3 bad size / cancel, 4 empty file, 6 invalid command, 7 file not found,
  8 index out of range, 100 end of transfer.
  status read (``..82``, 16 bytes, four little-endian u32): ``[used_bytes][file_count][free_bytes = 0][flags = 0]``.

THE CUSTODY PROBLEM, stated plainly (``storage.c`` ``storage_write``): when a file has been fully notified, the firmware
DELETES it itself ("Auto-delete after successful multi-file sync", skipped only for the file currently being recorded) and then
sends ``100``. There is no app acknowledgement and no way to defer it. So on 3.0.18 the pendant frees audio before Zoe has
confirmed anything, and the app cannot prevent it; the only custody is the Pi's durable spool (``spool.py``), exactly as for
stock ring firmware but with a coarser grain (a whole file at a time instead of 2 s checkpoints). This module therefore never
sends a delete, and ``engine`` only uses it behind an explicit opt-in (``OMI_SYNC_LEGACY_FILES``).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Union

from .ring_protocol import PAYLOAD_BYTES, RECORD_BYTES, ProtocolError

#: the only firmware whose ``storage.c`` was read for this module
LEGACY_FILE_FIRMWARE = ((3, 0, 18),)

CMD_LIST_FILES = 0x10
CMD_READ_FILE = 0x11
CMD_DELETE_FILE = 0x12      # documented only: there is deliberately no encoder
CMD_STOP = 0x03

STATUS_ACK = 0
STATUS_BAD_SIZE = 3
STATUS_EMPTY = 4
STATUS_INVALID_COMMAND = 6
STATUS_FILE_NOT_FOUND = 7
STATUS_INDEX_OUT_OF_RANGE = 8
STATUS_END = 100

BLOCK_BYTES = PAYLOAD_BYTES           # 440: one stored block of packed [size:1][frame:size]... per notification
NOTIFY_BLOCK_BYTES = 4 + BLOCK_BYTES  # [timestamp:4 BE][block:440]
MAX_LIST_FILES = 128                  # the app's own sanity bound


def is_legacy_file_firmware(version: Optional[tuple[int, int, int]]) -> bool:
    return version in LEGACY_FILE_FIRMWARE


@dataclass(frozen=True)
class LegacyStatus:
    used_bytes: int
    file_count: int


@dataclass(frozen=True)
class FileInfo:
    index: int
    timestamp: int      # the file's name (hex), as the firmware's clock saw it
    size: int

    @property
    def blocks(self) -> int:
        """Whole 440-byte blocks. A trailing partial block is ignored (never expected: writes are block-sized)."""
        return self.size // BLOCK_BYTES


@dataclass(frozen=True)
class Block:
    timestamp: int
    payload: bytes


@dataclass(frozen=True)
class Status:
    code: int


Note = Union[Block, Status]


def parse_status(data: bytes) -> LegacyStatus:
    if len(data) < 8:
        raise ProtocolError("legacy status read shorter than 8 bytes")
    used, count = struct.unpack_from("<II", data, 0)
    return LegacyStatus(used, count)


def encode_list() -> bytes:
    return bytes([CMD_LIST_FILES])


def encode_stop() -> bytes:
    return bytes([CMD_STOP])


def encode_read_file(index: int, offset: int) -> bytes:
    if not 0 <= index <= 255 or offset < 0 or offset > 0xFFFFFFFF:
        raise ValueError("index must fit a byte and offset a u32")
    return struct.pack(">BBI", CMD_READ_FILE, index, offset)


def parse_file_list(data: bytes) -> list[FileInfo]:
    """``[count][ts:4 BE][size:4 BE]...``. Raises ``ProtocolError`` for anything that is not a well-formed list
    (a data block or a status byte must never be mistaken for one)."""
    if not data:
        raise ProtocolError("empty file list notification")
    count = data[0]
    if count == 0:
        if len(data) == 1:
            return []
        raise ProtocolError("file list: zero count with trailing bytes")
    if count > MAX_LIST_FILES or len(data) < 1 + 8 * count:
        raise ProtocolError("file list truncated or implausible")
    out = []
    for i in range(count):
        ts, size = struct.unpack_from(">II", data, 1 + 8 * i)
        out.append(FileInfo(i, ts, size))
    return out


def classify(note: bytes) -> Note:
    """One READ_FILE notification: a single byte is a status, anything longer than 4 bytes is ``[ts:4][block bytes]``."""
    if len(note) == 1:
        return Status(note[0])
    if len(note) <= 4:
        raise ProtocolError("READ_FILE notification too short to carry data")
    return Block(struct.unpack_from(">I", note, 0)[0], bytes(note[4:]))


def block_to_record(file_timestamp: int, payload: bytes) -> bytes:
    """A 440-byte block in the same 444-byte shape as a ring record (``[ts:4 BE][440]``), so the spool, segmentation and
    decoder are shared. Raises ``ProtocolError`` unless the block is exactly 440 bytes (an MTU-fragmented stream would
    misalign every later block - the engine STOPs rather than guess)."""
    if len(payload) != BLOCK_BYTES:
        raise ProtocolError(f"block is {len(payload)} bytes, expected {BLOCK_BYTES}")
    rec = struct.pack(">I", file_timestamp & 0x7FFFFFFF) + payload     # bit 31 is the ring's 'uptime' flag: never carried over
    assert len(rec) == RECORD_BYTES
    return rec
