"""One drain session: pendant storage -> durable spool -> recordings -> Zoe -> (ring path only) free the ring.

THE CUSTODY RULE (the invariant every test in ``tests/unit/test_omi_sync_engine.py`` hangs off,
ported from Omi's own ``app/lib/services/wals/PENDANT_RING_CUSTODY.md``):

    audio is freed from the pendant only after Zoe has CONFIRMED it stored that exact recording.
    Delivery over Bluetooth is not custody.

How it is held:

* every received record is committed to the spool BEFORE anything else happens to it;
* ``Spool.mark_confirmed`` (which drops the records) runs only on ``Outcome.CONFIRMED``;
* on the RING path the pendant is advanced to ``advance_target(...)`` - a pure function of confirmed state: never past the
  first unconfirmed record, never past what we hold or confirmed contiguously from the pendant's own read pointer;
* a dropped link, a refused READ, an inconsistent DONE, an unavailable Zoe, a pendant nobody owns yet (``NOT_REGISTERED``), a
  rejected recording, an undecodable one: none of them advance anything. The next session resumes from the spool + the pendant's pointer, and a seq already held is ignored
  (idempotent by ``(ring, seq)``).

``CMD_RING_CLEAR`` and the legacy ``DELETE_FILE`` (the same byte as ADVANCE!) are never sent: neither protocol module has an
encoder for them.

TWO PATHS, chosen from the DIS firmware string:

* ``ring``    firmware >= 3.0.20 (``ring_protocol``). Default-on once the flag is on.
* ``legacy``  firmware 3.0.18 exactly (``legacy_protocol``), ONLY with ``cfg.legacy_files`` (``OMI_SYNC_LEGACY_FILES=1``). The
  owner's pendant runs 3.0.18. Its firmware deletes each file itself the moment the last byte is notified (see
  ``legacy_protocol``) - a free we can neither gate nor defer - so there the Pi's spool is the ONLY custody, and the drain is
  opt-in rather than quietly on.
* anything else is refused before a single storage command is sent.

STOCK-FIRMWARE CAVEAT (both paths): firmware without the custody capability frees records as the link layer acknowledges them.
The spool is what makes that safe: a record is durable here before our process can lose it, and the only exposure left is the
window between BlueZ receiving a notification and this process committing the batch (``flush_records`` bounds it, and a
crash inside it loses at most that batch).
"""
from __future__ import annotations

import io
import logging
import os
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable, Optional, Protocol, Sequence

from . import legacy_protocol as lp
from . import ring_protocol as rp
from .recordings import FrameDecoder, NOMINAL_RECORD_S, Segment, frame_seconds, render_wav, resolve_time, segment
from .spool import Spool, SpoolRecord
from .uploader import Outcome, UploadMeta, Uploader, sha256_hex

log = logging.getLogger("omi_sync.engine")


class LinkDropped(Exception):
    """The BLE link went away (or went silent) mid-operation."""


class RingLink(Protocol):
    """What the engine needs from a connected pendant. ``ble_link.BleLink`` is the real one; tests fake it."""

    async def firmware(self) -> str: ...
    async def codec(self) -> int: ...
    async def device_time(self) -> Optional[int]: ...
    async def info(self) -> bytes: ...
    def read(self, start_seq: int) -> AsyncIterator[bytes]: ...
    async def advance(self, command: bytes) -> bytes: ...
    async def stop(self) -> None: ...
    async def set_time(self, epoch_s: int) -> None: ...
    async def legacy_list(self) -> bytes: ...
    def legacy_read(self, index: int, offset: int) -> AsyncIterator[bytes]: ...


@dataclass
class SyncConfig:
    device_id: str                      # the hashed pendant id (uploader.device_hash)
    gap_s: float = 90.0                 # silence that ends a recording
    max_s: float = 600.0                # longest single recording (19 MB of WAV)
    flush_records: int = 64             # records per durable commit while reading
    set_device_time: bool = False       # OFF by default: a WRITE to the pendant. When on, done once at the start of a session.
    legacy_files: bool = False          # OFF by default: the 3.0.18 file protocol frees audio on delivery (see module docstring)
    assume_codec: Optional[int] = None  # use when the pendant reports a codec id we do not know (the owner's reads 0x0f)
    max_decode_error_ratio: float = 0.2  # more undecodable frames than this: hold the recording, never upload junk
    outbox: Optional[Path] = None       # staged WAVs awaiting upload (deleted on confirm)


