"""Spooled ring records -> recordings (segmentation, time resolution, Opus -> WAV).

All pure apart from the lazily-imported ``opuslib`` in ``OpusDecoder`` (tests inject a
fake decoder). The segmentation is DETERMINISTIC over the spooled records, which is what
makes an upload retry idempotent: the same spool always yields the same
``(first_seq, last_seq)`` and therefore the same recording key.

A recording ends at (a) a seq discontinuity (records were lost between - the next
recording says how many, ``gap_before``), (b) a wall-clock gap longer than ``gap_s``
between consecutive records, or (c) ``max_s`` of audio. The LAST segment is only
``closed`` - and so only eligible for upload - when it hit ``max_s`` or has been quiet
for ``gap_s``; otherwise the owner is still recording and it waits (its records stay in
the spool and, under custody firmware, on the pendant).
"""
from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

from . import ring_protocol as rp
from .spool import SpoolRecord

log = logging.getLogger("omi_sync.recordings")

#: ~4 kB/s of Opus / 440 payload bytes per record -> a record covers about this much audio
NOMINAL_RECORD_S = 0.11
#: a timestamp earlier than this (2023-11-14) cannot be a real wall clock - the RTC was never set
MIN_PLAUSIBLE_EPOCH = 1_700_000_000
#: a device clock further than this from the host is "unset/garbage", not "drifted"
MAX_TRUSTED_OFFSET_S = 12 * 3600
#: an offset under this is jitter, not drift - report the device time as-is
NEGLIGIBLE_OFFSET_S = 2

#: float slack so ten 0.1 s records reach a 1.0 s cap (0.1 * 10 == 0.9999999999999999)
_EPS = 1e-6
_QUALITY_RANK = {"device": 0, "corrected": 1, "estimated": 2}


def resolve_time(ts: Optional[int], *, clock_offset: Optional[float], estimate: float, now: float) -> tuple[float, str]:
    """Best host-timeline time for one record -> ``(t_epoch, quality)``.

    ``ts`` is the record's own epoch (``None`` = uptime-flagged). ``clock_offset`` is
    ``device_clock - host_clock`` measured at connect (``None`` = unreadable). ``estimate``
    is the fallback: the receive time minus the audio still queued behind this record.
    """
    if ts is None or ts < MIN_PLAUSIBLE_EPOCH:
        return estimate, "estimated"
    if clock_offset is None:
        return float(ts), "device" if ts <= now + 300 else "estimated"
    if abs(clock_offset) > MAX_TRUSTED_OFFSET_S:
        return estimate, "estimated"
    t = float(ts) - clock_offset
    if t > now + 300:                       # claims to be in the future even after correction
        return estimate, "estimated"
    return t, ("device" if abs(clock_offset) <= NEGLIGIBLE_OFFSET_S else "corrected")


def frame_seconds(codec: int) -> float:
    samples = rp.FRAME_SAMPLES_BY_CODEC.get(codec)
    return samples / rp.SAMPLE_RATE if samples else 0.0


@dataclass
class Segment:
    device: str
    ring: str
    first_seq: int
    last_seq: int
    started_at: float
    ended_at: float
    duration_s: float
    time_quality: str
    gap_before: int
    closed: bool
    codec: int
    records: list[SpoolRecord] = field(repr=False, default_factory=list)

    @property
    def key(self) -> str:
        """Idempotency key: the ring incarnation and the exact seq range (content-determined)."""
        return f"{self.ring}.{self.first_seq}-{self.last_seq}"


