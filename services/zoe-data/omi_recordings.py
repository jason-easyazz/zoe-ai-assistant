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
from typing import Any, Iterable, Iterator, Optional

import live_voice_gate
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


def keep_for_tagging() -> bool:
    """``ZOE_OMI_KEEP_AUDIO_FOR_TAGGING`` - default OFF. On: a transcribed recording whose voices were split keeps its WAV while ANY
    line still needs a name (someone else / not sure / mixed, not tagged, not marked not-a-person), so the member can play a few
    seconds of a voice on the Pendant page to tell who it is. The WAV goes the moment nothing in it needs a name
    (``release_tagged_audio``) and never outlives ``audio_expires_at`` (the ``ZOE_OMI_AUDIO_RETENTION_DAYS`` bound, default 7)."""
    return env_bool("ZOE_OMI_KEEP_AUDIO_FOR_TAGGING", default=False)


NEEDS_NAME_SPEAKERS = ("other", "unsure", "mixed")
CLIP_MAX_S = 8.0


def needs_name(seg: dict) -> bool:
    """A transcribed turn the member may still want to name (the in-memory twin of ``_NEEDS_NAME_SQL``)."""
    return (seg.get("speaker") in NEEDS_NAME_SPEAKERS and not seg.get("not_person")) and bool(str(seg.get("text") or "").strip())


_NEEDS_NAME_SQL = ("tagged_person_id IS NULL AND not_person IS NULL AND TRIM(text) != '' AND COALESCE(owner_label, speaker) IN ('other', "
                   "'unsure', 'mixed')")


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


def live_busy_reason() -> Optional[str]:
    """Seam over ``live_voice_gate.busy_reason``: live STT in flight, the voice-harness flock, the night-window unit, a cool-off."""
    return live_voice_gate.busy_reason()


# Gate reasons: the human string ``may_transcribe_now`` returns, and the stable code the log line / API use.
WHY_VOICE = "voice turn in flight"
WHY_NOT_IDLE = "panel not idle"
WHY_LIVE_PREFIX = "live voice: "       # + a ``live_voice_gate`` code (live_stt / harness / cooloff); night_unit maps to "night window"
WHY_MEM_PREFIX = "MemAvailable"


def reason_code(why: str) -> str:
    if why.startswith(WHY_MEM_PREFIX):
        return "low_memory"
    if why == WHY_VOICE:
        return "voice_active"
    if why == WHY_NOT_IDLE:
        return "panel_not_idle"
    if why.startswith(WHY_LIVE_PREFIX):
        return "voice_harness" if why.endswith("harness") else "voice_active"
    if why == "night window":
        return "night_window"
    if why == "flag off":
        return "flag_off"
    return "other"


def is_transient(why: str) -> bool:
    """A closure a RUNNING job should wait out (a voice turn ends, RAM comes back) rather than abort on. The flag and the night
    window are decisions, not weather: they end the job at once."""
    return reason_code(why) in ("low_memory", "voice_active", "voice_harness", "panel_not_idle")


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
    live = live_busy_reason()
    if live == "night_unit":
        return False, "night window"
    if live:
        return False, WHY_LIVE_PREFIX + live
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


QUIET_SKIP_RMS = ACTIVE_RMS // 3  # a 100 ms window at/above this RMS counts toward "something sustained happened" (pre-STT skip only)
QUIET_MIN_WINDOWS = 3             # a click/ringing spans at most 2 windows; a word is >= 3. Fewer sustained windows = nothing to transcribe


def quiet_skip_enabled() -> bool:
    """``ZOE_OMI_QUIET_SKIP`` (default on) - a chunk whose only loud part is a click (fewer than ``QUIET_MIN_WINDOWS`` 100 ms windows at
    or above ``QUIET_SKIP_RMS``) is not sent to Moonshine. The recording keeps its WAV when nothing at all came out (empty_transcript),
    so a wrong skip is recoverable inside the retention."""
    return env_bool("ZOE_OMI_QUIET_SKIP", True)