@dataclass
class DrainResult:
    path: Optional[str] = None          # 'ring' | 'legacy'
    refused: Optional[str] = None       # why we did not touch the pendant's storage at all
    new_records: int = 0
    read_complete: bool = False
    dropped: bool = False               # the link died during the session
    uploaded: int = 0
    rejected: int = 0
    undecodable: int = 0                # held: the audio did not decode (wrong codec assumption / corrupt)
    unavailable: bool = False           # Zoe could not be reached / is not accepting
    not_registered: bool = False        # Zoe answered "this pendant is not assigned to a household member": custody kept, ask again later
    pending: int = 0                    # closed recordings left unconfirmed + the open tail
    advanced_to: Optional[int] = None
    notes: list[str] = field(default_factory=list)
    # --- pendant health facts for the heartbeat (``health.build_report``): informational, never read by a custody decision ---
    firmware: Optional[str] = None               # the DIS firmware string
    battery_pct: Optional[int] = None            # standard BLE Battery Service level (0x2A19); None = unreadable
    ring_capacity_records: Optional[int] = None  # ring INFO: capacity in records, and the record size in bytes
    ring_packet_bytes: Optional[int] = None
    ring_unread_start: Optional[int] = None      # unread records when we connected
    ring_after: Optional[tuple] = None           # (read_seq, write_seq) after the drain's ADVANCE, from a fresh INFO
    dropped_total: Optional[int] = None          # the pendant's cumulative count of records it overwrote before they were read

    @property
    def ring_unread_end(self) -> Optional[int]:
        """Records still on the ring when the session ended (None = not measured, e.g. the link dropped before the ADVANCE step)."""
        if self.ring_after is None:
            return None
        return max(0, self.ring_after[1] - self.ring_after[0])

    @property
    def clean(self) -> bool:
        return (self.refused is None and self.read_complete and not self.dropped and not self.unavailable
                and not self.not_registered and not self.rejected and not self.undecodable)


# --- pure custody arithmetic --------------------------------------------------------------
def advance_target(read_seq: int, coverage: Sequence[tuple[int, int]], first_unconfirmed: Optional[int]) -> int:
    """The seq the ring may be advanced TO (everything below it freed). Never above ``read_seq`` means "do nothing".

    ``coverage`` is every inclusive seq interval we hold in the spool OR have confirmed. The target is the
    end of the contiguous run that starts at the pendant's own ``read_seq`` - so a record we never received
    is never freed - and never above the first record still waiting for Zoe's confirmation.
    """
    end = read_seq
    for a, b in sorted(coverage):
        if a <= end < b + 1:
            end = b + 1
    if first_unconfirmed is not None:
        end = min(end, first_unconfirmed)
    return end


def ring_label(spool: Spool, device: str, info: rp.RingInfo) -> str:
    """A stable name for THIS ring incarnation, so seqs from different generations never mix in the spool.

    Custody firmware reports a ``ring_id``. Stock firmware does not; there the best available signal that
    the ring restarted (CLEAR / metadata loss) is that its write pointer is now BELOW a seq we already hold.
    """
    if info.has_ring_id:
        return f"r{info.ring_id:x}"
    key = f"legacy_epoch:{device}"
    epoch = int(spool.get_meta(key, "0") or 0)
    high = spool.high_seq(device, f"legacy{epoch}")
    if high is not None and info.write_seq < high + 1:
        epoch += 1
        spool.set_meta(key, str(epoch))
        log.warning("ring write pointer %s is below the highest held seq %s: treating as a new ring incarnation", info.write_seq, high)
    return f"legacy{epoch}"


def file_label(timestamp: int) -> str:
    """The spool 'ring' name of one legacy file (its hex name). Blocks are numbered from 0 within it."""
    return f"f{timestamp:08x}"