def segment(records: Sequence[SpoolRecord], *, device: str, ring: str, now: float, gap_s: float, max_s: float,
            force_close: bool = False, hold_tail: bool = False, split_on_gap: bool = True) -> list[Segment]:
    """Cut the spooled records (ascending seq) into recordings. See the module docstring for the rules.

    ``force_close`` closes the last segment regardless of age: used for a ring incarnation
    that is no longer the pendant's current one (nothing will ever extend it).
    ``hold_tail`` keeps the last segment open however old it is: used when this side has NOT caught up
    with the pendant's write pointer (a dropped or refused READ) - "no newer record in the spool" then
    means "not received yet", not "the owner stopped talking", and closing it would upload a truncated
    recording and split the rest into a second one. A tail that hit ``max_s`` is complete regardless.
    ``split_on_gap=False`` never splits on a wall-clock gap (a legacy FILE is already the firmware's own recording unit, and its
    block times come from one anchor, so a gap between two sessions is an artefact, not a silence); the quiet-tail rule that
    closes the last segment still uses ``gap_s``.
    """
    out: list[Segment] = []
    cur: list[SpoolRecord] = []
    dur = 0.0
    gap_before = 0
    next_gap = 0

    def flush(closed: bool) -> None:
        nonlocal cur, dur, gap_before
        if not cur:
            return
        q = max((r.tq for r in cur), key=lambda t: _QUALITY_RANK.get(t, 2))
        out.append(Segment(
            device=device, ring=ring, first_seq=cur[0].seq, last_seq=cur[-1].seq,
            started_at=cur[0].t_epoch, ended_at=cur[-1].t_epoch + frame_seconds(cur[-1].codec) * max(cur[-1].frames, 0),
            duration_s=round(dur, 3), time_quality=q, gap_before=gap_before, closed=closed,
            codec=cur[0].codec, records=cur))
        cur, dur = [], 0.0

    prev: Optional[SpoolRecord] = None
    for r in records:
        rdur = r.frames * frame_seconds(r.codec)
        if prev is not None:
            hole = r.seq - prev.seq - 1
            if hole > 0:
                flush(True)
                next_gap = hole
            elif (split_on_gap and (r.t_epoch - prev.t_epoch) > gap_s) or dur + _EPS >= max_s or r.codec != prev.codec:
                flush(True)
                next_gap = 0
        if not cur:
            gap_before = next_gap
            next_gap = 0
        cur.append(r)
        dur += rdur
        prev = r
    if cur:
        last = cur[-1]
        quiet = (now - last.t_epoch) >= gap_s
        flush(closed=bool(force_close or dur + _EPS >= max_s or (quiet and not hold_tail)))
    return out


# --- Opus -> WAV --------------------------------------------------------------------
class FrameDecoder(Protocol):
    def decode(self, frame: bytes) -> bytes:
        """One Opus frame -> little-endian 16-bit mono PCM at 16 kHz."""


class OpusDecoder:
    """``opuslib`` over the system libopus. Imported lazily so the rest of the package (and the tests) need neither."""

    def __init__(self, codec: int) -> None:
        import opuslib  # type: ignore[import-not-found]
        self._dec = opuslib.Decoder(rp.SAMPLE_RATE, 1)
        self._max = 960          # the SDK's decode buffer bound, not the wire frame size
        self.samples = rp.FRAME_SAMPLES_BY_CODEC[codec]

    def decode(self, frame: bytes) -> bytes:
        return bytes(self._dec.decode(frame, self._max))


@dataclass
class Rendered:
    wav: bytes
    duration_s: float
    frames: int
    decode_errors: int


def render_wav(seg: Segment, decoder: FrameDecoder) -> Rendered:
    """Decode every Opus frame of the segment into one 16 kHz mono s16 WAV.

    A frame that fails to decode becomes the same length of silence, so the recording's
    timeline does not slip, and is counted (``decode_errors``) so the PR/operator can see it.
    """
    pcm = bytearray()
    frames = errors = 0
    sil = b"\x00\x00" * rp.FRAME_SAMPLES_BY_CODEC.get(seg.codec, 320)
    for r in seg.records:
        for frame in rp.parse_payload(r.record[rp.TIMESTAMP_BYTES:]):
            frames += 1
            try:
                pcm += decoder.decode(frame)
            except Exception as exc:  # noqa: BLE001 - a bad frame is silence, not a lost recording
                errors += 1
                pcm += sil
                log.debug("opus decode failed for seq %s: %s", r.seq, type(exc).__name__)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rp.SAMPLE_RATE)
        w.writeframes(bytes(pcm))
    return Rendered(buf.getvalue(), len(pcm) / 2 / rp.SAMPLE_RATE, frames, errors)
