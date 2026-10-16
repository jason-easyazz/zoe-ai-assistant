"""The owner's pendant recordings: store what the Pi synced, transcribe it locally, keep it reviewable and deletable.

Flag-dark: ``ZOE_OMI_SYNC_ENABLED`` (default OFF). Nothing in this module runs, and ``routers/omi_recordings.py`` is not
mounted, while it is off. Design: ``docs/architecture/omi-integration-plan.md`` section 4 ("record on the pendant, sync
when in range of Zoe"). Table: migration 0045.

THE FLOW
  Pi (services/omi-sync)  --POST /api/omi/recordings, device token, raw 16 kHz WAV-->  ``store_recording``
  ``store_recording``     verifies the SHA-256, writes the WAV (fsync + rename, 0600), INSERTs the row, and only THEN answers
                          ``ok`` with the digest echoed - that answer is the Pi's licence to free the audio on the pendant.
  ``transcribe_pending``  (background, ``run_loop``) Moonshine, in-process, chunk by chunk, between voice turns - the existing
                          STT path (``routers.voice_tts._transcribe_audio(..., capture=False)``), no second model.
  the WAV is deleted right after a transcription that PRODUCED TEXT unless the owner kept it; ``purge_expired_audio`` bounds the rest.
  An EMPTY transcript is not a success: the row goes ``empty_transcript``, the WAV is KEPT until its retention expiry, and a WARNING
  logs its measured length/peak/RMS - so a deaf pipeline (wrong rate, dead mic, gain) never silently eats the owner's audio.
  The WARNING's ``reason`` reads the transient-robust levels (``measure_wav``): ``transient_only`` is a click over room noise (the
  pendant's mic-wake transient opens most short recordings), ``voiced_but_no_text`` is sustained sound Moonshine found no words in.
  ``requeue_held`` (owner routes, and once per ``TRANSCRIBE_PIPELINE`` automatically) puts held recordings back in the queue.

WHAT THIS DOES NOT DO (on purpose, and why)
  * It does not feed the transcript to fact extraction, recall, the digest or the memory gate. A pendant recording can hold
    other people's words and no speaker gate runs on it yet (``speaker_state`` is ``unverified``, ``reviewed`` stays 0). The
    transcript is the owner's to read and delete; nothing derives from it until a reviewed-promotion step exists.
  * It never transcribes during the night window (the 12B owns the box 02:30-04:30: clock OR the ``WINDOW_OPEN`` marker),
    never while a voice turn is in flight, and never below the RAM floor (``ZOE_OMI_MIN_MEM_MB``).
  * It never holds Moonshine for more than one ~20 s chunk at a time, so a live voice turn waits at most one chunk.

"Forgotten means forever": ``forget_user`` (right to be forgotten) and ``erase_entity`` (forget the name "Dana") run through
``user_erase_gate`` exactly like ``commitments`` - an in-flight store/transcription lands first and is erased with the rest, or
is dropped; a transcript is redacted against the forget ledger as it lands.
"""
from __future__ import annotations

import array
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import user_erase_gate as erase_gate
from typed_env import env_bool, env_int, env_str

logger = logging.getLogger(__name__)

#: hard ingress bound for one upload (a 600 s 16 kHz mono WAV is 19.2 MB; the Pi caps recordings at 600 s by default)
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_DURATION_S = 1800.0
SAMPLE_RATE = 16000
CHUNK_S = 20.0                    # one Moonshine call at a time; a voice turn waits at most this long
SPLIT_SEARCH_S = 3.0              # look for the quietest 100 ms in the last 3 s of a chunk to cut at
SILENCE_PEAK = 300                # int16 peak under this = room noise: not sent to Moonshine (it hallucinates on silence)
ACTIVE_RMS = 600                  # a 100 ms window at/above this RMS (~-35 dBFS) is sustained sound; the pendant's room floor is ~40-300
TRANSIENT_ONLY_S = 0.5            # under this much sustained sound, a "voiced" recording was a click (diagnostic label only, never a gate)
MAX_ATTEMPTS = 3
#: Bump whenever transcription or its empty-transcript diagnostics change. The first loop pass after a deploy that changes it requeues
#: every held empty/failed recording ONCE (``requeue_after_pipeline_change``), so the change reaches audio still inside its retention.
TRANSCRIBE_PIPELINE = "2026-10-16.transient-robust-levels"
STALE_CLAIM_S = 30 * 60
POLL_S = 60.0
VOICE_BUSY_S = 45.0              # a voice turn this recent = "in flight": it aborts/holds a running job
HOLD_MAX_S = 300.0               # a RUNNING job waits out a transient closure (voice turn / low RAM) this long before it gives up
HOLD_POLL_S = 5.0
DEFERRED_LOG_EVERY_S = 600.0     # OMI_TRANSCRIBE_DEFERRED is logged at most once per reason per 10 minutes
NIGHT_START, NIGHT_END = (2, 30), (4, 30)      # household-local; matches the 12B night window

DEFAULT_RETENTION_DAYS = 7
_FORGOTTEN_MARK = "[forgotten]"
_TIME_QUALITIES = ("device", "corrected", "estimated")
_KEY_RX = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_DEVICE_RX = re.compile(r"^[A-Za-z0-9]{8,64}$")


# --- flags ---------------------------------------------------------------------------------------------
def enabled() -> bool:
    """``ZOE_OMI_SYNC_ENABLED`` - default OFF. The routes are not mounted and no loop starts while off."""
    return env_bool("ZOE_OMI_SYNC_ENABLED", default=False)


def owner_user_id() -> str:
    """``ZOE_OMI_OWNER_USER_ID`` - whose pendant it is, while the device registry is OFF (``ZOE_OMI_DEVICE_OWNERS`` unset). Unset = uploads
    are refused (503): a device token authenticates the DEVICE, never the person, so the owner is named by configuration, not by whoever
    happens to be bound to the panel. With the registry ON this is only the one-time seed (``omi_devices.ensure_seeded``) - uploads are
    attributed by ``omi_devices.owner_of(<X-Omi-Device>)`` and nothing else."""
    return env_str("ZOE_OMI_OWNER_USER_ID", "").strip()


