"""omi-sync: the drain engine and its CUSTODY RULE, against a faithful fake pendant (no hardware, no BLE).

The rule (Omi's own ``PENDANT_RING_CUSTODY.md``): audio is freed from the pendant only after Zoe has CONFIRMED
it stored that recording. These tests pin it from every direction:

  no ADVANCE without a confirmed upload        Zoe down / rejecting / hanging up mid-batch
  ADVANCE never past the first unconfirmed     partial confirmation advances exactly to the boundary
  resume after a drop neither loses nor dupes  compared against an uninterrupted control run, key for key
  stock firmware that frees records itself     the spool is the custody; nothing delivered is lost
  the firmware gate                            < 3.0.20 -> the ring is never touched
  never CLEAR                                  asserted on the wire, every scenario

Break-the-fix controls: the ``test_control_*`` tests at the end swap ONE mechanism for its naive version by monkeypatch
(naive ``advance_target``; bypassed firmware gate; a held tail closed) and assert the guarded property is then violated.
Source mutations worth re-running by Edit: call ``mark_confirmed`` before the upload outcome is checked; start the resume
read at ``read_seq`` without the spool's ``high_seq``; drop the ``flush()`` in ``_read``'s ``finally``.
"""
from __future__ import annotations

import asyncio
import struct
import sys
from pathlib import Path
from typing import AsyncIterator, Optional

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))

from omi_sync import ring_protocol as rp  # noqa: E402
from omi_sync.engine import Drain, LinkDropped, SyncConfig, advance_target, ring_label  # noqa: E402
from omi_sync.spool import Spool, SpoolRecord  # noqa: E402
from omi_sync.uploader import Outcome, UploadMeta, UploadResult, classify, device_hash, sha256_hex  # noqa: E402

NOW = 1_800_000_000.0
DEV = "dev0123456789abc"


# --- fixture bytes ---------------------------------------------------------------------------------
def make_record(ts: int, tag: int) -> bytes:
    frames = [bytes([tag & 0xFF]) + bytes(79)] * 5                 # five 80-byte "Opus" frames, first byte = tag
    body = b"".join(bytes([len(f)]) + f for f in frames)
    return struct.pack(">I", ts) + body + b"\x00" * (rp.PAYLOAD_BYTES - len(body))