class Drain:
    def __init__(self, link: RingLink, spool: Spool, uploader: Uploader, cfg: SyncConfig, *,
                 decoder_factory: Callable[[int], FrameDecoder], clock: Callable[[], float] = time.time) -> None:
        self.link, self.spool, self.up, self.cfg = link, spool, uploader, cfg
        self._decoder_factory = decoder_factory
        self._clock = clock
        self.res = DrainResult()
        self._open_rings: set[str] = set()

    # ------------------------------------------------------------------------------
    async def run(self) -> DrainResult:
        res, link, cfg = self.res, self.link, self.cfg
        try:
            fw = await link.firmware()
            ver = rp.parse_firmware(fw)
            if rp.supports_ring(fw):
                res.path = "ring"
            elif lp.is_legacy_file_firmware(ver):
                if not cfg.legacy_files:
                    res.refused = (f"firmware {fw!r} is below 3.0.20 (no ring protocol); it speaks the legacy file protocol, whose firmware "
                                   "deletes each file itself on delivery - opt in with OMI_SYNC_LEGACY_FILES=1, or update the pendant (plan 4.10)")
                    return res
                res.path = "legacy"
            else:
                res.refused = f"firmware {fw!r} is below {'.'.join(map(str, rp.MIN_RING_FIRMWARE))} (no ring protocol)"
                return res
            res.firmware = fw
            await self._read_battery()
            reported = await link.codec()
            codec = reported if reported in rp.FRAME_SAMPLES_BY_CODEC else cfg.assume_codec
            if codec not in rp.FRAME_SAMPLES_BY_CODEC:
                res.refused = (f"unsupported codec id {reported} (set OMI_SYNC_ASSUME_CODEC=21 only after confirming the audio decodes; "
                               "the CV1 source says 21, this firmware reports otherwise)")
                return res
            if codec != reported:
                res.notes.append(f"pendant reports codec id {reported}; decoding as {codec} (OMI_SYNC_ASSUME_CODEC)")
            info = rp.parse_info(await link.info()) if res.path == "ring" else None
            if info is not None and info.packet_size != rp.RECORD_BYTES:
                res.refused = f"unexpected ring record size {info.packet_size}"
                return res
        except LinkDropped:
            res.dropped = True
            return res
        except rp.ProtocolError as exc:
            res.refused = f"unreadable INFO: {exc}"
            return res

        device = cfg.device_id
        offset: Optional[float] = None
        try:
            dev_t = await link.device_time()
            offset = None if dev_t is None else float(dev_t) - self._clock()
            await self._maybe_set_time(offset)
        except LinkDropped:
            res.dropped = True

        if res.path == "ring":
            assert info is not None
            ring = ring_label(self.spool, device, info)
            self._open_rings = {ring}
            res.ring_capacity_records, res.ring_packet_bytes = info.capacity_packets, info.packet_size
            res.ring_unread_start, res.dropped_total = info.unread, info.dropped_packets
            if info.dropped_packets:
                res.notes.append(f"pendant overwrote {info.dropped_packets} records before they were read")
            if not (info.caps & rp.CAP_APP_ACK_RECLAIM):
                res.notes.append("legacy firmware: the pendant frees records itself as they are delivered; the spool is the custody")
            if not res.dropped:
                await self._read(info, device, ring, codec, offset)
            await self._upload_all(device)
            if not res.dropped:
                await self._advance(info, device, ring)
        else:
            res.notes.append("firmware 3.0.18 (legacy files): the pendant deletes each file as it is delivered; the spool is the only custody")
            if not res.dropped:
                await self._read_legacy(device, codec, offset)
            await self._upload_all(device)
        if res.uploaded:
            await self.up.notify_synced(res.uploaded, device)
        return res

    async def _read_battery(self) -> None:
        """Best effort and informational: one GATT read of the standard Battery Service. Never affects custody; a fake or an old link
        without it, or a failed read, just leaves the level unknown."""
        reader = getattr(self.link, "battery", None)
        if reader is None:
            return
        try:
            level = await reader()
        except Exception as exc:  # noqa: BLE001 - LinkDropped included: the next operation finds a dropped link on its own
            log.debug("battery unreadable: %s", type(exc).__name__)
            return
        if isinstance(level, int) and not isinstance(level, bool) and 0 <= level <= 100:
            self.res.battery_pct = level

    # ------------------------------------------------------------------------------ ring path
    async def _read(self, info: rp.RingInfo, device: str, ring: str, codec: int, offset: Optional[float]) -> None:
        res, spool, cfg = self.res, self.spool, self.cfg
        high = spool.high_seq(device, ring)
        start = info.read_seq if high is None else max(info.read_seq, high + 1)
        if start >= info.write_seq:
            res.read_complete = True
            return
        reasm = rp.RecordReassembler()
        batch: list[SpoolRecord] = []
        n = 0
        begin: Optional[rp.ReadBegin] = None
        done: Optional[rp.Done] = None

        def flush() -> None:
            nonlocal batch
            if batch:
                res.new_records += spool.add_records(device, ring, batch)
                batch = []

        try:
            async for note in self.link.read(start):
                op = rp.opcode(note)
                if op == rp.NOTIFY_READ_BEGIN:
                    begin = rp.parse_read_begin(note)
                    if begin.start_seq != start:
                        res.notes.append(f"READ_BEGIN start {begin.start_seq} != requested {start}: aborted")
                        break
                elif op == rp.NOTIFY_DATA:
                    reasm.append(note[1:])
                    for record in reasm.drain():
                        seq = start + n
                        n += 1
                        batch.append(self._spool_record(record, seq, codec, info, offset))
                        if len(batch) >= cfg.flush_records:
                            flush()
                elif op == rp.NOTIFY_DONE:
                    done = rp.parse_done(note)
                    break
                elif op == rp.NOTIFY_ACK:
                    ack = rp.parse_ack(note)
                    if not ack.ok:
                        res.notes.append(f"READ refused (ack {ack.status})")
                        break
                # LIVE_MARK / unknown opcodes: not ours
        except LinkDropped:
            res.dropped = True
        except rp.ProtocolError as exc:
            res.notes.append(f"malformed notification during READ: {exc}")
        finally:
            flush()          # whatever arrived is durable BEFORE we decide anything about it
        res.read_complete = bool(
            done and done.ok and done.next_seq == start + n and reasm.pending == 0
            and (begin is None or begin.packet_count == n) and not res.dropped)
        if not res.read_complete and not res.dropped:
            await self._stop_quietly()

    def _spool_record(self, record: bytes, seq: int, codec: int, info: rp.RingInfo, offset: Optional[float]) -> SpoolRecord:
        ts, frames = rp.split_record(record)
        now = self._clock()
        est = now - max(0, info.write_seq - 1 - seq) * NOMINAL_RECORD_S
        t, q = resolve_time(ts, clock_offset=offset, estimate=est, now=now)
        return SpoolRecord(seq=seq, t_epoch=t, tq=q, frames=len(frames), codec=codec, received_at=now, record=record)

    async def _stop_quietly(self) -> None:
        try:
            await self.link.stop()
        except Exception as exc:  # noqa: BLE001 - STOP is a courtesy; the link may already be gone
            log.debug("STOP failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------------------ legacy file path
    def _sealed_key(self, device: str, label: str) -> str:
        return f"sealed:{device}:{label}"

    async def _read_legacy(self, device: str, codec: int, offset: Optional[float]) -> None:
        res, spool = self.res, self.spool
        try:
            files = lp.parse_file_list(await self.link.legacy_list())
        except LinkDropped:
            res.dropped = True
            return
        except rp.ProtocolError as exc:
            res.notes.append(f"unreadable file list: {exc}")
            return
        all_done = True
        handled: set[int] = set()
        newest = max((f.timestamp for f in files), default=None)
        while True:
            f = next((x for x in sorted(files, key=lambda x: x.timestamp) if x.timestamp not in handled), None)
            if f is None:
                break
            handled.add(f.timestamp)
            label = file_label(f.timestamp)
            if spool.get_meta(self._sealed_key(device, label)):
                # a file we finished earlier and the pendant has since reused the name for: not supported, leave it
                res.notes.append(f"file {label} was already fully received; skipped")
                continue
            ok = await self._consume_file(f, device, label, codec, offset)
            if not ok:
                all_done = False
                self._open_rings.add(label)             # cut short: the rest of THIS file is still to come, so its tail must wait
                break
            try:                                         # a finished, non-active file is deleted by the firmware: indices shift
                files = lp.parse_file_list(await self.link.legacy_list())
            except LinkDropped:
                res.dropped = True
                all_done = False
                break
            except rp.ProtocolError:
                all_done = False
                break
            if f.timestamp not in {x.timestamp for x in files}:
                spool.set_meta(self._sealed_key(device, label), "1")        # gone from the pendant: nothing will ever extend it
        res.read_complete = all_done and not res.dropped
        if files and newest is not None:
            label = file_label(newest)
            if not spool.get_meta(self._sealed_key(device, label)):
                self._open_rings.add(label)             # the file still on the pendant may still be the one being recorded

    async def _consume_file(self, f: lp.FileInfo, device: str, label: str, codec: int, offset: Optional[float]) -> bool:
        res, spool, cfg = self.res, self.spool, self.cfg
        have = spool.high_seq(device, label)
        start_block = 0 if have is None else have + 1
        if start_block >= f.blocks:
            return True
        existing = spool.records(device, label)
        seconds_before = sum(r.frames for r in existing) * frame_seconds(codec)
        if existing:                                    # a resumed file keeps the anchor its first block was given
            start_t, start_q = existing[0].t_epoch, existing[0].tq
        else:
            now0 = self._clock()
            start_t, start_q = resolve_time(f.timestamp, clock_offset=offset, estimate=now0 - f.blocks * NOMINAL_RECORD_S, now=now0)
        batch: list[SpoolRecord] = []
        n = 0
        end_seen = False
        failed = False

        def flush() -> None:
            nonlocal batch
            if batch:
                res.new_records += spool.add_records(device, label, batch)
                batch = []

        try:
            async for note in self.link.legacy_read(f.index, start_block * lp.BLOCK_BYTES):
                c = lp.classify(note)
                if isinstance(c, lp.Status):
                    if c.code == lp.STATUS_END:
                        end_seen = True
                        break
                    if c.code == lp.STATUS_EMPTY:
                        end_seen = True
                        break
                    if c.code == lp.STATUS_ACK:
                        continue
                    res.notes.append(f"READ_FILE refused (status {c.code}) for {label}")
                    failed = True
                    break
                if len(note) != lp.NOTIFY_BLOCK_BYTES or c.timestamp != f.timestamp:
                    res.notes.append(f"{label}: notification of {len(note)} bytes / timestamp mismatch - blocks would misalign; stopped")
                    failed = True
                    break
                seq = start_block + n
                n += 1
                record = lp.block_to_record(c.timestamp, c.payload)
                ts_unused, frames = rp.split_record(record)
                now = self._clock()
                t, q = start_t + seconds_before, start_q            # one anchor per file; the audio itself supplies the spacing
                seconds_before += len(frames) * frame_seconds(codec)
                batch.append(SpoolRecord(seq=seq, t_epoch=t, tq=q, frames=len(frames), codec=codec, received_at=now, record=record))
                if len(batch) >= cfg.flush_records:
                    flush()
        except LinkDropped:
            res.dropped = True
        except rp.ProtocolError as exc:
            res.notes.append(f"malformed notification in {label}: {exc}")
            failed = True
        finally:
            flush()
        if failed and not res.dropped:
            await self._stop_quietly()
        return bool(end_seen and not failed and not res.dropped)

    # ------------------------------------------------------------------------------ upload
    def _outbox_path(self, seg: Segment) -> Optional[Path]:
        if self.cfg.outbox is None:
            return None
        return self.cfg.outbox / (f"{seg.device}-{seg.key}.wav")

    def _stage(self, seg: Segment) -> Optional[bytes]:
        """The recording's WAV: the staged file if a previous session already rendered it, else render + stage it.
        ``None`` = the audio does not decode well enough to upload (held, never discarded)."""
        path = self._outbox_path(seg)
        if path is not None and path.is_file():
            return path.read_bytes()
        rendered = render_wav(seg, self._decoder_factory(seg.codec))
        if rendered.frames == 0 or rendered.decode_errors > rendered.frames * self.cfg.max_decode_error_ratio:
            self.res.notes.append(f"{seg.key}: {rendered.decode_errors}/{rendered.frames} frames did not decode - held, not uploaded "
                                  "(wrong codec assumption or corrupt audio; the raw records stay in the spool)")
            return None
        if rendered.decode_errors:
            self.res.notes.append(f"{seg.key}: {rendered.decode_errors} undecodable frame(s) replaced with silence")
        # The decoded level, so "the pendant heard nothing" (peak ~0) is visible on the Pi and distinguishable from "it did not decode".
        self.res.notes.append(f"{seg.key}: {rendered.duration_s:.1f}s level peak={rendered.peak} rms={rendered.rms}")
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            with open(tmp, "wb", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
                fh.write(rendered.wav)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        return rendered.wav

    @staticmethod
    def _wav_seconds(wav: bytes) -> float:
        with wave.open(io.BytesIO(wav)) as w:
            return w.getnframes() / float(w.getframerate())

    async def _upload_all(self, device: str) -> None:
        res, spool, cfg = self.res, self.spool, self.cfg
        now = self._clock()
        rings = sorted(set(spool.rings(device)) | self._open_rings)
        stop = False
        for ring in rings:
            is_open = ring in self._open_rings
            segs = segment(spool.records(device, ring), device=device, ring=ring, now=now,
                           gap_s=cfg.gap_s, max_s=cfg.max_s, force_close=not is_open,
                           hold_tail=(is_open and not res.read_complete), split_on_gap=(res.path != "legacy"))
            for seg in segs:
                if not seg.closed:
                    res.pending += 1          # the owner may still be recording: wait
                    continue
                if stop:
                    res.pending += 1
                    continue
                wav = self._stage(seg)
                if wav is None:
                    res.undecodable += 1
                    res.pending += 1
                    continue
                meta = UploadMeta(device_id=device, key=seg.key, started_at=seg.started_at, duration_s=self._wav_seconds(wav),
                                  time_quality=seg.time_quality, gap_before=seg.gap_before, sha256=sha256_hex(wav))
                out = await self.up.upload(meta, wav)
                if out.outcome is Outcome.CONFIRMED:
                    spool.mark_confirmed(device, ring, seg.first_seq, seg.last_seq, seg.key, now=now)
                    path = self._outbox_path(seg)
                    if path is not None:
                        path.unlink(missing_ok=True)
                    res.uploaded += 1
                elif out.outcome is Outcome.NOT_REGISTERED:
                    # Nobody owns this pendant yet. Nothing is confirmed, so nothing is freed; the rest of the session would get the
                    # same answer, so stop asking. The runner waits OMI_SYNC_UNREGISTERED_RETRY_S before the next visit.
                    res.not_registered = True
                    res.pending += 1
                    res.notes.append("Zoe: this pendant is not assigned to anyone yet (an admin assigns it in Settings > Bluetooth); kept")
                    stop = True
                elif out.outcome is Outcome.REJECTED:
                    res.rejected += 1
                    res.pending += 1
                    res.notes.append(f"{seg.key} rejected by Zoe ({out.detail}); kept")
                else:
                    res.unavailable = True
                    res.pending += 1
                    res.notes.append(f"Zoe unavailable ({out.detail}); kept")
                    stop = True               # no point hammering a down server with the rest

    # ------------------------------------------------------------------------------ ring ADVANCE
    async def _advance(self, info_before: rp.RingInfo, device: str, ring: str) -> None:
        res, spool, link = self.res, self.spool, self.link
        try:
            info = rp.parse_info(await link.info())          # the pendant may have moved its own pointer meanwhile
        except (LinkDropped, rp.ProtocolError):
            info = info_before
        else:
            res.ring_after = (info.read_seq, info.write_seq)          # health only: what the pendant holds before our ADVANCE
            res.dropped_total = max(res.dropped_total or 0, info.dropped_packets)
        remaining = spool.records(device, ring)
        first_unconfirmed = remaining[0].seq if remaining else None
        target = advance_target(info.read_seq, spool.coverage(device, ring), first_unconfirmed)
        if target <= info.read_seq:
            return
        command = (rp.encode_advance_id(info.ring_id, target) if info.has_ring_id and info.ring_id
                   else rp.encode_advance(target))
        try:
            ack = rp.parse_ack(await link.advance(command))
        except (LinkDropped, rp.ProtocolError) as exc:
            res.notes.append(f"ADVANCE to {target} unacknowledged ({type(exc).__name__}); the next session re-sends it")
            return
        if ack.ok:
            res.advanced_to = target
            res.ring_after = (target, info.write_seq)
            return
        if ack.status == rp.ACK_SEQ_OUT_OF_RANGE:
            # benign iff the pendant's read pointer is already at/after what we asked for (it freed it itself)
            try:
                again = rp.parse_info(await link.info())
            except (LinkDropped, rp.ProtocolError):
                return
            if target <= again.read_seq:
                res.advanced_to = again.read_seq
                res.ring_after = (again.read_seq, again.write_seq)
            else:
                res.notes.append(f"ADVANCE {target} out of range (read {again.read_seq}, write {again.write_seq})")
        elif ack.status == rp.ACK_RING_ID_MISMATCH:
            res.notes.append("ADVANCE refused: ring incarnation changed; nothing freed")
        else:
            res.notes.append(f"ADVANCE refused (ack {ack.status}); nothing freed")

    # ------------------------------------------------------------------------------ the one pendant WRITE
    async def _maybe_set_time(self, offset: Optional[float]) -> None:
        """OFF unless ``cfg.set_device_time``: the one write the sync service ever makes to the pendant's clock. Done once, at the
        START of a session, after the offset was measured (so records already stored are still corrected by the OLD offset)."""
        if not self.cfg.set_device_time:
            return
        if offset is not None and abs(offset) <= 2:
            return
        try:
            await self.link.set_time(int(self._clock()))
            self.res.notes.append(f"pendant clock set (was {'unreadable' if offset is None else f'{offset:+.0f}s off'})")
        except Exception as exc:  # noqa: BLE001 - never fail a drain over the clock
            self.res.notes.append(f"device time not set ({type(exc).__name__})")