def audio_dir() -> Path:
    raw = env_str("ZOE_OMI_AUDIO_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "omi-recordings"


def retention_days() -> int:
    return max(1, env_int("ZOE_OMI_AUDIO_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))


def keep_audio_default() -> bool:
    """``ZOE_OMI_KEEP_AUDIO`` - keep every WAV (until its retention expiry) after transcription. Default OFF."""
    return env_bool("ZOE_OMI_KEEP_AUDIO", default=False)


#: Default RAM floor for STARTING a recording, MB of ``MemAvailable``. See ``docs/architecture/omi-integration-plan.md`` section 4
#: ("The RAM floor") for the measurement this number comes from.
DEFAULT_MIN_MEM_MB = 800


def min_mem_mb() -> int:
    """``ZOE_OMI_MIN_MEM_MB`` - the MemAvailable a NEW recording needs before it is claimed. A job already running only yields below
    HALF of this (``continue_floor_mb``): the incremental cost of a chunk is already inside the process, so a dip between the two
    levels must not throw away the chunks transcribed so far."""
    return max(0, env_int("ZOE_OMI_MIN_MEM_MB", DEFAULT_MIN_MEM_MB))


def continue_floor_mb() -> int:
    return min_mem_mb() // 2


def idle_s() -> float:
    """``ZOE_OMI_IDLE_S`` - a NEW recording is only claimed once the panels have had no voice turn for this long (default 120 s), so the
    ~2-3 minute job starts in a quiet spell. A running job only yields to a turn in the last ``VOICE_BUSY_S``."""
    return float(max(0, env_int("ZOE_OMI_IDLE_S", 120)))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- gates ---------------------------------------------------------------------------------------------
def night_window(now: Optional[datetime] = None) -> bool:
    """True while the 12B night window may own the box: the ``WINDOW_OPEN`` marker, or household-local 02:30-04:30."""
    try:
        from self_model import night_window_open
        if night_window_open():
            return True
    except Exception:  # noqa: BLE001 - an unreadable marker must not make us transcribe INTO the window: fall to the clock
        pass
    try:
        from time_utils import zoe_timezone
        local = (now or _now()).astimezone(zoe_timezone())
    except Exception:  # noqa: BLE001
        local = now or _now()
    return NIGHT_START <= (local.hour, local.minute) < NIGHT_END


def mem_available_mb() -> Optional[float]:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


def last_voice_age_s(now_mono: Optional[float] = None) -> Optional[float]:
    """Seconds since the newest panel voice turn, or None when there has been none. Reads the voice router's session table WITHOUT
    importing it."""
    mod = sys.modules.get("routers.voice_tts")
    sessions = getattr(mod, "_VOICE_SESSIONS", None) if mod else None
    if not sessions:
        return None
    t = time.monotonic() if now_mono is None else now_mono
    try:
        return min(t - float(e.get("last_at", -1e9)) for e in list(sessions.values()))
    except Exception:  # noqa: BLE001
        return None


def voice_busy(now_mono: Optional[float] = None) -> bool:
    """A panel voice turn happened in the last ``VOICE_BUSY_S`` seconds."""
    age = last_voice_age_s(now_mono)
    return age is not None and age < VOICE_BUSY_S


# Gate reasons: the human string ``may_transcribe_now`` returns, and the stable code the log line / API use.
WHY_VOICE = "voice turn in flight"
WHY_NOT_IDLE = "panel not idle"
WHY_MEM_PREFIX = "MemAvailable"


def reason_code(why: str) -> str:
    if why.startswith(WHY_MEM_PREFIX):
        return "low_memory"
    if why == WHY_VOICE:
        return "voice_active"
    if why == WHY_NOT_IDLE:
        return "panel_not_idle"
    if why == "night window":
        return "night_window"
    if why == "flag off":
        return "flag_off"
    return "other"


def is_transient(why: str) -> bool:
    """A closure a RUNNING job should wait out (a voice turn ends, RAM comes back) rather than abort on. The flag and the night
    window are decisions, not weather: they end the job at once."""
    return reason_code(why) in ("low_memory", "voice_active", "panel_not_idle")


def may_transcribe_now(now: Optional[datetime] = None, *, continuing: bool = False) -> tuple[bool, str]:
    """Is it OK to run Moonshine on a chunk right now? ``continuing=False`` is the START gate (claiming a new recording): full RAM
    floor and a panel that has been idle for ``ZOE_OMI_IDLE_S``. ``continuing=True`` is the per-chunk gate of a job already running:
    half the floor and only the 45 s voice-turn window, so a brief dip does not discard the chunks already transcribed."""
    if not enabled():
        return False, "flag off"
    if night_window(now):
        return False, "night window"
    if voice_busy():
        return False, WHY_VOICE
    if not continuing:
        age = last_voice_age_s()
        if age is not None and age < idle_s():
            return False, WHY_NOT_IDLE
    floor = continue_floor_mb() if continuing else min_mem_mb()
    mem = mem_available_mb()
    if mem is not None and mem < floor:
        return False, f"{WHY_MEM_PREFIX} {mem:.0f} MB < {floor} MB"
    return True, ""


# --- deferral visibility ------------------------------------------------------------------------------------------
# One process, one loop: module state is the truth for "is transcription waiting, and why". Counts + codes only - never content.
_deferred: dict[str, Any] = {"reason": None, "since": None}
_deferred_logged: dict[str, float] = {}


def deferred_state() -> dict:
    return {"deferred": _deferred["reason"] is not None, "reason": _deferred["reason"], "since": _deferred["since"],
            "mem_available_mb": None if (m := mem_available_mb()) is None else int(m), "mem_floor_mb": min_mem_mb()}


def _clear_deferred() -> None:
    _deferred["reason"], _deferred["since"] = None, None


async def note_deferred(why: str, *, now_mono: Optional[float] = None) -> None:
    """Record that the gate is closed (``/api/omi/recordings`` shows it) and log it at INFO - at most once per reason per
    ``DEFERRED_LOG_EVERY_S``. Silent when nothing is waiting: a closed gate with an empty backlog is not news. The expected daily
    closures (night window, flag off) are state only, never log noise."""
    code = reason_code(why)
    if code == "flag_off":
        _clear_deferred()                            # the feature is off: nothing is "waiting", so no stale reason may linger
        return
    if code == "other":
        return
    try:
        n = await backlog_count()
    except Exception:  # noqa: BLE001 - diagnostics must never break the loop
        return
    if n <= 0:
        _clear_deferred()
        return
    if _deferred["reason"] != code:
        _deferred["reason"], _deferred["since"] = code, _iso(_now())
    if code == "night_window":
        return
    t = time.monotonic() if now_mono is None else now_mono
    last = _deferred_logged.get(code)
    if last is not None and t - last < DEFERRED_LOG_EVERY_S:
        return
    _deferred_logged[code] = t
    mem = mem_available_mb()
    logger.info("OMI_TRANSCRIBE_DEFERRED reason=%s avail=%s floor=%d backlog=%d", code,
                "unknown" if mem is None else int(mem), min_mem_mb(), n)


# --- WAV / chunking (pure) --------------------------------------------------------------------------------------
class BadAudio(ValueError):
    pass


class GateClosed(Exception):
    """The transcription gate (night window / voice turn / RAM) closed mid-job. NOT a failure: the recording goes back to pending.
    A dedicated type on purpose - a Moonshine ``RuntimeError`` must count as a failed attempt, not as 'try again forever'."""


def inspect_wav(data: bytes) -> float:
    """Validate a 16 kHz mono 16-bit PCM WAV and return its duration in seconds. Raises ``BadAudio``."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
                raise BadAudio("expected 16 kHz mono 16-bit PCM")
            n = w.getnframes()
    except (wave.Error, EOFError) as exc:
        raise BadAudio("not a readable WAV") from exc
    dur = n / SAMPLE_RATE
    if n == 0:
        raise BadAudio("empty audio")
    if dur > MAX_DURATION_S:
        raise BadAudio("recording too long")
    return dur


def _samples(pcm: bytes) -> "array.array":
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _quietest_cut(a: "array.array", pos: int, end: int, search: int, win: int) -> int:
    """The sample index in the last ``search`` samples before ``end`` (not before ``pos + win``) that centres the quietest ``win`` window."""
    lo = max(pos + win, end - search)
    best, best_e = end, None
    i = lo
    while i + win <= end:
        e = sum(abs(v) for v in a[i:i + win])
        if best_e is None or e < best_e:
            best, best_e = i + win // 2, e
        i += win
    return best


def chunk_pcm(pcm: bytes, *, rate: int = SAMPLE_RATE, target_s: float = CHUNK_S, search_s: float = SPLIT_SEARCH_S,
              win_ms: int = 100) -> list[tuple[int, int]]:
    """Cut PCM16 into ``(start_byte, end_byte)`` chunks of about ``target_s``, each cut placed at the QUIETEST 100 ms window within the
    last ``search_s`` of the chunk so a word is not sliced in half. Contiguous, covering, never empty. (The whole-buffer form, for tests
    and short audio; ``iter_wave_chunks`` is the streaming equivalent the transcriber uses - same cuts.)"""
    a = _samples(pcm)
    n = len(a)
    target = int(target_s * rate)
    search = int(search_s * rate)
    win = max(1, int(rate * win_ms / 1000))
    out: list[tuple[int, int]] = []
    pos = 0
    while pos < n:
        end = pos + target
        if end >= n:
            end = n
        else:
            end = _quietest_cut(a, pos, end, search, win)
        out.append((pos * 2, end * 2))
        pos = end
    return out


def iter_wave_chunks(w: Any, *, target_s: float = CHUNK_S, search_s: float = SPLIT_SEARCH_S, win_ms: int = 100) -> Iterator[bytes]:
    """The same chunks as ``chunk_pcm`` but STREAMED from an open ``wave`` reader: never more than one chunk (~640 KB) plus the leftover
    tail (<= ``search_s``) is held, so a 10-minute recording costs the same RAM as a 20-second one (the old path held the file, a
    copy of its PCM and an array of it: ~60 MB transient per 600 s recording). ``w`` needs ``getframerate/getnframes/readframes``."""
    rate = w.getframerate()
    total = w.getnframes()
    target = int(target_s * rate)
    search = int(search_s * rate)
    win = max(1, int(rate * win_ms / 1000))
    buf = b""
    consumed = 0
    while True:
        want = target - len(buf) // 2
        if want > 0 and consumed < total:
            got = w.readframes(min(want, total - consumed))
            if not got:                                   # header promised more than the file holds: treat what we have as the end
                total = consumed
            consumed += len(got) // 2
            buf += got
        have = len(buf) // 2
        if have == 0:
            return
        if consumed >= total and have <= target:
            yield buf
            return
        cut = _quietest_cut(_samples(buf[:target * 2]), 0, target, search, win)
        yield buf[:cut * 2]
        buf = buf[cut * 2:]


def peak(pcm: bytes) -> int:
    a = _samples(pcm)
    return max(map(abs, a)) if len(a) else 0


def _measure_reader(w: Any) -> dict:
    rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    pk, sumsq, count, clipped = 0, 0, 0, 0
    win = max(1, (rate or SAMPLE_RATE) // 10)                # 100 ms windows for the transient-robust numbers
    wins: list[float] = []
    if width == 2:
        left = n
        step = max(win, (max(rate, 1) // win) * win)           # one second at a time (a whole number of windows): never the whole file
        while left > 0:
            a = _samples(w.readframes(min(left, step)))
            if not len(a):
                break
            left -= len(a)
            count += len(a)
            for i in range(0, len(a), win):
                part = a[i:i + win]
                sq = sum(v * v for v in part)
                sumsq += sq
                m = max(map(abs, part))
                if m > pk:
                    pk = m
                if m >= 32767:
                    clipped += sum(1 for v in part if v >= 32767 or v <= -32768)
                if len(part) == win:
                    wins.append((sq / win) ** 0.5)
    rms = (sumsq / count) ** 0.5 if count else 0.0
    wins_sorted = sorted(wins)
    return {"seconds": round(n / rate, 1) if rate else 0.0, "peak": int(pk), "rms": round(rms, 1), "rate": rate, "channels": ch,
            "width": width, "clipped": clipped, "rms_median": round(wins_sorted[len(wins_sorted) // 2], 1) if wins_sorted else 0.0,
            "active_s": round(sum(1 for r in wins if r >= ACTIVE_RMS) * win / (rate or SAMPLE_RATE), 1)}


_UNREADABLE = {"seconds": 0.0, "peak": 0, "rms": 0.0, "rate": 0, "channels": 0, "width": 0, "clipped": 0, "rms_median": 0.0,
               "active_s": 0.0}


def measure_wav(wav: bytes) -> dict:
    """What the audio actually is, for the empty-transcript WARNING. Never raises (an unreadable WAV measures as zeros) - it is
    diagnostics, not a gate. Levels are int16 units (full scale 32768).

    ``seconds``/``rate``/``channels``/``width``: the format. ``peak``/``rms``: whole-file - and BOTH are dominated by a single
    click: one full-scale pendant wake transient over ten seconds of room noise reads "peak 32768, rms 700" (measured 2026-10-16,
    plan 4.3b: the median 100 ms-window RMS of those files was ~60). So the transient-robust numbers are what to read:
    ``clipped`` (samples on the int16 rails, 32767 / -32768), ``rms_median`` (the median 100 ms-window RMS: the room, not the click) and ``active_s``
    (seconds of 100 ms windows at or above ``ACTIVE_RMS``: sustained sound, which a click is not)."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            return _measure_reader(w)
    except (wave.Error, EOFError):
        return dict(_UNREADABLE)


def measure_wav_file(path: str) -> dict:
    """``measure_wav`` over a file on disk, streamed (the empty-transcript path must not load a 19 MB recording to say it was quiet)."""
    try:
        with wave.open(path) as w:
            return _measure_reader(w)
    except (wave.Error, EOFError, OSError):
        return dict(_UNREADABLE)


def empty_reason(chunk_stats: dict, level: dict) -> str:
    """Why a recording came back with no text - the label on ``OMI_EMPTY_TRANSCRIPT``:
    ``below_silence_floor`` - no chunk reached Moonshine (peak under ``SILENCE_PEAK``);
    ``transient_only``      - chunks reached Moonshine only because of a click (a pendant wake transient, a tap): under
                              ``TRANSIENT_ONLY_S`` of sustained sound. Nothing was said; Moonshine was right;
    ``voiced_but_no_text``  - there WAS sustained sound and Moonshine still found no words. This is the one worth a look."""
    if not chunk_stats.get("voiced"):
        return "below_silence_floor"
    if float(level.get("active_s") or 0.0) < TRANSIENT_ONLY_S:
        return "transient_only"
    return "voiced_but_no_text"


# --- rows -----------------------------------------------------------------------------------------------------------
_PUBLIC = ("id", "source", "provenance", "device_id", "recording_key", "started_at", "ended_at", "duration_s", "time_quality",
           "gap_before", "audio_bytes", "keep_audio", "audio_expires_at", "status", "speaker_state", "reviewed", "created_at",
           "transcribed_at")


def _public(row: Any, *, transcript: bool = False) -> dict:
    d = {k: row[k] for k in _PUBLIC}
    d["has_audio"] = bool(row["audio_path"])
    d["keep_audio"] = bool(d["keep_audio"])
    d["reviewed"] = bool(d["reviewed"])
    if transcript:
        d["transcript"] = row["transcript"]
    return d


async def _fetch(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


def _write_audio(path: Path, data: bytes) -> None:
    """Atomic, durable, owner-only: a confirmed upload must survive a crash one microsecond later."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".part")
    with open(tmp, "wb", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so a rename/unlink inside it survives a crash. Best effort (some filesystems refuse a dir fsync)."""
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _unlink(path: str) -> None:
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("omi_recordings: audio file not removed (%s)", type(exc).__name__)


class StoreError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


async def store_recording(user_id: str, *, device_id: str, key: str, sha256: str, started_at: str, duration_s: float,
                          time_quality: str, gap_before: int, wav: bytes, scheduled_at: Optional[int] = None,
                          registry: bool = False) -> dict:
    """Store one synced recording and return the confirmation the Pi needs. Idempotent by ``(user, device, key)``.

    ``registry`` (the device-owners mode): ``user_id`` came from ``omi_devices.owner_of(device_id)``; it is re-checked INSIDE the member's
    erase gate, so an erase (which unassigns their pendants) or a reassignment that lands between the lookup and the write wins -
    the upload is refused 409 ``device_not_registered`` and the Pi keeps the audio.

    Returns ``{"ok": True, "id", "key", "sha256", "duplicate": bool}`` or, for a tombstone, ``{"ok": True, "deleted": True}``.
    Raises ``StoreError`` (status, code) for a refusal; the Pi treats 4xx as "never retry this one" and everything else as "try later".
    """
    if not _DEVICE_RX.match(device_id or ""):
        raise StoreError(400, "bad device id")
    if not _KEY_RX.match(key or ""):
        raise StoreError(400, "bad recording key")
    if time_quality not in _TIME_QUALITIES:
        raise StoreError(400, "bad time quality")
    if not wav or len(wav) > MAX_UPLOAD_BYTES:
        raise StoreError(413, "audio too large")
    digest = hashlib.sha256(wav).hexdigest()
    if (sha256 or "").lower() != digest:
        raise StoreError(422, "sha256 does not match the body")        # a truncated upload is refused, never confirmed
    try:
        inspect_wav(wav)
    except BadAudio as exc:
        raise StoreError(422, str(exc)) from exc
    try:
        start = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as exc:
        raise StoreError(422, "bad started_at") from exc
    if start > _now() + timedelta(days=1):
        raise StoreError(422, "started_at is in the future")
    dur = float(duration_s)

    from db_compat import get_compat_db
    gen = erase_gate.generation(user_id) if scheduled_at is None else scheduled_at
    async with erase_gate.writing(user_id, gen) as live:
        if not live:
            raise StoreError(409, "erased while uploading")
        if registry:
            import omi_devices
            if await omi_devices.owner_of(device_id) != user_id:
                raise StoreError(409, "device_not_registered")
        async with get_compat_db() as db:
            rows = await _fetch(db, "SELECT id, sha256, status FROM omi_recordings WHERE user_id = ? AND device_id = ? AND recording_key = ?",
                                (user_id, device_id, key))
            if rows:
                r = rows[0]
                if r["status"] == "deleted":
                    return {"ok": True, "deleted": True, "key": key}
                if r["sha256"] != digest:
                    raise StoreError(409, "key already stored with different content")
                return {"ok": True, "id": r["id"], "key": key, "sha256": digest, "duplicate": True}
            rid = str(uuid.uuid4())
            path = audio_dir() / f"{rid}.wav"
            try:
                _write_audio(path, wav)
            except OSError as exc:
                logger.error("omi_recordings: audio not written (%s)", type(exc).__name__)
                raise StoreError(503, "storage unavailable") from exc
            now = _now()
            expires = _iso(now + timedelta(days=retention_days()))
            try:
                await db.execute(
                    "INSERT INTO omi_recordings (id, user_id, source, provenance, device_id, recording_key, sha256, started_at, ended_at, "
                    "duration_s, time_quality, gap_before, audio_path, audio_bytes, keep_audio, audio_expires_at, status, speaker_state, "
                    "reviewed, created_at) VALUES (?, ?, 'omi', 'recording', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'unverified', 0, ?) "
                    "ON CONFLICT (user_id, device_id, recording_key) DO NOTHING",
                    (rid, user_id, device_id, key, digest, _iso(start), _iso(start + timedelta(seconds=dur)), dur, time_quality,
                     int(gap_before), str(path), len(wav), 1 if keep_audio_default() else 0, expires, _iso(now)))
                await db.commit()
                won = await _fetch(db, "SELECT id, sha256 FROM omi_recordings WHERE user_id = ? AND device_id = ? AND recording_key = ?",
                                   (user_id, device_id, key))
            except Exception as exc:  # noqa: BLE001 - never confirm what we could not record
                _unlink(str(path))
                logger.error("omi_recordings: insert failed (%s)", type(exc).__name__)
                raise StoreError(503, "database unavailable") from exc
            if not won or won[0]["id"] != rid:                         # a concurrent identical upload won the race
                _unlink(str(path))
                if won and won[0]["sha256"] == digest:
                    return {"ok": True, "id": won[0]["id"], "key": key, "sha256": digest, "duplicate": True}
                raise StoreError(409, "key already stored with different content")
    logger.info("OMI_STORED id=%s seconds=%.1f quality=%s gap_before=%d", rid, dur, time_quality, int(gap_before))
    return {"ok": True, "id": rid, "key": key, "sha256": digest, "duplicate": False}


# --- the owner's views ------------------------------------------------------------------------------------------------
async def list_recordings(user_id: str, *, limit: int = 50, offset: int = 0) -> list[dict]:
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        rows = await _fetch(db, "SELECT * FROM omi_recordings WHERE user_id = ? AND status != 'deleted' ORDER BY started_at DESC LIMIT ? OFFSET ?",
                            (user_id, max(1, min(int(limit), 200)), max(0, int(offset))))
    return [_public(r) for r in rows]


async def count_empty_transcripts(user_id: str) -> int:
    """How many of the member's recordings came back with NO text but whose audio is still held (the owner-visible half of the
    empty-transcript safety rule: the list shows the count, each row shows ``status: empty_transcript`` + ``has_audio``)."""
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        rows = await _fetch(db, "SELECT COUNT(*) AS n FROM omi_recordings WHERE user_id = ? AND status = 'empty_transcript' AND audio_path != ''",
                            (user_id,))
    return int(rows[0]["n"]) if rows else 0


async def get_recording(user_id: str, rid: str) -> Optional[dict]:
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        rows = await _fetch(db, "SELECT * FROM omi_recordings WHERE id = ? AND user_id = ? AND status != 'deleted'", (rid, user_id))
    return _public(rows[0], transcript=True) if rows else None


async def _tombstone(db, row: Any, now: datetime) -> None:
    _unlink(row["audio_path"] or "")
    await db.execute(
        "UPDATE omi_recordings SET status = 'deleted', transcript = '', audio_path = '', audio_bytes = 0, started_at = '', ended_at = '', "
        "duration_s = 0, keep_audio = 0, audio_expires_at = NULL, reviewed = 0, deleted_at = ? WHERE id = ?", (_iso(now), row["id"]))


async def delete_recording(user_id: str, rid: str) -> bool:
    """Delete the recording: the WAV, the transcript and the times go; a content-free tombstone stays so a Pi retry is acknowledged."""
    from db_compat import get_compat_db
    async with erase_gate.writing(user_id, erase_gate.generation(user_id)):
        async with get_compat_db() as db:
            rows = await _fetch(db, "SELECT id, audio_path, status FROM omi_recordings WHERE id = ? AND user_id = ?", (rid, user_id))
            if not rows or rows[0]["status"] == "deleted":
                return False
            await _tombstone(db, rows[0], _now())
            await db.commit()
    logger.info("OMI_DELETED id=%s", rid)
    return True


async def update_recording(user_id: str, rid: str, *, keep_audio: Optional[bool] = None, reviewed: Optional[bool] = None) -> Optional[dict]:
    from db_compat import get_compat_db
    sets, params = [], []
    if keep_audio is not None:
        sets.append("keep_audio = ?")
        params.append(1 if keep_audio else 0)
    if reviewed is not None:
        sets.append("reviewed = ?")
        params.append(1 if reviewed else 0)
    if not sets:
        return await get_recording(user_id, rid)
    async with get_compat_db() as db:
        cur = await db.execute(f"UPDATE omi_recordings SET {', '.join(sets)} WHERE id = ? AND user_id = ? AND status != 'deleted'",
                               tuple(params) + (rid, user_id))
        await db.commit()
        if not getattr(cur, "rowcount", 1):
            return None
        if keep_audio is False:       # un-keeping an already-transcribed (or empty-transcript) recording drops the WAV now - the owner's explicit call
            rows = await _fetch(db, "SELECT id, audio_path, status FROM omi_recordings WHERE id = ? AND user_id = ?", (rid, user_id))
            if rows and rows[0]["status"] in ("done", "empty_transcript") and rows[0]["audio_path"]:
                _unlink(rows[0]["audio_path"])
                await db.execute("UPDATE omi_recordings SET audio_path = '', audio_bytes = 0, audio_expires_at = NULL WHERE id = ?", (rid,))
                await db.commit()
    return await get_recording(user_id, rid)


async def forget_user(user_id: str) -> int:
    """Right to be forgotten: every recording of the member is deleted (audio, transcript, times) AND their pendant registrations are
    unassigned (``omi_devices.forget_user``; the device row stays, unowned, so a restart cannot re-seed it to them). Takes the member's
    erase gate. Returns the number of recordings deleted."""
    try:
        from db_compat import get_compat_db
        import omi_devices
        n = 0
        async with erase_gate.erasing(user_id):
            async with get_compat_db() as db:
                for r in await _fetch(db, "SELECT id, audio_path, status FROM omi_recordings WHERE user_id = ? AND status != 'deleted'", (user_id,)):
                    await _tombstone(db, r, _now())
                    n += 1
                await db.commit()
            try:        # AFTER the recordings are committed: a registry that is not migrated yet (0047) must never block erasing audio
                async with get_compat_db() as db:
                    await omi_devices.forget_user(db, user_id)
                    await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("omi_devices: pendant registrations NOT unassigned for a forgotten user (%s)", type(exc).__name__)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_recordings: recordings NOT erased for a forgotten user (%s)", type(exc).__name__)
        return 0


async def erase_entity(user_id: str, rx: "re.Pattern[str]", *, strict: bool = False) -> int:
    """Forget the name ``rx`` matches: it is replaced by ``[forgotten]`` in the member's transcripts. The audio still holds the spoken
    name, so a recording whose transcript named it loses its WAV too, kept or not. A failure returns 0 (WARNING); with ``strict`` it
    RAISES so the forget cascade can record it instead of reading as complete."""
    try:
        from db_compat import get_compat_db
        n = 0
        async with erase_gate.erasing(user_id, bump=False):
            async with get_compat_db() as db:
                for r in await _fetch(db, "SELECT id, transcript, audio_path FROM omi_recordings WHERE user_id = ? AND status = 'done'", (user_id,)):
                    text = r["transcript"] or ""
                    if not text or not rx.search(text):
                        continue
                    _unlink(r["audio_path"] or "")
                    await db.execute("UPDATE omi_recordings SET transcript = ?, audio_path = '', audio_bytes = 0, keep_audio = 0 WHERE id = ?",
                                     (rx.sub(_FORGOTTEN_MARK, text), r["id"]))
                    n += 1
                await db.commit()
        return n
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise
        logger.warning("omi_recordings: transcripts naming a forgotten entity NOT redacted (%s)", type(exc).__name__)
        return 0


async def _redact_forgotten(user_id: str, text: str) -> str:
    """A transcript lands AFTER a forget: redact it against the forget ledger so a forgotten name cannot come back through here."""
    if not text:
        return text
    try:
        import forget_match as fm
        import memory_forgotten as mf
        spans = await mf.spans(user_id, text)
        if spans:
            return fm.redact(text, spans)[0]
    except Exception as exc:  # noqa: BLE001 - the ledger being unreadable must not lose the transcript; erase_entity covers the live case
        logger.debug("omi_recordings: forget ledger not applied (%s)", type(exc).__name__)
    return text


# --- transcription ------------------------------------------------------------------------------------------------------
async def _transcribe_file(path: str) -> str:
    """The seam over the service's Moonshine (capture OFF: nothing enters the panel STT corpus, nothing is broadcast)."""
    from routers.voice_tts import _transcribe_audio
    return await _transcribe_audio(path, capture=False)


def _chunk_wav(pcm: bytes, tmpdir: str, idx: int) -> str:
    p = os.path.join(tmpdir, f"c{idx}.wav")
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return p


async def transcribe_wave(w: Any, *, gate=None, pause_s: float = 0.4, stats: Optional[dict] = None) -> str:
    """Moonshine over an open ``wave`` reader in ~20 s chunks cut at the quietest point, STREAMED (``iter_wave_chunks``). Silent chunks
    are skipped. ``gate()`` (sync or async) is asked before every chunk and aborts the job by raising ``GateClosed``; the caller leaves
    the recording pending. ``stats`` (optional) is filled with ``chunks`` (total) and ``voiced`` (sent to Moonshine, peak >= ``SILENCE_PEAK``)."""
    parts: list[str] = []
    st = stats if stats is not None else {}
    st["chunks"], st["voiced"] = 0, 0
    with tempfile.TemporaryDirectory(prefix="zoe-omi-") as tmp:
        for i, piece in enumerate(iter_wave_chunks(w)):
            st["chunks"] += 1
            if gate is not None:
                verdict = gate()
                if asyncio.iscoroutine(verdict):
                    verdict = await verdict
                ok, why = verdict
                if not ok:
                    raise GateClosed(why)
            if peak(piece) < SILENCE_PEAK:
                continue
            st["voiced"] += 1
            text = (await _transcribe_file(_chunk_wav(piece, tmp, i)) or "").strip()
            if text:
                parts.append(text)
            await asyncio.sleep(pause_s)           # a waiting voice turn gets the Moonshine lock between chunks
    return " ".join(parts)


async def transcribe_audio_file(path: str, **kw: Any) -> str:
    """``transcribe_wave`` over a WAV on disk - the production path (the file is never read whole)."""
    with wave.open(path) as w:
        return await transcribe_wave(w, **kw)


async def transcribe_audio_bytes(wav: bytes, **kw: Any) -> str:
    """``transcribe_wave`` over WAV bytes already in memory (tests, short audio)."""
    with wave.open(io.BytesIO(wav)) as w:
        return await transcribe_wave(w, **kw)


async def _patient_gate(now: Optional[datetime]) -> tuple[bool, str]:
    """The per-chunk gate of a RUNNING job. A transient closure (a voice turn, RAM dipping under half the floor) is WAITED OUT for up to
    ``HOLD_MAX_S`` - throwing the job away would re-transcribe every earlier chunk from zero, which is what stalled a 600 s backlog when
    MemAvailable floated around the floor. A decision (night window, flag off) ends the job at once. Moonshine is not held while waiting."""
    deadline = time.monotonic() + HOLD_MAX_S
    while True:
        ok, why = may_transcribe_now(now, continuing=True)
        if ok or not is_transient(why) or time.monotonic() >= deadline:
            return ok, why
        await asyncio.sleep(HOLD_POLL_S)


async def backlog_count(user_id: Optional[str] = None) -> int:
    """Recordings waiting for a transcript (``pending``, not yet out of attempts). One member's, or everyone's when ``user_id`` is None."""
    from db_compat import get_compat_db
    sql, params = "SELECT COUNT(*) AS n FROM omi_recordings WHERE status = 'pending' AND attempts < ?", [MAX_ATTEMPTS]
    if user_id is not None:
        sql, params = sql + " AND user_id = ?", params + [user_id]
    async with get_compat_db() as db:
        rows = await _fetch(db, sql, tuple(params))
    return int(rows[0]["n"]) if rows else 0


async def transcription_status(user_id: str) -> dict:
    """What the owner's list shows about the transcriber: counts and a reason code, never content."""
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        rows = await _fetch(db, "SELECT COUNT(*) AS n FROM omi_recordings WHERE user_id = ? AND status = 'transcribing'", (user_id,))
    st = deferred_state()
    return {"backlog": await backlog_count(user_id), "transcribing": int(rows[0]["n"]) if rows else 0, **st}


async def transcribe_pending(*, limit: int = 1, now: Optional[datetime] = None, pause_s: float = 0.4) -> int:
    """Claim and transcribe up to ``limit`` pending recordings (oldest first). Returns how many finished. Never raises."""
    done = 0
    try:
        from db_compat import get_compat_db
        stamp = _iso(now or _now())
        stale = _iso((now or _now()) - timedelta(seconds=STALE_CLAIM_S))
        async with get_compat_db() as db:
            await db.execute("UPDATE omi_recordings SET status = 'pending' WHERE status = 'transcribing' AND claimed_at < ?", (stale,))
            await db.commit()
        seen = erase_gate.snapshot()
        for _ in range(limit):
            ok, why = may_transcribe_now(now)
            if not ok:
                await note_deferred(why)
                break
            _clear_deferred()
            async with get_compat_db() as db:
                rows = await _fetch(db, "SELECT id, user_id, audio_path FROM omi_recordings WHERE status = 'pending' AND attempts < ? "
                                        "ORDER BY created_at LIMIT 1", (MAX_ATTEMPTS,))
                if not rows:
                    break
                r = rows[0]
                claim = await db.execute("UPDATE omi_recordings SET status = 'transcribing', claimed_at = ?, attempts = attempts + 1 "
                                         "WHERE id = ? AND status = 'pending'", (stamp, r["id"]))
                await db.commit()
                if not getattr(claim, "rowcount", 1):
                    continue
            if await _transcribe_one(r, seen, now, pause_s):
                done += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_recordings: transcription pass failed (%s)", type(exc).__name__)
    return done


async def _transcribe_one(r: Any, seen: dict, now: Optional[datetime], pause_s: float) -> bool:
    from db_compat import get_compat_db
    rid, uid, path = r["id"], r["user_id"], r["audio_path"]
    text, err = "", ""
    seen_chunks: dict = {}
    level: dict = {}
    try:
        text = await transcribe_audio_file(path, gate=lambda: _patient_gate(now), pause_s=pause_s, stats=seen_chunks)
        text = await _redact_forgotten(uid, text)
        if not text.strip():                    # diagnostics for the WARNING below; off the event loop (a 10-minute WAV is ~10M samples)
            level = await asyncio.to_thread(measure_wav_file, path)
    except GateClosed as exc:                   # the gate closed mid-job: not a failure, try again later
        err = f"deferred:{exc}"
        await note_deferred(str(exc))
    except Exception as exc:  # noqa: BLE001
        err = f"failed:{type(exc).__name__}"
    keep = False
    async with erase_gate.writing(uid, seen.get(uid, 0)) as live:
        async with get_compat_db() as db:
            if not live:                         # the member was erased since this was fetched: nothing of it survives
                rows = await _fetch(db, "SELECT id, audio_path, status FROM omi_recordings WHERE id = ?", (rid,))
                if rows and rows[0]["status"] != "deleted":
                    await _tombstone(db, rows[0], _now())
                    await db.commit()
                return False
            if err.startswith("deferred"):
                await db.execute("UPDATE omi_recordings SET status = 'pending', attempts = attempts - 1 WHERE id = ? AND status = 'transcribing'", (rid,))
                await db.commit()
                return False
            if err:
                logger.warning("omi_recordings: transcription of %s %s", rid, err)
                row = await _fetch(db, "SELECT attempts FROM omi_recordings WHERE id = ?", (rid,))
                final = bool(row) and int(row[0]["attempts"]) >= MAX_ATTEMPTS
                await db.execute("UPDATE omi_recordings SET status = ? WHERE id = ? AND status = 'transcribing'", ("failed" if final else "pending", rid))
                await db.commit()
                return False
            cur = await _fetch(db, "SELECT status, keep_audio FROM omi_recordings WHERE id = ?", (rid,))
            if not cur or cur[0]["status"] != "transcribing":      # deleted while we worked: write nothing
                return False
            keep = bool(cur[0]["keep_audio"])
            if not text.strip():
                # An EMPTY transcript is not a successful transcription: nothing was understood, so the audio is the only copy of
                # whatever the pendant heard. Keep the WAV (its retention expiry still bounds it) and say so loudly.
                await db.execute("UPDATE omi_recordings SET status = 'empty_transcript', transcript = '', transcribed_at = ? "
                                 "WHERE id = ? AND status = 'transcribing'", (_iso(_now()), rid))
                await db.commit()
                logger.warning("OMI_EMPTY_TRANSCRIPT id=%s seconds=%s peak=%s rms=%s clipped=%s rms_median=%s active_s=%s rate=%s "
                               "voiced_chunks=%s/%s reason=%s - WAV kept until retention",
                               rid, level.get("seconds"), level.get("peak"), level.get("rms"), level.get("clipped"),
                               level.get("rms_median"), level.get("active_s"), level.get("rate"),
                               seen_chunks.get("voiced"), seen_chunks.get("chunks"), empty_reason(seen_chunks, level))
                return True
            await db.execute("UPDATE omi_recordings SET status = 'done', transcript = ?, transcribed_at = ?"
                             + ("" if keep else ", audio_path = '', audio_bytes = 0, audio_expires_at = NULL")
                             + " WHERE id = ? AND status = 'transcribing'", (text, _iso(_now()), rid))
            await db.commit()
            if not keep:
                _unlink(path)
    logger.info("OMI_TRANSCRIBED id=%s chars=%d kept_audio=%s", rid, len(text), keep)
    return True


_REQUEUE_STATUSES = ("empty_transcript", "failed")


async def requeue_held(user_id: Optional[str] = None, *, rid: Optional[str] = None, now: Optional[datetime] = None) -> int:
    """Put held recordings back in the transcription queue: rows that ended ``empty_transcript`` or ``failed`` and whose WAV is
    still on disk go back to ``pending`` with ``attempts = 0``. ``user_id`` scopes it to one member (the owner routes); ``None``
    is every member (the one-shot after a pipeline change). ``rid`` narrows it to one recording. Returns how many were requeued.

    Nothing here extends the retention: a requeued recording still loses its WAV at ``audio_expires_at``, and a row whose retention
    has ALREADY passed is never requeued, even while its WAV still waits for ``purge_expired_audio`` - in the SELECT and again in the
    guarded UPDATE (the expiry is always set while a WAV is held - ``store_recording`` sets it, ``keep_audio`` does not clear it - so
    a NULL expiry is not "kept forever" and is not requeued either). A row whose file is gone is left as it is (nothing to
    transcribe). An erase landing meanwhile wins: the transcription pass re-checks the gate."""
    from db_compat import get_compat_db
    stamp = _iso(now or _now())
    sql = (f"SELECT id, audio_path FROM omi_recordings WHERE status IN ({', '.join('?' for _ in _REQUEUE_STATUSES)}) "
           "AND audio_path != '' AND audio_expires_at IS NOT NULL AND audio_expires_at > ?")
    params: list = [*_REQUEUE_STATUSES, stamp]
    if user_id is not None:
        sql += " AND user_id = ?"
        params.append(user_id)
    if rid is not None:
        sql += " AND id = ?"
        params.append(rid)
    n = 0
    async with get_compat_db() as db:
        for r in await _fetch(db, sql, tuple(params)):
            if not Path(r["audio_path"]).is_file():
                continue
            cur = await db.execute(
                f"UPDATE omi_recordings SET status = 'pending', attempts = 0, claimed_at = NULL, transcribed_at = NULL "
                f"WHERE id = ? AND status IN ({', '.join('?' for _ in _REQUEUE_STATUSES)}) "
                "AND audio_path != '' AND audio_expires_at IS NOT NULL AND audio_expires_at > ?", (r["id"], *_REQUEUE_STATUSES, stamp))
            n += 1 if getattr(cur, "rowcount", 1) else 0
        if n:
            await db.commit()
    if n:
        logger.info("OMI_REQUEUED count=%d scope=%s", n, "one" if rid else ("member" if user_id else "all"))
    return n


def _pipeline_marker() -> Path:
    return audio_dir() / ".transcribe-pipeline"


def _write_marker(marker: Path, value: Optional[str]) -> None:
    """Durably set (``value``) or clear (``None``) the pipeline marker: tmp + fsync + rename + fsync of the directory (the rename is
    not durable until its directory is - a crash could otherwise bring back the old marker and requeue a second time). Raises ``OSError``."""
    if value is None:
        marker.unlink(missing_ok=True)
        _fsync_dir(marker.parent)
        return
    marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = marker.with_suffix(".part")
    with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
        fh.write(value + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, marker)
    _fsync_dir(marker.parent)


async def requeue_after_pipeline_change() -> int:
    """AT MOST ONCE per ``TRANSCRIBE_PIPELINE`` value: requeue every held empty/failed recording so a transcription fix reaches the
    audio still inside its retention window.

    The marker is CLAIMED (durably written) BEFORE anything is requeued, so this can never loop: a marker that cannot be written
    requeues nothing (WARNING; the owner routes still work), and a recording that comes back empty again is not requeued again. If
    the requeue itself fails OR IS CANCELLED part-way, the previous marker is put back so the next loop pass requeues the rest (rows
    already requeued are ``pending``, so they are not picked twice) - and if even that fails, the cost is a missed one-shot (the owner
    can still requeue), never a repeat."""
    marker = _pipeline_marker()
    try:
        prev = marker.read_text().strip() if marker.is_file() else None
    except OSError:
        return 0
    if prev == TRANSCRIBE_PIPELINE:
        return 0
    try:
        _write_marker(marker, TRANSCRIBE_PIPELINE)
    except OSError as exc:
        logger.warning("omi_recordings: pipeline marker not written (%s) - held recordings NOT requeued", type(exc).__name__)
        return 0
    try:
        return await requeue_held(None)
    except BaseException:                 # a cancellation too (shutdown mid-requeue): put the claim back so the next pass finishes
        try:
            _write_marker(marker, prev)
        except OSError as exc:
            logger.warning("omi_recordings: pipeline marker not restored after a failed requeue (%s)", type(exc).__name__)
        raise


async def purge_expired_audio(*, now: Optional[datetime] = None) -> int:
    """Delete WAVs past ``audio_expires_at`` (kept ones included - the flag keeps audio for the retention window, not forever)."""
    n = 0
    try:
        from db_compat import get_compat_db
        stamp = _iso(now or _now())
        async with get_compat_db() as db:
            rows = await _fetch(db, "SELECT id, audio_path FROM omi_recordings WHERE audio_path != '' AND audio_expires_at IS NOT NULL AND audio_expires_at < ?", (stamp,))
            for r in rows:
                _unlink(r["audio_path"])
                await db.execute("UPDATE omi_recordings SET audio_path = '', audio_bytes = 0, audio_expires_at = NULL, keep_audio = 0 WHERE id = ?", (r["id"],))
                n += 1
            if n:
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_recordings: audio retention purge failed (%s)", type(exc).__name__)
    return n


async def run_loop() -> None:
    """The background task (started by ``main.lifespan`` only when the flag is on). One pass per ``POLL_S``; never raises."""
    logger.info("omi_recordings: transcription loop started")
    while True:
        await asyncio.sleep(POLL_S)
        try:
            if enabled():
                await purge_expired_audio()
                await requeue_after_pipeline_change()
                await transcribe_pending(limit=3)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("omi_recordings: loop pass failed (%s)", type(exc).__name__)


# --- the "I synced N" notice ---------------------------------------------------------------------------------------------------
def notice_text(count: int) -> tuple[str, str]:
    if count == 1:
        return "Pendant synced", "I've synced 1 recording from your pendant."
    return "Pendant synced", f"I've synced {count} recordings from your pendant."


async def notify_synced(user_id: str, count: int) -> bool:
    """One short bell notice (the existing ``notifications`` row + broadcast path). Never raises; losing it loses nothing."""
    if count <= 0 or not user_id:
        return False
    try:
        title, message = notice_text(min(int(count), 999))
        from db_pool import get_db_ctx
        from push import broadcaster
        nid = str(uuid.uuid4())
        async with get_db_ctx() as db:
            await db.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) "
                             "VALUES (?, ?, 'omi_sync', ?, ?, ?, 0, NOW())", (nid, user_id, title, message, json.dumps({"count": int(count)})))
            await db.commit()
        await broadcaster.broadcast("all", "notification_created", {"id": nid, "type": "omi_sync", "title": title, "message": message,
                                                                     "delivered": False}, user_id=user_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_recordings: sync notice failed (%s)", type(exc).__name__)
        return False