def build_ring(groups: list[tuple[int, int]]) -> list[bytes]:
    """groups = [(start_epoch, n_records)] -> records with ~9 records/second timestamps."""
    out = []
    for start, n in groups:
        for i in range(n):
            out.append(make_record(start + i // 9, len(out)))
    return out


#: three recordings separated by long silences, all comfortably older than the quiet gap; 40 records each
RING = [(int(NOW) - 3000, 40), (int(NOW) - 2000, 40), (int(NOW) - 1000, 40)]


class FakeDecoder:
    def decode(self, frame: bytes) -> bytes:
        return struct.pack("<h", frame[0]) + b"\x00" * 638          # one 320-sample (20 ms) frame


def info_note(read, write, *, caps=None, ring_id=None, dropped=0) -> bytes:
    note = bytes([rp.NOTIFY_INFO]) + struct.pack(">QQIQH", read, write, 100000, dropped, 444)
    if caps is not None:
        note += bytes([caps, 1]) + struct.pack(">Q", ring_id or 0)
    return note


class FakePendant:
    """A pendant on the far side of a link. Records every command written to it in ``sent``."""

    def __init__(self, records: list[bytes], *, fw="3.0.21", codec=21, dev_time: Optional[int] = int(NOW), caps: Optional[int] = None,
                 ring_id: int = 0xA1B2C3, auto_free=False, drop_after_notes: Optional[int] = None,
                 advance_ack: Optional[int] = None, done_next_delta=0) -> None:
        self.records, self.fw, self.codec_id, self.dev_time = list(records), fw, codec, dev_time
        self.caps, self.ring_id = caps, ring_id
        self.read_seq = 0
        self.auto_free = auto_free              # stock firmware: frees records as the link layer confirms them
        self.drop_after_notes = drop_after_notes
        self.advance_ack = advance_ack          # force an ack status for ADVANCE
        self.done_next_delta = done_next_delta
        self.sent: list[bytes] = []
        self.time_written: list[int] = []
        self.dropped_once = False

    @property
    def write_seq(self) -> int:
        return len(self.records)

    def append(self, extra: list[bytes]) -> None:
        self.records += extra

    def _info(self) -> bytes:
        return info_note(self.read_seq, self.write_seq, caps=self.caps, ring_id=self.ring_id if self.caps is not None else None)

    async def firmware(self) -> str:
        return self.fw

    async def codec(self) -> int:
        return self.codec_id

    async def device_time(self) -> Optional[int]:
        return self.dev_time

    async def info(self) -> bytes:
        self.sent.append(rp.encode_info())
        return self._info()

    async def read(self, start_seq: int) -> AsyncIterator[bytes]:
        self.sent.append(rp.encode_read(start_seq))
        assert self.read_seq <= start_seq <= self.write_seq, "reading below the read pointer / past the write pointer"
        count = self.write_seq - start_seq
        yield bytes([rp.NOTIFY_READ_BEGIN]) + struct.pack(">QI", start_seq, count)
        blob = b"".join(self.records[start_seq:])
        notes = 0
        delivered = 0
        for i in range(0, len(blob), 237):
            if self.drop_after_notes is not None and not self.dropped_once and notes >= self.drop_after_notes:
                self.dropped_once = True
                raise LinkDropped("test: link lost")
            piece = blob[i:i + 237]
            yield bytes([rp.NOTIFY_DATA]) + piece
            notes += 1
            delivered += len(piece)
            if self.auto_free:
                self.read_seq = max(self.read_seq, start_seq + delivered // rp.RECORD_BYTES)
        yield bytes([rp.NOTIFY_DONE, 0]) + struct.pack(">Q", self.write_seq + self.done_next_delta)

    async def advance(self, command: bytes) -> bytes:
        self.sent.append(command)
        if self.advance_ack is not None:
            return bytes([rp.NOTIFY_ACK, self.advance_ack])
        if command[0] == rp.CMD_ADVANCE_ID:
            rid, seq = struct.unpack(">QQ", command[1:17])
            if rid != self.ring_id:
                return bytes([rp.NOTIFY_ACK, rp.ACK_RING_ID_MISMATCH])
        else:
            seq = struct.unpack(">Q", command[1:9])[0]
        if seq < self.read_seq or seq > self.write_seq:
            return bytes([rp.NOTIFY_ACK, rp.ACK_SEQ_OUT_OF_RANGE])
        self.read_seq = seq
        return bytes([rp.NOTIFY_ACK, rp.ACK_OK])

    async def stop(self) -> None:
        self.sent.append(rp.encode_stop())

    async def set_time(self, epoch_s: int) -> None:
        self.time_written.append(epoch_s)


class FakeZoe:
    """The server's side of the contract: idempotent by key, content-addressed confirmation."""

    def __init__(self, mode="ok") -> None:
        self.mode = mode                        # ok | down | reject | unregistered
        self.stored: dict[str, tuple[UploadMeta, bytes]] = {}
        self.calls: list[str] = []
        self.notices: list[int] = []
        self.notice_devices: list[str] = []
        self.fail_keys: set[str] = set()

    async def upload(self, meta: UploadMeta, wav: bytes) -> UploadResult:
        self.calls.append(meta.key)
        if meta.sha256 != sha256_hex(wav):
            return UploadResult(Outcome.REJECTED, "sha mismatch")
        if self.mode == "down":
            return UploadResult(Outcome.UNAVAILABLE, "down")
        if self.mode == "unregistered":
            return UploadResult(Outcome.NOT_REGISTERED, "device_not_registered")
        if self.mode == "reject" or meta.key in self.fail_keys:
            return UploadResult(Outcome.REJECTED, "422")
        self.stored.setdefault(meta.key, (meta, wav))
        return UploadResult(Outcome.CONFIRMED, "stored")

    async def notify_synced(self, count: int, device_id: str = "") -> None:
        self.notices.append(count)
        self.notice_devices.append(device_id)


@pytest.fixture
def spool(tmp_path):
    s = Spool(tmp_path / "spool.sqlite3")
    yield s
    s.close()


def drain(pendant, spool, zoe, tmp_path, **cfg):
    config = SyncConfig(device_id=DEV, outbox=tmp_path / "outbox", **cfg)
    d = Drain(pendant, spool, zoe, config, decoder_factory=lambda codec: FakeDecoder(), clock=lambda: NOW)
    return asyncio.run(d.run())


def never_cleared(pendant):
    assert not [c for c in pendant.sent if c[:1] == bytes([rp.CMD_CLEAR])], "a CLEAR reached the pendant"


def advances(pendant):
    return [c for c in pendant.sent if c[:1] in (bytes([rp.CMD_ADVANCE]), bytes([rp.CMD_ADVANCE_ID]))]


def seq_of(cmd: bytes) -> int:
    return struct.unpack(">Q", cmd[-8:])[0]


# --- the happy path -----------------------------------------------------------------------------------
def test_every_staged_recording_logs_its_decoded_level(spool, tmp_path):
    """A pendant that heard nothing (peak ~0) must be tellable from one that did not decode: the level rides the sync log line."""
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path)
    levels = [n for n in res.notes if " level peak=" in n]
    assert len(levels) == 3 and all(n.split(":")[0].startswith("r") for n in levels)
    assert all("s level peak=" in n and "rms=" in n for n in levels)


def test_level_of_distinguishes_silence_from_sound_and_tolerates_odd_bytes():
    import math
    from omi_sync.recordings import level_of
    tone = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * i / 16000))) for i in range(16000))
    peak, rms = level_of(tone)
    assert peak == 6000 and 4000 < rms < 4400
    assert level_of(b"\x00\x00" * 16000) == (0, 0.0)          # negative control: digital silence reads as zero, not as the tone
    assert level_of(b"") == (0, 0.0)
    assert level_of(tone + b"\x01")[0] == 6000                  # a dangling byte is ignored, not an exception