def sustained_windows(pcm: bytes) -> int:
    """Count of 100 ms windows at or above ``QUIET_SKIP_RMS``. Pure; call it off the event loop for a 20 s chunk."""
    a = _samples(pcm)
    win = SAMPLE_RATE // 10
    n = 0
    for i in range(0, len(a) - win + 1, win):
        part = a[i:i + win]
        if (sum(v * v for v in part) / win) ** 0.5 >= QUIET_SKIP_RMS:
            n += 1
    return n


CLICK_PEAK_SHARE = 0.5            # a click puts >= this share of the chunk's loud-window energy into ONE 10 ms slice; a voiced word spreads it


def is_click_only(pcm: bytes) -> bool:
    """True only for a lone sharp transient: fewer than ``QUIET_MIN_WINDOWS`` loud windows AND (when there is any loud window) the
    energy is peak-dominated - one 10 ms slice holds >= ``CLICK_PEAK_SHARE`` of it. A short voiced word ("no", ~200 ms) has a
    normal envelope, so it fails the second test and still reaches Moonshine. Pure; call it off the event loop."""
    a = _samples(pcm)
    win = SAMPLE_RATE // 10
    sl = SAMPLE_RATE // 100
    loud = 0
    total = 0.0
    top = 0.0
    for i in range(0, len(a) - win + 1, win):
        part = a[i:i + win]
        if (sum(v * v for v in part) / win) ** 0.5 >= QUIET_SKIP_RMS:
            loud += 1
            total += sum(v * v for v in part)
            top = max(top, max(sum(v * v for v in part[j:j + sl]) for j in range(0, win, sl)))
    if loud >= QUIET_MIN_WINDOWS:
        return False
    return loud == 0 or total <= 0 or top / total >= CLICK_PEAK_SHARE


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
    """One recording with its transcript and, once tagging has run on it, its speaker turns (``segments``, the member's own)."""
    from db_compat import get_compat_db
    import omi_voices
    async with get_compat_db() as db:
        rows = await _fetch(db, "SELECT * FROM omi_recordings WHERE id = ? AND user_id = ? AND status != 'deleted'", (rid, user_id))
        if not rows:
            return None
        out = _public(rows[0], transcript=True)
        try:
            out["segments"] = await omi_voices.segments_for(db, user_id, rid) if rows[0]["status"] == "done" else []
        except Exception as exc:  # noqa: BLE001 - pre-0055: no segments yet
            if not omi_voices._missing(exc):
                raise
            out["segments"] = []
    return out


async def _tombstone(db, row: Any, now: datetime) -> None:
    import omi_voices            # FIRST: its segments, held voice embeddings and voiceprint contributions go with it (voiceprints rebuilt).
    await omi_voices.drop_recording(db, row["id"])    # A failure here leaves the row NOT deleted, so the owner's retry runs it again
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
            import omi_day_review          # FIRST: the day review's quotes of this recording go too (a failure keeps the recording for a retry)
            await omi_day_review.forget_recording(user_id, rid)
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


class VoiceEraseIncomplete(RuntimeError):
    """A member's learned-voice rows could not be erased. Raised by ``forget_user`` so the erase never reads as complete."""


class HealthEraseIncomplete(VoiceEraseIncomplete):
    """A member's pendant-health rows could not be erased (anything but a missing table). Same fail-closed contract as the voice erase."""


