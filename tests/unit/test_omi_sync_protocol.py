"""omi-sync: the ring protocol parser, the firmware gate, time resolution and recording segmentation.

Pure functions over fixture bytes - no BLE, no clock, no hardware (``services/omi-sync``). The wire layouts
under test were read from Omi's MIT source (``storage.c``, ``ring_protocol.dart``,
``PENDANT_RING_CUSTODY.md``); see ``services/omi-sync/THIRD_PARTY_NOTICES.md``.

Source mutations that must turn this file red (re-run by Edit): drop the ``>=`` in ``parse_payload``'s boundary; make
``supports_ring`` accept 3.0.19; stop treating bit 31 as the uptime flag; drop the seq-hole split in ``segment``.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))

from omi_sync import ring_protocol as rp  # noqa: E402
from omi_sync.recordings import resolve_time, segment  # noqa: E402
from omi_sync.spool import SpoolRecord  # noqa: E402


def packed(frames: list[bytes], total: int = rp.PAYLOAD_BYTES) -> bytes:
    body = b"".join(bytes([len(f)]) + f for f in frames)
    assert len(body) <= total
    return body + b"\x00" * (total - len(body))


def record(ts: int, frames: list[bytes]) -> bytes:
    return struct.pack(">I", ts) + packed(frames)


# --- firmware gate --------------------------------------------------------------------------------
@pytest.mark.parametrize("fw,ok", [
    ("3.0.19", False), ("3.0.20", True), ("3.0.21", True), ("3.1.0", True), ("4.0.0", True),
    ("2.9.9", False), ("1.5.2", False), ("garbage", False), ("", False), ("3.0", False),
    (b"3.0.21\x00", True), ("v3.0.21", True), (None, False), ("3.0.21-dirty", True),
])
def test_firmware_gate(fw, ok):
    assert rp.supports_ring(fw) is ok


def test_parse_firmware_tuple():
    assert rp.parse_firmware("3.0.21") == (3, 0, 21)
    assert rp.parse_firmware("nope") is None


# --- INFO / DONE / READ_BEGIN / ACK / status -------------------------------------------------------
def info_bytes(read=10, write=50, cap=100000, dropped=0, pkt=444, tail=b"") -> bytes:
    return bytes([rp.NOTIFY_INFO]) + struct.pack(">QQIQH", read, write, cap, dropped, pkt) + tail


def test_info_legacy_31_bytes_has_no_caps_and_no_ring_id():
    info = rp.parse_info(info_bytes(read=7, write=9, dropped=3))
    assert (info.read_seq, info.write_seq, info.dropped_packets, info.packet_size) == (7, 9, 3, 444)
    assert info.caps == 0 and info.ring_id is None and info.has_ring_id is False
    assert info.unread == 2


def test_info_custody_41_bytes_carries_caps_version_ring_id():
    tail = bytes([rp.CAP_APP_ACK_RECLAIM | rp.CAP_RING_ID, 1]) + struct.pack(">Q", 0xDEADBEEF12345678)
    info = rp.parse_info(info_bytes(tail=tail))
    assert info.caps == 0x09 and info.contract_version == 1
    assert info.ring_id == 0xDEADBEEF12345678 and info.has_ring_id is True


def test_ring_id_ignored_when_the_cap_is_not_advertised():
    tail = bytes([rp.CAP_APP_ACK_RECLAIM, 1]) + struct.pack(">Q", 5)
    assert rp.parse_info(info_bytes(tail=tail)).has_ring_id is False


def test_info_truncated_or_wrong_opcode_raises():
    with pytest.raises(rp.ProtocolError):
        rp.parse_info(info_bytes()[:30])
    with pytest.raises(rp.ProtocolError):
        rp.parse_info(bytes([rp.NOTIFY_DONE]) + info_bytes()[1:])


def test_done_begin_ack_layouts():
    d = rp.parse_done(bytes([rp.NOTIFY_DONE, 0]) + struct.pack(">Q", 77))
    assert (d.status, d.next_seq, d.ok) == (0, 77, True)
    assert rp.parse_done(bytes([rp.NOTIFY_DONE, 9]) + struct.pack(">Q", 1)).ok is False
    b = rp.parse_read_begin(bytes([rp.NOTIFY_READ_BEGIN]) + struct.pack(">QI", 12, 30))
    assert (b.start_seq, b.packet_count) == (12, 30)
    assert rp.parse_ack(bytes([rp.NOTIFY_ACK, rp.ACK_SEQ_OUT_OF_RANGE])).status == 10
    for parse, bad in ((rp.parse_done, b""), (rp.parse_done, bytes([rp.NOTIFY_DONE, 0, 0])),
                       (rp.parse_read_begin, bytes([rp.NOTIFY_READ_BEGIN, 0, 0])), (rp.parse_ack, bytes([rp.NOTIFY_ACK]))):
        with pytest.raises(rp.ProtocolError):
            parse(bad)


def test_status_read_is_the_one_little_endian_structure():
    st = rp.parse_status(struct.pack("<IIII", 4440, 10, 999, 1))
    assert (st.used_bytes, st.unread_packets, st.free_bytes, st.rtc_valid) == (4440, 10, 999, True)
    with pytest.raises(rp.ProtocolError):
        rp.parse_status(b"\x00" * 15)


# --- commands ---------------------------------------------------------------------------------------
def test_command_encodings_are_big_endian():
    assert rp.encode_info() == b"\x10"
    assert rp.encode_stop() == b"\x03"
    assert rp.encode_read(0x0102) == bytes([0x11]) + (0x0102).to_bytes(8, "big")
    assert rp.encode_read(5, 30) == bytes([0x11]) + (5).to_bytes(8, "big") + (30).to_bytes(4, "big")
    assert rp.encode_advance(300) == bytes([0x12]) + (300).to_bytes(8, "big")
    assert rp.encode_advance_id(0xAB, 9) == bytes([0x15]) + (0xAB).to_bytes(8, "big") + (9).to_bytes(8, "big")
    assert rp.encode_time(0x01020304) == b"\x04\x03\x02\x01"        # the time write is little-endian


def test_there_is_no_clear_encoder():
    """'Never CLEAR' is structural: nothing in the module can build the 0x13 command."""
    assert rp.CMD_CLEAR == 0x13
    assert not [n for n in dir(rp) if "clear" in n.lower() and n.startswith("encode")]


# --- records ----------------------------------------------------------------------------------------
def test_reassembler_cuts_444_byte_records_from_unaligned_chunks():
    recs = [record(1_800_000_000 + i, [bytes([i]) * 40]) for i in range(3)]
    blob = b"".join(recs)
    r = rp.RecordReassembler()
    got = []
    for i in range(0, len(blob), 237):                      # MTU-sized, not record-aligned
        r.append(blob[i:i + 237])
        got += r.drain()
    assert got == recs and r.pending == 0
    r.append(recs[0][:100])
    assert r.drain() == [] and r.pending == 100


def test_payload_parses_packed_frames_and_skips_zero_padding():
    f1, f2, f3 = b"\x11" * 80, b"\x22" * 3, b"\x33" * 50
    payload = b"\x00\x00" + packed([f1, f2], 200) + b"\x00" * 3 + bytes([len(f3)]) + f3
    payload += b"\x00" * (rp.PAYLOAD_BYTES - len(payload))
    assert rp.parse_payload(payload) == [f1, f2, f3]


def test_payload_boundary_frame_is_dropped_like_the_firmware_writes_it():
    """At the edge the firmware writes a size byte WITHOUT its frame; `i + 1 + size >= n` must drop it."""
    first = b"\x44" * 100
    pad = rp.PAYLOAD_BYTES - (1 + len(first)) - 1            # leaves exactly one byte: the orphan size byte
    payload = bytes([len(first)]) + first + b"\x00" * pad + bytes([60])
    assert len(payload) == rp.PAYLOAD_BYTES
    assert rp.parse_payload(payload) == [first]
    # a frame that ends EXACTLY at the end of the payload is also dropped (>= not >): the stale-tail rule
    assert rp.parse_payload(bytes([99]) + b"\x55" * 99) == []                       # n=100, frame ends exactly at n
    assert rp.parse_payload(bytes([98]) + b"\x55" * 98 + b"\x00") == [b"\x55" * 98]  # one pad byte of slack: kept


def test_uptime_flagged_timestamp_is_no_wall_clock():
    assert rp.record_timestamp(record(1_800_000_000, [])) == 1_800_000_000
    flagged = struct.pack(">I", 0x80000000 | 4242) + packed([])
    assert rp.record_timestamp(flagged) is None
    ts, frames = rp.split_record(flagged)
    assert ts is None and frames == []


def test_split_record_rejects_wrong_size():
    with pytest.raises(rp.ProtocolError):
        rp.split_record(b"\x00" * 443)


# --- time resolution ---------------------------------------------------------------------------------
NOW = 1_800_000_000.0


def test_resolve_time_trusts_a_plausible_device_clock_and_corrects_drift():
    t, q = resolve_time(int(NOW - 100), clock_offset=0.5, estimate=NOW - 50, now=NOW)
    assert q == "device" and t == pytest.approx(NOW - 100.5)
    t, q = resolve_time(int(NOW - 100 + 240), clock_offset=240.0, estimate=NOW - 50, now=NOW)     # device runs 4 min ahead
    assert q == "corrected" and t == pytest.approx(NOW - 100)


def test_resolve_time_falls_back_to_the_estimate_when_the_clock_is_garbage():
    est = NOW - 33
    assert resolve_time(None, clock_offset=0.0, estimate=est, now=NOW) == (est, "estimated")          # uptime flag
    assert resolve_time(12345, clock_offset=0.0, estimate=est, now=NOW) == (est, "estimated")         # RTC never set
    assert resolve_time(int(NOW), clock_offset=20 * 3600.0, estimate=est, now=NOW) == (est, "estimated")
    assert resolve_time(int(NOW + 5000), clock_offset=0.0, estimate=est, now=NOW) == (est, "estimated")  # future


def test_resolve_time_without_a_readable_device_clock_keeps_the_record_time():
    assert resolve_time(int(NOW - 10), clock_offset=None, estimate=NOW, now=NOW) == (NOW - 10, "device")


# --- segmentation ------------------------------------------------------------------------------------
def srec(seq: int, t: float, frames: int = 5, tq: str = "device", codec: int = 21) -> SpoolRecord:
    return SpoolRecord(seq=seq, t_epoch=t, tq=tq, frames=frames, codec=codec, received_at=NOW, record=b"x" * 444)


def run(seqs_times, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("gap_s", 90.0)
    kw.setdefault("max_s", 600.0)
    return segment([srec(s, t) for s, t in seqs_times], device="d", ring="r1", **kw)


def test_a_long_silence_ends_a_recording():
    segs = run([(0, NOW - 1000), (1, NOW - 999), (2, NOW - 800), (3, NOW - 799)])
    assert [(s.first_seq, s.last_seq) for s in segs] == [(0, 1), (2, 3)]
    assert all(s.closed for s in segs)


def test_a_seq_hole_ends_a_recording_and_is_counted():
    segs = run([(0, NOW - 1000), (1, NOW - 999.9), (5, NOW - 999.8), (6, NOW - 999.7)])
    assert [(s.first_seq, s.last_seq, s.gap_before) for s in segs] == [(0, 1, 0), (5, 6, 3)]


def test_max_length_cuts_deterministically():
    # 5 frames x 20 ms = 0.1 s/record; max_s = 1.0 -> a cut every 10 records (the check runs before each add)
    segs = run([(i, NOW - 500 + i * 0.1) for i in range(25)], max_s=1.0)
    assert [(s.first_seq, s.last_seq) for s in segs] == [(0, 9), (10, 19), (20, 24)]
    assert segs[0].duration_s == pytest.approx(1.0)
    assert run([(i, NOW - 500 + i * 0.1) for i in range(25)], max_s=1.0)[1].key == segs[1].key      # same input, same key


def test_the_tail_is_open_while_the_owner_is_still_recording():
    segs = run([(0, NOW - 200), (1, NOW - 199), (2, NOW - 20), (3, NOW - 19)])
    assert [(s.first_seq, s.closed) for s in segs] == [(0, True), (2, False)]


def test_the_tail_closes_after_the_quiet_gap_or_force_close():
    segs = run([(0, NOW - 200), (1, NOW - 199)])
    assert segs[0].closed is True                               # last record is 199 s old > 90 s
    segs = run([(0, NOW - 5), (1, NOW - 4)], force_close=True)
    assert segs[0].closed is True


def test_a_tail_we_have_not_caught_up_with_stays_open_however_old_it_is():
    """After a dropped READ 'no newer record' means 'not received yet' - closing it would split one recording in two."""
    recs = [(0, NOW - 900), (1, NOW - 899)]
    assert run(recs)[0].closed is True
    assert run(recs, hold_tail=True)[0].closed is False
    # ... but a tail that already hit the length cap is complete either way
    full = [(i, NOW - 900 + i * 0.1) for i in range(10)]
    assert run(full, max_s=1.0, hold_tail=True)[0].closed is True


def test_worst_time_quality_wins_and_key_names_ring_and_range():
    recs = [srec(0, NOW - 300, tq="device"), srec(1, NOW - 299.9, tq="estimated")]
    seg = segment(recs, device="d", ring="r1", now=NOW, gap_s=90, max_s=600)[0]
    assert seg.time_quality == "estimated"
    assert seg.key == "r1.0-1"