def test_full_drain_uploads_every_recording_then_frees_the_ring(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.clean and res.refused is None
    assert res.new_records == 120 and res.uploaded == 3
    assert sorted(zoe.stored) == [f"r{pendant.ring_id:x}.0-39", f"r{pendant.ring_id:x}.40-79", f"r{pendant.ring_id:x}.80-119"]
    assert pendant.read_seq == 120 == res.advanced_to
    assert spool.stats()["spooled_records"] == 0                      # local copies dropped once confirmed
    assert not list((tmp_path / "outbox").glob("*.wav"))              # staged WAVs deleted after confirmation
    assert zoe.notices == [3]
    never_cleared(pendant)
    # the wire order is INFO, READ, INFO (fresh pointer), ADVANCE_ID - and the ADVANCE is the LAST mutating command
    assert pendant.sent[-1][0] == rp.CMD_ADVANCE_ID and seq_of(pendant.sent[-1]) == 120


def test_wav_content_is_the_decoded_audio_in_order(spool, tmp_path):
    import io
    import wave
    pendant, zoe = FakePendant(build_ring(RING[:1])), FakeZoe()
    drain(pendant, spool, zoe, tmp_path)
    (meta, wav), = zoe.stored.values()
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() == 40 * 5 * 320                          # 40 records x 5 frames x 320 samples
        first = struct.unpack("<h", w.readframes(1)[:2])[0]
    assert first == 0                                                  # record 0's tag
    assert meta.duration_s == pytest.approx(40 * 5 * 0.02) and meta.time_quality == "device"
    assert meta.started_at == pytest.approx(RING[0][0])


# --- the firmware gate --------------------------------------------------------------------------------
@pytest.mark.parametrize("fw", ["3.0.19", "2.9.0", "unknown", ""])
def test_old_or_unreadable_firmware_never_reaches_the_ring(spool, tmp_path, fw):
    pendant, zoe = FakePendant(build_ring(RING), fw=fw), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.refused and "ring protocol" in res.refused
    assert pendant.sent == [] and zoe.calls == [] and spool.stats()["spooled_records"] == 0


def test_unsupported_codec_is_refused_before_any_ring_command(spool, tmp_path):
    pendant = FakePendant(build_ring(RING), codec=1)
    res = drain(pendant, spool, FakeZoe(), tmp_path)
    assert res.refused and "codec" in res.refused and pendant.sent == []


# --- the custody rule ----------------------------------------------------------------------------------
def test_zoe_down_means_nothing_is_freed_and_the_next_session_finishes_the_job(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe(mode="down")
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.unavailable and res.uploaded == 0 and res.pending == 3
    assert advances(pendant) == [] and pendant.read_seq == 0          # NO ADVANCE on the wire at all
    assert spool.stats()["spooled_records"] == 120                    # but every record is safe here
    assert len(zoe.calls) == 1                                        # one failure ends the batch: no hammering
    zoe.mode = "ok"
    res2 = drain(pendant, spool, zoe, tmp_path)
    assert res2.clean and res2.uploaded == 3 and pendant.read_seq == 120
    never_cleared(pendant)


def test_a_pendant_nobody_owns_keeps_custody_and_asks_to_be_retried_slowly(spool, tmp_path):
    """Zoe's 409 device_not_registered: nothing is confirmed so nothing is freed, the spool holds every record, one answer ends the
    batch (no hammering) and the result is NOT clean (the runner waits OMI_SYNC_UNREGISTERED_RETRY_S). Once an admin assigns the
    pendant the very next session uploads and frees everything."""
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe(mode="unregistered")
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.not_registered and not res.clean and res.uploaded == 0 and res.pending == 3
    assert advances(pendant) == [] and pendant.read_seq == 0
    assert spool.stats()["spooled_records"] == 120
    assert len(zoe.calls) == 1 and zoe.notices == []
    zoe.mode = "ok"
    res2 = drain(pendant, spool, zoe, tmp_path)
    assert res2.clean and res2.uploaded == 3 and pendant.read_seq == 120
    never_cleared(pendant)


def test_the_legacy_file_path_also_keeps_the_spool_for_a_pendant_nobody_owns(spool, tmp_path):
    """3.0.18 firmware deletes each file itself on delivery - the spool is the ONLY custody, so an unowned pendant must not cost audio."""
    pendant, zoe = FakeLegacyPendant(legacy_files((T1, 40))), FakeZoe(mode="unregistered")
    res = drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    assert res.not_registered and res.uploaded == 0 and res.pending >= 1
    assert spool.stats()["spooled_records"] > 0


def test_the_sync_notice_names_the_device_so_zoe_can_tell_only_its_owner(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe()
    drain(pendant, spool, zoe, tmp_path)
    assert zoe.notice_devices == [DEV]


def test_advance_stops_exactly_at_the_first_unconfirmed_recording(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe()
    zoe.fail_keys = {f"r{pendant.ring_id:x}.40-79"}                    # the middle one is rejected
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.rejected == 1 and res.uploaded == 2                     # the third still went (a reject doesn't block later ones)
    assert [seq_of(c) for c in advances(pendant)] == [40]              # freed 0..39 only; 40..119 stay on the pendant
    assert pendant.read_seq == 40
    assert spool.stats()["spooled_records"] == 40                      # the rejected recording is kept locally too


def test_a_recording_zoe_never_confirmed_is_never_freed_even_after_others_succeed(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe()
    zoe.fail_keys = {f"r{pendant.ring_id:x}.0-39"}                     # the FIRST is rejected
    drain(pendant, spool, zoe, tmp_path)
    assert advances(pendant) == [] and pendant.read_seq == 0


def test_the_open_tail_is_not_uploaded_and_not_freed(spool, tmp_path):
    ring = build_ring([(int(NOW) - 2000, 40), (int(NOW) - 30, 40)])    # the second ended 25 s ago: still being recorded
    pendant, zoe = FakePendant(ring, caps=0x09), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.uploaded == 1 and res.pending == 1
    assert pendant.read_seq == 40 and [seq_of(c) for c in advances(pendant)] == [40]
    assert spool.stats()["spooled_records"] == 40


def test_the_tail_is_picked_up_once_the_owner_stops_recording(spool, tmp_path):
    ring = build_ring([(int(NOW) - 2000, 40), (int(NOW) - 30, 40)])
    pendant, zoe = FakePendant(ring, caps=0x09), FakeZoe()
    drain(pendant, spool, zoe, tmp_path)
    later = Drain(pendant, spool, zoe, SyncConfig(device_id=DEV, outbox=tmp_path / "outbox"),
                  decoder_factory=lambda c: FakeDecoder(), clock=lambda: NOW + 300)
    res = asyncio.run(later.run())
    assert res.uploaded == 1 and pendant.read_seq == 80
    assert res.new_records == 0                                        # the tail was NOT re-downloaded: the spool already held it
    assert len(zoe.stored) == 2


# --- resume after a drop ------------------------------------------------------------------------------
def test_a_drop_mid_drain_then_resume_equals_an_uninterrupted_run(spool, tmp_path):
    ring = build_ring(RING)
    control_pendant, control_zoe = FakePendant(ring, caps=0x09), FakeZoe()
    ctl_spool = Spool(tmp_path / "control.sqlite3")
    drain(control_pendant, ctl_spool, control_zoe, tmp_path / "ctl")
    ctl_spool.close()

    pendant, zoe = FakePendant(ring, caps=0x09, drop_after_notes=100), FakeZoe()   # ~23 KB in: inside recording 2
    r1 = drain(pendant, spool, zoe, tmp_path)
    assert r1.dropped and not r1.read_complete
    assert advances(pendant) == []                                      # a dropped session frees NOTHING it has not confirmed
    held = spool.stats()["spooled_records"] + 40 * r1.uploaded
    assert 0 < held < 120

    r2 = drain(pendant, spool, zoe, tmp_path)
    assert r2.clean
    assert sorted(zoe.stored) == sorted(control_zoe.stored)             # same recordings, same keys: nothing lost, nothing split
    for key, (meta, wav) in zoe.stored.items():
        assert wav == control_zoe.stored[key][1]                       # byte-identical audio
    assert len(zoe.calls) == len(set(zoe.calls))                        # and no recording uploaded twice
    assert pendant.read_seq == 120


def test_stock_firmware_that_frees_records_itself_still_loses_nothing(spool, tmp_path):
    """Legacy fw (no caps): the pendant advances its own pointer as data is delivered. The spool is the custody."""
    ring = build_ring(RING)
    pendant, zoe = FakePendant(ring, caps=None, auto_free=True, drop_after_notes=100), FakeZoe()
    r1 = drain(pendant, spool, zoe, tmp_path)
    assert r1.dropped and pendant.read_seq > 0                          # the pendant already freed what it delivered
    assert any("legacy firmware" in n for n in r1.notes)
    r2 = drain(pendant, spool, zoe, tmp_path)
    assert r2.read_complete
    seqs = sorted(s for key in zoe.stored for s in range(int(key.split(".")[1].split("-")[0]), int(key.split("-")[1]) + 1))
    assert seqs == list(range(120))                                     # every record reached Zoe exactly once
    never_cleared(pendant)


def test_a_lost_advance_ack_is_harmless_the_next_session_does_not_reupload(spool, tmp_path):
    ring = build_ring(RING)
    pendant, zoe = FakePendant(ring, caps=0x09, advance_ack=rp.ACK_STORAGE_NOT_READY), FakeZoe()
    r1 = drain(pendant, spool, zoe, tmp_path)
    assert r1.uploaded == 3 and pendant.read_seq == 0 and r1.advanced_to is None
    assert any("refused" in n for n in r1.notes)
    pendant.advance_ack = None
    r2 = drain(pendant, spool, zoe, tmp_path)
    assert r2.uploaded == 0 and len(zoe.calls) == 3 and r2.new_records == 0
    assert pendant.read_seq == 120                                      # the freed range was re-sent from confirmed state


def test_confirmed_records_are_not_re_spooled_if_the_pendant_sends_them_again(spool):
    recs = [SpoolRecord(i, NOW - 1000 + i, "device", 5, 21, NOW, b"x" * 444) for i in range(10)]
    assert spool.add_records(DEV, "r1", recs) == 10
    assert spool.add_records(DEV, "r1", recs) == 0                      # idempotent by (ring, seq)
    spool.mark_confirmed(DEV, "r1", 0, 9, "r1.0-9")
    assert spool.add_records(DEV, "r1", recs) == 0                      # confirmed range is a tombstone
    assert spool.stats()["spooled_records"] == 0 and spool.high_seq(DEV, "r1") == 9


# --- ack handling ----------------------------------------------------------------------------------------
def test_seq_out_of_range_is_benign_when_the_pendant_already_freed_it(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=None, auto_free=True), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.uploaded == 3 and pendant.read_seq == 120
    assert res.advanced_to in (None, 120)
    assert not any("out of range" in n for n in res.notes)


def test_ring_id_mismatch_frees_nothing_and_says_so(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe()
    pendant.advance_ack = rp.ACK_RING_ID_MISMATCH
    res = drain(pendant, spool, zoe, tmp_path)
    assert pendant.read_seq == 0 and res.advanced_to is None
    assert any("incarnation" in n for n in res.notes)


def test_custody_firmware_advances_with_the_ring_id_command(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09, ring_id=0xFEED), FakeZoe()
    drain(pendant, spool, zoe, tmp_path)
    (adv,) = advances(pendant)
    assert adv[0] == rp.CMD_ADVANCE_ID and struct.unpack(">QQ", adv[1:]) == (0xFEED, 120)


def test_stock_firmware_advances_with_the_plain_command(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=None), FakeZoe()
    drain(pendant, spool, zoe, tmp_path)
    (adv,) = advances(pendant)
    assert adv[0] == rp.CMD_ADVANCE and len(adv) == 9


# --- incomplete reads ---------------------------------------------------------------------------------------
def test_a_done_that_disagrees_with_what_arrived_is_not_a_complete_read(spool, tmp_path):
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09, done_next_delta=-1), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.read_complete is False
    assert rp.encode_stop() in pendant.sent                             # the transfer is told to stop
    assert pendant.read_seq == 80                                       # and only the two confirmed recordings were freed
    # what DID arrive is confirmed normally - except the tail, which stays open: we cannot tell "caught up" from "cut short"
    assert res.uploaded == 2 and res.pending == 1


# --- ring incarnations ------------------------------------------------------------------------------------------
def test_advance_target_never_frees_what_was_never_received():
    assert advance_target(0, [(0, 39), (80, 119)], None) == 40          # hole at 40..79 is not ours to free
    assert advance_target(0, [(0, 119)], 80) == 80                      # nor anything at/after the first unconfirmed
    assert advance_target(5, [(0, 39)], None) == 40
    assert advance_target(200, [(0, 39)], None) == 200                  # pendant already past us: no-op target
    assert advance_target(0, [], None) == 0


def test_legacy_ring_restart_is_a_new_incarnation_not_a_mix(spool, tmp_path):
    ring = build_ring(RING)
    pendant, zoe = FakePendant(ring, caps=None), FakeZoe()
    drain(pendant, spool, zoe, tmp_path)
    assert ring_label(spool, DEV, rp.parse_info(info_note(0, 120))) == "legacy0"
    # the pendant's ring restarts (CLEAR / metadata loss): write pointer is now far below what we hold
    pendant2 = FakePendant(build_ring([(int(NOW) - 900, 30)]), caps=None)
    res = drain(pendant2, spool, zoe, tmp_path)
    assert res.uploaded == 1
    assert "legacy1.0-29" in zoe.stored and "legacy0.0-39" in zoe.stored    # distinct keys: no collision, nothing overwritten


# --- clock handling ------------------------------------------------------------------------------------------------
def test_device_clock_ahead_is_corrected_and_the_pendant_clock_is_reset(spool, tmp_path):
    ring = build_ring([(int(NOW) + 240 - 2000, 40)])                    # the pendant runs 4 minutes ahead
    pendant, zoe = FakePendant(ring, caps=0x09, dev_time=int(NOW) + 240), FakeZoe()
    drain(pendant, spool, zoe, tmp_path, set_device_time=True)
    (meta, _), = zoe.stored.values()
    assert meta.time_quality == "corrected" and meta.started_at == pytest.approx(NOW - 2000)
    assert pendant.time_written == [int(NOW)]


def test_the_pendant_clock_is_never_written_unless_the_flag_is_on(spool, tmp_path):
    """A WRITE to the pendant is flag-gated and OFF by default - even when the clock is months stale."""
    pendant = FakePendant(build_ring([(int(NOW) + 240 - 2000, 40)]), caps=0x09, dev_time=int(NOW) - 109 * 86400)
    drain(pendant, spool, FakeZoe(), tmp_path)
    assert pendant.time_written == []


def test_an_accurate_clock_is_left_alone(spool, tmp_path):
    pendant = FakePendant(build_ring(RING[:1]), caps=0x09, dev_time=int(NOW) + 1)
    drain(pendant, spool, FakeZoe(), tmp_path, set_device_time=True)
    assert pendant.time_written == []


def test_the_clock_is_set_before_the_storage_is_read(spool, tmp_path):
    """The write happens once, at the START of the session; records stored under the old offset are still corrected by it."""
    order = []
    pendant = FakePendant(build_ring(RING[:1]), caps=0x09, dev_time=int(NOW) + 300)
    real_set, real_read = pendant.set_time, pendant.read

    async def set_time(epoch):
        order.append("set_time")
        await real_set(epoch)

    async def read(start):
        order.append("read")
        async for n in real_read(start):
            yield n

    pendant.set_time, pendant.read = set_time, read
    drain(pendant, spool, FakeZoe(), tmp_path, set_device_time=True)
    assert order[:2] == ["set_time", "read"]


def test_unset_pendant_clock_falls_back_to_estimated_times(spool, tmp_path):
    ring = []
    for i in range(40):
        body = make_record(0, i)
        ring.append(struct.pack(">I", 0x80000000 | (1000 + i // 9)) + body[4:])
    pendant, zoe = FakePendant(ring, caps=0x09, dev_time=None), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.uploaded == 0                                            # estimated times put it at "just now": the tail is open
    assert res.pending == 1
    (rec,) = spool.records(DEV, f"r{pendant.ring_id:x}")[-1:]
    assert rec.tq == "estimated" and rec.t_epoch <= NOW


# --- uploader semantics ------------------------------------------------------------------------------------------------
def test_confirmation_requires_the_server_to_echo_the_exact_bytes():
    ok = b'{"ok": true, "key": "k", "sha256": "abc"}'
    assert classify(200, ok, "abc").outcome is Outcome.CONFIRMED
    assert classify(200, ok, "different").outcome is Outcome.UNAVAILABLE           # 200 is not enough
    assert classify(200, b'{"ok": true}', "abc").outcome is Outcome.UNAVAILABLE
    assert classify(200, b"<html>", "abc").outcome is Outcome.UNAVAILABLE
    assert classify(200, b'{"ok": false}', "abc").outcome is Outcome.UNAVAILABLE


def test_deleted_by_owner_counts_as_dealt_with_and_errors_do_not_free_audio():
    assert classify(200, b'{"ok": true, "deleted": true}', "abc").outcome is Outcome.CONFIRMED
    for status in (401, 403, 404, 500, 502, 503):
        assert classify(status, b"", "abc").outcome is Outcome.UNAVAILABLE         # flag off (404), bad token, owner unset...
    for status in (400, 409, 413, 415, 422):
        assert classify(status, b"", "abc").outcome is Outcome.REJECTED


def test_the_pendant_address_never_appears_in_its_uploaded_id():
    addr = "AA:BB:CC:DD:EE:FF"
    h = device_hash(addr)
    assert len(h) == 16 and "aabbcc" not in h.lower() and h == device_hash(addr.lower())


# --- firmware 3.0.18: the legacy multi-file protocol (what the owner's pendant actually runs) ---------------------------------
from omi_sync import legacy_protocol as lp  # noqa: E402


def make_block(tag: int) -> bytes:
    return make_record(0, tag)[4:]                       # a 440-byte packed payload


class FakeLegacyPendant:
    """Firmware 3.0.18 ``storage.c``: LIST_FILES, READ_FILE [idx][offset], and the AUTO-DELETE of a fully sent file (except the
    one being recorded). Records every command written to it."""

    def __init__(self, files: dict[int, list[bytes]], *, active: Optional[int] = None, fw="3.0.18", codec=0x0f,
                 dev_time: Optional[int] = int(NOW), drop_after_blocks: Optional[int] = None, wrong_size: bool = False) -> None:
        self.files, self.active, self.fw, self.codec_id, self.dev_time = {k: list(v) for k, v in files.items()}, active, fw, codec, dev_time
        self.drop_after_blocks, self.wrong_size = drop_after_blocks, wrong_size
        self.sent: list[bytes] = []
        self.deleted: list[int] = []
        self.dropped_once = False

    def _order(self):
        return sorted(self.files)

    async def firmware(self):
        return self.fw

    async def codec(self):
        return self.codec_id

    async def device_time(self):
        return self.dev_time

    async def legacy_list(self) -> bytes:
        self.sent.append(lp.encode_list())
        out = bytes([len(self.files)])
        for ts in self._order():
            out += struct.pack(">II", ts, len(self.files[ts]) * lp.BLOCK_BYTES)
        return out

    async def legacy_read(self, index: int, offset: int) -> AsyncIterator[bytes]:
        self.sent.append(lp.encode_read_file(index, offset))
        ts = self._order()[index]
        blocks = self.files[ts][offset // lp.BLOCK_BYTES:]
        yield bytes([lp.STATUS_ACK])
        sent = 0
        for b in blocks:
            if self.drop_after_blocks is not None and not self.dropped_once and sent >= self.drop_after_blocks:
                self.dropped_once = True
                raise LinkDropped("test: link lost")
            body = b[:-10] if self.wrong_size else b
            yield struct.pack(">I", ts) + body
            sent += 1
        if ts != self.active:                            # the firmware's own free-on-delivery: not gated by anything we do
            del self.files[ts]
            self.deleted.append(ts)
        yield bytes([lp.STATUS_END])

    async def stop(self):
        self.sent.append(lp.encode_stop())

    async def set_time(self, epoch_s):
        self.sent.append(b"TIME")

    # the ring methods must never be touched on this firmware
    async def info(self):
        raise AssertionError("ring INFO sent to a legacy-file firmware (0x10 there is LIST_FILES)")

    async def advance(self, command):
        raise AssertionError("ADVANCE sent to a legacy-file firmware (0x12 there is DELETE_FILE!)")

    def read(self, start):
        raise AssertionError("ring READ sent to a legacy-file firmware")


T1, T2, T3 = int(NOW) - 3000, int(NOW) - 2000, int(NOW) - 1000


def legacy_files(*spec):
    return {ts: [make_block(i) for i in range(n)] for ts, n in spec}


def never_deleted(pendant):
    """No DELETE_FILE (0x12) and no CLEAR ever reaches the pendant from us; any file gone from it went by the firmware's own hand."""
    bad = [c for c in pendant.sent if c[:1] in (bytes([0x12]), bytes([0x13]))]
    assert not bad, f"destructive command sent: {bad}"


def test_firmware_3_0_18_is_refused_by_default_and_nothing_is_sent(spool, tmp_path):
    pendant = FakeLegacyPendant(legacy_files((T1, 40)))
    res = drain(pendant, spool, FakeZoe(), tmp_path)
    assert res.refused and "legacy file protocol" in res.refused and "OMI_SYNC_LEGACY_FILES" in res.refused
    assert pendant.sent == [] and spool.stats()["spooled_records"] == 0


def test_an_unknown_codec_id_blocks_the_drain_until_the_operator_assumes_one(spool, tmp_path):
    pendant = FakeLegacyPendant(legacy_files((T1, 40)), codec=0x0f)
    res = drain(pendant, spool, FakeZoe(), tmp_path, legacy_files=True)
    assert res.refused and "codec id 15" in res.refused and pendant.sent == []
    res2 = drain(pendant, spool, FakeZoe(), tmp_path, legacy_files=True, assume_codec=21)
    assert res2.refused is None and res2.path == "legacy"


def test_legacy_opt_in_drains_every_file_and_never_sends_a_delete(spool, tmp_path):
    pendant, zoe = FakeLegacyPendant(legacy_files((T1, 40), (T2, 40), (T3, 40))), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    assert res.path == "legacy" and res.clean and res.new_records == 120 and res.uploaded == 3
    assert sorted(zoe.stored) == [f"f{T1:08x}.0-39", f"f{T2:08x}.0-39", f"f{T3:08x}.0-39"]
    never_deleted(pendant)
    assert pendant.deleted == [T1, T2, T3]                    # the FIRMWARE freed them on delivery - see the custody test below
    assert any("firmware 3.0.18" in n for n in res.notes)
    assert spool.stats()["spooled_records"] == 0 and not list((tmp_path / "outbox").glob("*.wav"))
    (meta, _), = [zoe.stored[f"f{T1:08x}.0-39"]]
    assert meta.time_quality == "device" and meta.started_at == pytest.approx(T1)


def test_legacy_custody_is_the_spool_when_zoe_is_down_the_pendant_has_already_freed_the_audio(spool, tmp_path):
    pendant, zoe = FakeLegacyPendant(legacy_files((T1, 40), (T2, 40))), FakeZoe(mode="down")
    res = drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    assert res.unavailable and res.uploaded == 0
    assert pendant.deleted == [T1, T2] and pendant.files == {}                # gone from the pendant, outside our control
    assert spool.stats()["spooled_records"] == 80                              # ... and safe here, still unconfirmed
    zoe.mode = "ok"
    res2 = drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    assert res2.uploaded == 2 and spool.stats()["spooled_records"] == 0       # nothing to re-read: the spool delivers
    never_deleted(pendant)


def test_legacy_drop_mid_file_resumes_from_the_next_block_and_equals_a_clean_run(spool, tmp_path):
    control_zoe = FakeZoe()
    ctl = Spool(tmp_path / "ctl.sqlite3")
    drain(FakeLegacyPendant(legacy_files((T1, 40), (T2, 40))), ctl, control_zoe, tmp_path / "c", legacy_files=True, assume_codec=21)
    ctl.close()
    pendant, zoe = FakeLegacyPendant(legacy_files((T1, 40), (T2, 40)), drop_after_blocks=17), FakeZoe()
    r1 = drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    assert r1.dropped and not r1.read_complete and pendant.deleted == []      # the half-read file is still on the pendant
    reads1 = [c for c in pendant.sent if c[:1] == bytes([lp.CMD_READ_FILE])]
    r2 = drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    assert r2.clean
    reads2 = [c for c in pendant.sent if c[:1] == bytes([lp.CMD_READ_FILE])][len(reads1):]
    assert struct.unpack(">I", reads2[0][2:6])[0] == 17 * lp.BLOCK_BYTES        # resumed at block 17, not 0
    assert sorted(zoe.stored) == sorted(control_zoe.stored)
    for key, (_m, wav) in zoe.stored.items():
        assert wav == control_zoe.stored[key][1]


def test_the_file_being_recorded_stays_open_and_is_not_uploaded_until_it_goes_quiet(spool, tmp_path):
    ts = int(NOW) - 30
    pendant, zoe = FakeLegacyPendant(legacy_files((T1, 40), (ts, 40)), active=ts), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    assert res.uploaded == 1 and res.pending == 1 and pendant.deleted == [T1]    # the active file is not deleted by the firmware
    assert f"f{ts:08x}.0-39" not in zoe.stored
    pendant.files[ts] += [make_block(99) for _ in range(10)]                     # it grew while the owner kept talking
    later = Drain(pendant, spool, zoe, SyncConfig(device_id=DEV, outbox=tmp_path / "outbox", legacy_files=True, assume_codec=21),
                  decoder_factory=lambda c: FakeDecoder(), clock=lambda: NOW + 600)
    res2 = asyncio.run(later.run())
    assert res2.new_records == 10 and res2.uploaded == 1
    assert f"f{ts:08x}.0-49" in zoe.stored                                        # one recording, not two


def test_a_stale_pendant_clock_gives_estimated_times_not_wrong_ones(spool, tmp_path):
    stale = int(NOW) - 109 * 86400                                                  # the owner's pendant: months behind
    pendant, zoe = FakeLegacyPendant(legacy_files((stale - 2000, 40)), dev_time=stale), FakeZoe()
    drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    (meta, _), = zoe.stored.values()
    assert meta.time_quality == "estimated" and abs(meta.started_at - NOW) < 3600   # near the sync, never 109 days ago


def test_blocks_that_are_not_440_bytes_stop_the_read_instead_of_misaligning(spool, tmp_path):
    pendant, zoe = FakeLegacyPendant(legacy_files((T1, 40)), wrong_size=True), FakeZoe()
    res = drain(pendant, spool, zoe, tmp_path, legacy_files=True, assume_codec=21)
    assert not res.read_complete and zoe.stored == {} and spool.stats()["spooled_records"] == 0
    assert lp.encode_stop() in pendant.sent and any("misalign" in n for n in res.notes)


def test_undecodable_audio_is_held_not_uploaded_and_stays_in_the_spool(spool, tmp_path):
    """A wrong codec assumption must never ship junk to Zoe: the raw records are kept so a corrected decoder can retry."""
    class Broken:
        def decode(self, frame):
            raise ValueError("not opus")

    pendant, zoe = FakeLegacyPendant(legacy_files((T1, 40))), FakeZoe()
    config = SyncConfig(device_id=DEV, outbox=tmp_path / "outbox", legacy_files=True, assume_codec=21)
    res = asyncio.run(Drain(pendant, spool, zoe, config, decoder_factory=lambda c: Broken(), clock=lambda: NOW).run())
    assert res.undecodable == 1 and res.uploaded == 0 and zoe.calls == [] and not res.clean
    assert spool.stats()["spooled_records"] == 40 and any("did not decode" in n for n in res.notes)
    ok = asyncio.run(Drain(pendant, spool, zoe, config, decoder_factory=lambda c: FakeDecoder(), clock=lambda: NOW).run())
    assert ok.uploaded == 1                                                          # the same records, decoded properly later


def test_the_legacy_protocol_module_has_no_delete_or_clear_encoder():
    assert lp.CMD_DELETE_FILE == 0x12
    assert not [n for n in dir(lp) if n.startswith("encode") and ("delete" in n.lower() or "clear" in n.lower())]
    assert lp.encode_read_file(3, 0x0102) == bytes([0x11, 3, 0, 0, 1, 2])


def test_file_list_parsing_rejects_data_and_error_bytes():
    ok = bytes([2]) + struct.pack(">II", 5, 880) + struct.pack(">II", 9, 440)
    assert [(f.index, f.timestamp, f.blocks) for f in lp.parse_file_list(ok)] == [(0, 5, 2), (1, 9, 1)]
    assert lp.parse_file_list(bytes([0])) == []
    for bad in (b"", bytes([0xFF]), bytes([3]) + b"\x00" * 8, bytes([0, 1])):
        with pytest.raises(rp.ProtocolError):
            lp.parse_file_list(bad)
    assert lp.is_legacy_file_firmware((3, 0, 18)) and not lp.is_legacy_file_firmware((3, 0, 19))


# --- break-the-fix controls, in-test ------------------------------------------------------------------------------
# Each swaps ONE safety mechanism for its naive version via monkeypatch and shows the assertion that guards it goes
# red - i.e. the tests above can fail. (They complement the Edit-based controls recorded in the PR.)
def test_control_a_naive_advance_target_would_free_unconfirmed_audio(spool, tmp_path, monkeypatch):
    from omi_sync import engine
    monkeypatch.setattr(engine, "advance_target", lambda read_seq, coverage, first_unconfirmed:
                        max((b for _a, b in coverage), default=read_seq - 1) + 1)
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09), FakeZoe(mode="down")
    drain(pendant, spool, zoe, tmp_path)
    assert pendant.read_seq == 120, "control broken: the naive target should have freed everything while Zoe was down"
    assert advances(pendant), "control broken: no ADVANCE reached the wire"


def test_control_without_the_firmware_gate_the_ring_would_be_touched(spool, tmp_path, monkeypatch):
    from omi_sync import engine
    monkeypatch.setattr(engine.rp, "supports_ring", lambda fw: True)
    pendant = FakePendant(build_ring(RING), fw="3.0.19", caps=0x09)
    drain(pendant, spool, FakeZoe(), tmp_path)
    assert pendant.sent, "control broken: with the gate bypassed the old firmware should have received ring commands"


def test_control_closing_a_held_tail_would_upload_a_truncated_recording(spool, tmp_path, monkeypatch):
    from omi_sync import engine
    ctl_spool = Spool(tmp_path / "control.sqlite3")
    control_zoe = FakeZoe()
    drain(FakePendant(build_ring(RING), caps=0x09), ctl_spool, control_zoe, tmp_path / "ctl")
    ctl_spool.close()
    real = engine.segment
    monkeypatch.setattr(engine, "segment", lambda *a, **k: real(*a, **{**k, "hold_tail": False}))
    pendant, zoe = FakePendant(build_ring(RING), caps=0x09, drop_after_notes=100), FakeZoe()
    drain(pendant, spool, zoe, tmp_path)
    drain(pendant, spool, zoe, tmp_path)
    assert sorted(zoe.stored) != sorted(control_zoe.stored), "control broken: the truncated tail should have split a recording"