async def forget_user(user_id: str) -> int:
    """Right to be forgotten: every recording of the member is deleted (audio, transcript, times) AND their pendant registrations are
    unassigned (``omi_devices.forget_user``; the device row stays, unowned, so a restart cannot re-seed it to them). Takes the member's
    erase gate. Returns the number of recordings deleted. FAILS CLOSED: a voice-erase (or recording) failure raises after the rest of
    the erase has run, so the caller cannot report the member forgotten."""
    try:
        from db_compat import get_compat_db
        import omi_devices
        n = 0
        voice_failure: Optional[BaseException] = None
        health_failure: Optional[BaseException] = None
        async with erase_gate.erasing(user_id):
            async with get_compat_db() as db:
                for r in await _fetch(db, "SELECT id, audio_path, status FROM omi_recordings WHERE user_id = ? AND status != 'deleted'", (user_id,)):
                    await _tombstone(db, r, _now())
                    n += 1
                await db.commit()
            try:        # the member's voice rows: a failure is LOUD (an erase that leaves biometrics behind is a P1)
                import omi_voices
                async with get_compat_db() as db:
                    await omi_voices.forget_user(db, user_id)
                    await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.error("OMI_VOICES_ERASE_INCOMPLETE user=%s reason=%s", user_id, type(exc).__name__)
                voice_failure = exc               # raised below, once the rest of the erase has run: success is never reported
            try:        # the member's pendant health rows: only a MISSING table (0056 not applied) is "nothing to erase"; anything else is LOUD
                import omi_health
                await omi_health.forget_user(user_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("OMI_HEALTH_ERASE_INCOMPLETE user=%s reason=%s", user_id, type(exc).__name__)
                health_failure = exc              # raised below, once the rest of the erase has run
            try:        # AFTER the recordings are committed: a registry that is not migrated yet (0047) must never block erasing audio
                async with get_compat_db() as db:
                    await omi_devices.forget_user(db, user_id)
                    await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("omi_devices: pendant registrations NOT unassigned for a forgotten user (%s)", type(exc).__name__)
        if voice_failure is not None:
            raise VoiceEraseIncomplete(type(voice_failure).__name__) from voice_failure
        if health_failure is not None:
            raise HealthEraseIncomplete(type(health_failure).__name__) from health_failure
        return n
    except VoiceEraseIncomplete:
        raise                                     # fail closed: the caller must retry the whole erase, not read this as done
    except Exception as exc:  # noqa: BLE001
        import omi_voices
        no_store = omi_voices._missing(exc) or (isinstance(exc, RuntimeError) and "not initialised" in str(exc))   # nothing to erase
        if not no_store:                          # a recording or its voice cleanup that failed is NOT "forgotten"
            logger.error("omi_recordings: recordings NOT erased for a forgotten user (%s)", type(exc).__name__)
            raise
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
                import omi_voices
                try:
                    n += await omi_voices.redact(db, user_id, rx, _FORGOTTEN_MARK)     # each speaker turn's text, too
                except Exception as exc:  # noqa: BLE001
                    if not omi_voices._missing(exc):
                        raise
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


async def transcribe_wave(w: Any, *, gate=None, pause_s: float = 0.4, stats: Optional[dict] = None,
                          spans: Optional[list] = None) -> str:
    """Moonshine over an open ``wave`` reader in ~20 s chunks cut at the quietest point, STREAMED (``iter_wave_chunks``). Silent chunks
    are skipped. ``gate()`` (sync or async) is asked before every chunk and aborts the job by raising ``GateClosed``; the caller leaves
    the recording pending. ``stats`` (optional) is filled with ``chunks`` (total) and ``voiced`` (sent to Moonshine, peak >= ``SILENCE_PEAK``).
    ``spans`` (optional) receives ``(start_ms, end_ms, text)`` per chunk - the untagged form of ``omi_segments``."""
    parts: list[str] = []
    st = stats if stats is not None else {}
    st.setdefault("chunks", 0)
    st.setdefault("voiced", 0)
    pos = 0
    with tempfile.TemporaryDirectory(prefix="zoe-omi-") as tmp:
        for i, piece in enumerate(iter_wave_chunks(w)):
            st["chunks"] += 1
            start_ms, pos = pos * 1000 // SAMPLE_RATE, pos + len(piece) // 2
            if gate is not None:
                verdict = gate()
                if asyncio.iscoroutine(verdict):
                    verdict = await verdict
                ok, why = verdict
                if not ok:
                    raise GateClosed(why)
            if peak(piece) < SILENCE_PEAK:
                if spans is not None:
                    spans.append((start_ms, pos * 1000 // SAMPLE_RATE, ""))
                continue
            if quiet_skip_enabled() and await asyncio.to_thread(is_click_only, piece):
                st["quiet_skipped"] = st.get("quiet_skipped", 0) + 1    # a lone click above the peak floor: Moonshine would say nothing
                if spans is not None:
                    spans.append((start_ms, pos * 1000 // SAMPLE_RATE, ""))
                continue
            st["voiced"] += 1
            text = (await _transcribe_file(_chunk_wav(piece, tmp, i)) or "").strip()
            if text:
                parts.append(text)
            if spans is not None:
                spans.append((start_ms, pos * 1000 // SAMPLE_RATE, text))
            await asyncio.sleep(pause_s)           # a waiting voice turn gets the Moonshine lock between chunks
    return " ".join(parts)


class _Span:
    """A ``wave`` reader limited to frames ``[start, end)`` - what ``iter_wave_chunks`` needs to transcribe ONE speaker turn."""

    def __init__(self, w: Any, start: int, end: int) -> None:
        self.w, self.left, self.n = w, max(0, end - start), max(0, end - start)
        w.setpos(start)

    def getframerate(self) -> int:
        return self.w.getframerate()

    def getnframes(self) -> int:
        return self.n

    def readframes(self, k: int) -> bytes:
        got = self.w.readframes(min(k, self.left)) if self.left > 0 else b""
        self.left -= len(got) // 2
        return got


async def transcribe_turns(path: str, turns: list, **kw: Any) -> list[str]:
    """Text ALIGNED to speaker turns (Moonshine has no word times): each turn ``(start_ms, end_ms)`` is transcribed on its own,
    split at its quietest points when longer than a chunk - the same model, the same gate and the same silence rule as
    ``transcribe_wave``. Returns one text per turn."""
    out = []
    with wave.open(path) as w:
        for start_ms, end_ms in turns:
            sub = _Span(w, start_ms * SAMPLE_RATE // 1000, end_ms * SAMPLE_RATE // 1000)
            out.append(await transcribe_wave(sub, **kw))
    return out


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


def _retention_passed(expires: Any, stamp: str) -> bool:
    """True when a WAV's retention has run out at ``stamp`` (equal counts as out: ``requeue_held`` needs ``expires > now`` too). A
    row with no expiry is not expired here - ``store_recording`` always sets one while a WAV is held, so NULL is a row with no audio
    to age."""
    return bool(expires) and str(expires) <= stamp


async def _expire_unclaimed(db, r: Any, stamp: str) -> bool:
    """A pending row whose retention passed before it was claimed (it expired between ``requeue_held`` and the claim, or waited out
    the deferral gates, or ran out between the read and the claim): never transcribed and never left pending. The row is marked
    ``failed`` by a GUARDED update (still pending AND the SQL itself says the retention has passed, so a live row is never touched),
    then its WAV goes and the path is cleared (``requeue_held`` needs a WAV and an unexpired retention, so nothing brings it back)."""
    cur = await db.execute("UPDATE omi_recordings SET status = 'failed' WHERE id = ? AND status = 'pending' "
                           "AND audio_expires_at IS NOT NULL AND audio_expires_at <= ?", (r["id"], stamp))
    await db.commit()
    if not getattr(cur, "rowcount", 1):
        return False                                     # not pending any more, or not expired after all: nothing marked
    path = r["audio_path"]
    _unlink(path)
    if not path or not Path(path).exists():             # a WAV that would not unlink keeps its path: the hourly purge retries it
        await db.execute("UPDATE omi_recordings SET audio_path = '', audio_bytes = 0, audio_expires_at = NULL, keep_audio = 0 "
                         "WHERE id = ? AND status = 'failed'", (r["id"],))
        await db.commit()
    logger.info("OMI_EXPIRED_AT_CLAIM id=%s - retention passed before transcription; WAV removed, row marked failed", r["id"])
    return True


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
            stamp = _iso(now or _now())         # CLAIM time: a transcription takes minutes, so a later row may have expired meanwhile
            async with get_compat_db() as db:
                r = None
                while r is None:       # an expired row is marked and the next one taken: it never spends the pass's quota
                    rows = await _fetch(db, "SELECT id, user_id, audio_path, audio_expires_at FROM omi_recordings WHERE status = 'pending' "
                                            "AND attempts < ? ORDER BY created_at LIMIT 1", (MAX_ATTEMPTS,))
                    if not rows:
                        break
                    stamp = _iso(now or _now())     # the clock AT this row's check and claim: the cleanup loop can run a while
                    if _retention_passed(rows[0]["audio_expires_at"], stamp):
                        if not await _expire_unclaimed(db, rows[0], stamp):
                            break                      # could not mark it: end this pass rather than spin on the same row
                        continue
                    r = rows[0]
                if r is None:
                    break
                # the retention is re-checked IN the claim: a row that expires between the read above and here is not claimed
                claim = await db.execute("UPDATE omi_recordings SET status = 'transcribing', claimed_at = ?, attempts = attempts + 1 "
                                         "WHERE id = ? AND status = 'pending' AND (audio_expires_at IS NULL OR audio_expires_at > ?)",
                                         (stamp, r["id"], stamp))
                await db.commit()
                if not getattr(claim, "rowcount", 1):
                    await _expire_unclaimed(db, r, stamp)       # lost the race with the clock: mark it now, not a pass later
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
    segs: Optional[list] = None            # the omi_segments rows (only while ZOE_OMI_SPEAKER_TAG is not off)
    spk_state, model_id = "unverified", None
    try:
        turns, spk_state, model_id = await _diarise(uid, path, now)
        if turns:
            texts = await transcribe_turns(path, [(t.start_ms, t.end_ms) for t in turns], gate=lambda: _patient_gate(now),
                                           pause_s=pause_s, stats=seen_chunks)
            segs = [{"start_ms": t.start_ms, "end_ms": t.end_ms, "speaker": t.speaker, "score": t.score, "windows": t.windows,
                     "embedding": t.embedding, "text": await _redact_forgotten(uid, x)} for t, x in zip(turns, texts)]
            text = " ".join(x["text"] for x in segs if x["text"].strip())
        else:
            spans: Optional[list] = [] if _tag_mode() != "off" else None
            text = await transcribe_audio_file(path, gate=lambda: _patient_gate(now), pause_s=pause_s, stats=seen_chunks, spans=spans)
            text = await _redact_forgotten(uid, text)
            if spans is not None:
                segs = [{"start_ms": a, "end_ms": b, "speaker": "unverified", "score": None, "windows": 0, "embedding": None,
                         "text": await _redact_forgotten(uid, x)} for a, b, x in spans]
        if not text.strip():                    # diagnostics for the WARNING below; off the event loop (a 10-minute WAV is ~10M samples)
            level = await asyncio.to_thread(measure_wav_file, path)
    except GateClosed as exc:                   # the gate closed mid-job: not a failure, try again later
        err = f"deferred:{exc}"
        await note_deferred(str(exc))
    except Exception as exc:  # noqa: BLE001
        err = f"failed:{type(exc).__name__}"
    keep = hold = False
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
            if seen_chunks.get("quiet_skipped"):
                # a chunk was skipped before STT: other chunks' text must not make the recording look complete and delete the only
                # copy of audio nobody transcribed. Held until its retention expiry (audio_expires_at), like any kept recording.
                keep = True
                await db.execute("UPDATE omi_recordings SET keep_audio = 1 WHERE id = ?", (rid,))
            if not text.strip():
                # An EMPTY transcript is not a successful transcription: nothing was understood, so the audio is the only copy of
                # whatever the pendant heard. Keep the WAV (its retention expiry still bounds it) and say so loudly.
                await db.execute("UPDATE omi_recordings SET status = 'empty_transcript', transcript = '', transcribed_at = ? "
                                 "WHERE id = ? AND status = 'transcribing'", (_iso(_now()), rid))
                await db.commit()
                logger.warning("OMI_EMPTY_TRANSCRIPT id=%s seconds=%s peak=%s rms=%s clipped=%s rms_median=%s active_s=%s rate=%s "
                               "voiced_chunks=%s/%s quiet_skipped=%s reason=%s - WAV kept until retention",
                               rid, level.get("seconds"), level.get("peak"), level.get("rms"), level.get("clipped"),
                               level.get("rms_median"), level.get("active_s"), level.get("rate"),
                               seen_chunks.get("voiced"), seen_chunks.get("chunks"), seen_chunks.get("quiet_skipped", 0), empty_reason(seen_chunks, level))
                return True
            seg_ids: list = []
            if segs is not None:              # segments land BEFORE the row says done, and the WAV goes only after both
                import omi_voices
                try:
                    omi_voices.mark_overlaps(segs, await omi_voices.zoe_spoken_spans(db, uid, rid))  # Zoe's own replies: not a person
                    seg_ids = await omi_voices.store_segments(db, uid, rid, segs, model_id=model_id,
                                                              channel="panel" if spk_state == "tagged" else None)
                except Exception as exc:  # noqa: BLE001 - a segment-store failure never costs the transcript
                    logger.warning("OMI_SEGMENTS_NOT_STORED id=%s reason=%s - transcript kept untagged", rid, type(exc).__name__)
                    seg_ids, spk_state, segs = [], "untaggable", None
                    try:
                        await omi_voices.drop_recording(db, rid)
                    except Exception:  # noqa: BLE001
                        pass
            # held for tagging: only while a line needs a name, never past audio_expires_at (release_tagged_audio / purge_expired_audio)
            hold = keep or (keep_for_tagging() and bool(seg_ids) and spk_state == "tagged" and any(needs_name(x) for x in segs or []))
            await db.execute("UPDATE omi_recordings SET status = 'done', transcript = ?, transcribed_at = ?, speaker_state = ?"
                             + ("" if hold else ", audio_path = '', audio_bytes = 0, audio_expires_at = NULL")
                             + " WHERE id = ? AND status = 'transcribing'", (text, _iso(_now()), spk_state, rid))
            await db.commit()
            if not hold:
                _unlink(path)
            if seg_ids and spk_state == "tagged":
                try:                          # learning is optional; the transcript and its segments are already safe
                    import omi_diarize
                    learned = await omi_voices.learn(db, uid, seg_ids, segs, model_id=model_id, you_min=omi_diarize.thresholds()[0])
                    if learned["stored"]:
                        logger.info("OMI_VOICES_LEARNED id=%s stored=%d suggested=%d", rid, learned["stored"], learned["suggested"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("omi_voices: learning pass failed for %s (%s)", rid, type(exc).__name__)
    if hold and not keep:
        # learn() may just have marked a line not-a-person (it matched a voice the member called TV / Zoe): if nothing in the
        # recording needs a name any more, the WAV goes now, not at the next hourly pass
        await release_tagged_audio(uid)
    logger.info("OMI_TRANSCRIBED id=%s chars=%d kept_audio=%s segments=%d speaker_state=%s", rid, len(text), keep,
                len(segs or []), spk_state)
    return True


def _tag_mode() -> str:
    import omi_diarize
    return omi_diarize.mode()


async def _diarise(uid: str, path: str, now: Optional[datetime]) -> tuple[Optional[list], str, Optional[str]]:
    """Speaker turns for the recording, or ``(None, state, model)`` for the untagged path. ``unverified``: tagging is off, the
    wearer is not positively an adult (D14: no tagging of a child's pendant) or has no profile in this model (turns are still cut
    by voice, labelled ``unverified``); ``untaggable``: the model refused or the pass failed (the transcript is never lost to it).
    A closing gate is NOT a failure: ``GateClosed`` propagates and the recording goes back to pending."""
    if _tag_mode() == "off":
        return None, "unverified", None
    import manner_block
    import omi_diarize
    import omi_voices
    if not await manner_block.eligible(uid):
        return None, "unverified", None
    embedder = await asyncio.to_thread(omi_diarize.load_embedder)
    if embedder is None:
        return None, "untaggable", None
    mid = omi_diarize.model_id()
    try:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            profile = await omi_voices.wearer_profile(db, uid, mid)
        turns = await omi_diarize.diarise(path, embedder, profile, gate=lambda: _patient_gate(now))
        return turns, ("tagged" if profile is not None else "unverified"), mid
    except GateClosed:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("OMI_TAG_FAILED reason=%s - transcribing untagged", type(exc).__name__)
        return None, "untaggable", None
    finally:
        del embedder                          # the session is released after every pass (RAM is the scarce resource)


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


async def release_tagged_audio(user_id: Optional[str] = None) -> int:
    """Delete the WAV of every transcribed recording held ONLY for tagging (``keep_audio`` off) in which no line needs a name any
    more - everything tagged, called "me", or marked not a person - or whose voices were never split. ``user_id`` scopes it to one
    member (after each of their tags); None is everyone (the retention loop). Also the backstop when the flag is turned off: a held
    WAV is released at once rather than waiting for its retention. Returns how many WAVs went. Never raises."""
    n = 0
    try:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            sql = "SELECT id, user_id, audio_path FROM omi_recordings WHERE status = 'done' AND keep_audio = 0 AND audio_path != ''"
            rows = await _fetch(db, sql + (" AND user_id = ?" if user_id else ""), (user_id,) if user_id else ())
            for r in rows:
                if keep_for_tagging():
                    try:
                        left = await _fetch(db, f"SELECT COUNT(*) AS n FROM omi_segments WHERE recording_id = ? AND user_id = ? AND {_NEEDS_NAME_SQL}",
                                            (r["id"], r["user_id"]))
                    except Exception as exc:  # noqa: BLE001 - pre-0055: no segments, nothing to name
                        import omi_voices
                        if not omi_voices._missing(exc):
                            raise
                        left = []
                    if left and int(left[0]["n"]):
                        continue
                # the row first, re-checking keep_audio: a "keep this" that lands meanwhile wins and the WAV stays
                cur = await db.execute("UPDATE omi_recordings SET audio_path = '', audio_bytes = 0, audio_expires_at = NULL WHERE id = ? "
                                       "AND keep_audio = 0 AND audio_path = ?", (r["id"], r["audio_path"]))
                if getattr(cur, "rowcount", 1):
                    _unlink(r["audio_path"])
                    n += 1
            if n:
                await db.commit()
                logger.info("OMI_TAGGING_AUDIO_RELEASED count=%d", n)
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_recordings: tagging-audio release failed (%s)", type(exc).__name__)
    return n


#: when ``segment_clip`` serves a line's audio: its recording is done, still holds a WAV, and is inside retention (past retention a
#: clip is never served, even before the hourly purge runs). ONE predicate for the clip and for ``segment_lines``' ``audio_kept``.
_CLIP_HELD = "r.status = 'done' AND r.audio_path != '' AND (r.audio_expires_at IS NULL OR r.audio_expires_at > ?)"


async def segment_lines(user_id: str, segment_ids: Iterable[str]) -> dict[str, dict]:
    """``{segment_id: {"offset_ms", "audio_kept"}}`` for the member's OWN lines among ``segment_ids`` (anyone else's id = absent):
    where the line starts in its recording, and whether ``segment_clip`` would play it now (the same ``_CLIP_HELD`` predicate). For
    the day review's quotes on the Pendant page; never the audio path. Fail-soft: an unreadable store = {}."""
    ids = sorted({str(s) for s in segment_ids if s})[:200]
    if not ids:
        return {}
    from db_compat import get_compat_db
    base = ("FROM omi_segments s JOIN omi_recordings r ON r.id = s.recording_id WHERE s.user_id = ? AND r.user_id = ? "
            "AND s.id IN (%s)" % ",".join("?" * len(ids)))
    try:
        async with get_compat_db() as db:
            rows = await _fetch(db, "SELECT s.id, s.start_ms " + base, (user_id, user_id, *ids))
            held = {r["id"] for r in await _fetch(db, f"SELECT s.id {base} AND {_CLIP_HELD}", (user_id, user_id, *ids, _iso(_now())))}
    except Exception as exc:  # noqa: BLE001
        logger.debug("omi_recordings: segment_lines unavailable (%s)", type(exc).__name__)
        return {}
    return {r["id"]: {"offset_ms": max(0, int(r["start_ms"] or 0)), "audio_kept": r["id"] in held} for r in rows}


async def segment_clip(user_id: str, segment_id: str, *, max_s: float = CLIP_MAX_S) -> Optional[bytes]:
    """A short WAV (at most ``max_s``, from the start of the turn) of one of the member's OWN lines, cut from its recording's
    still-held WAV; None when the line is not theirs, its recording is not done, or no audio is held. The file must sit inside
    ``audio_dir()`` (a row can never point the route at another path)."""
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        rows = await _fetch(db, "SELECT s.start_ms, s.end_ms, r.audio_path FROM omi_segments s JOIN omi_recordings r ON r.id = s.recording_id "
                                f"WHERE s.id = ? AND s.user_id = ? AND r.user_id = ? AND {_CLIP_HELD}",
                            (segment_id, user_id, user_id, _iso(_now())))
    if not rows:
        return None
    path = Path(rows[0]["audio_path"])
    try:
        path.resolve().relative_to(audio_dir().resolve())
    except (ValueError, OSError):
        logger.warning("OMI_CLIP_REFUSED reason=outside_audio_dir")
        return None
    start = max(0, int(rows[0]["start_ms"]))
    end = max(start, min(int(rows[0]["end_ms"]), start + int(max_s * 1000)))

    def _cut() -> Optional[bytes]:
        try:
            with wave.open(str(path)) as w:
                rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
                w.setpos(min(w.getnframes(), start * rate // 1000))
                pcm = w.readframes(max(0, (end - start) * rate // 1000))
        except (OSError, EOFError, wave.Error):
            return None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as o:
            o.setnchannels(ch)
            o.setsampwidth(width)
            o.setframerate(rate)
            o.writeframes(pcm)
        return buf.getvalue()
    return await asyncio.to_thread(_cut)


async def _sweep_voices() -> None:
    """Learned-voice expiry runs whether or not learning is on (design 2.6): turning the flag off must not stop things expiring."""
    try:
        import omi_voices
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            await omi_voices.sweep(db)
    except Exception as exc:  # noqa: BLE001
        import omi_voices
        if not omi_voices._missing(exc):                     # pre-0055: nothing to expire yet
            logger.warning("omi_voices: expiry sweep failed (%s)", type(exc).__name__)


RETENTION_S = 3600.0


async def run_retention_loop() -> None:
    """Expiry that must NOT depend on the sync flag (started by ``main.lifespan`` always): unconfirmed voiceprints, held voice
    embeddings and unknown voices expire on their dates and WAVs go at their retention, also after an operator turns
    ``ZOE_OMI_SYNC_ENABLED`` off with learned data and audio still on disk. A missing table is nothing to expire. Never raises."""
    while True:
        await asyncio.sleep(RETENTION_S)
        try:
            await purge_expired_audio()
            await release_tagged_audio()
            await _sweep_voices()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("omi_recordings: retention pass failed (%s)", type(exc).__name__)


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
