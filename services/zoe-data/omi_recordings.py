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
  the WAV is deleted right after a successful transcription unless the owner kept it; ``purge_expired_audio`` bounds the rest.

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
from typing import Any, Optional

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
MAX_ATTEMPTS = 3
STALE_CLAIM_S = 30 * 60
POLL_S = 60.0
VOICE_BUSY_S = 45.0
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
    """``ZOE_OMI_OWNER_USER_ID`` - whose pendant it is. Unset = uploads are refused (503): a device token authenticates the
    DEVICE, never the person, so the owner is named by configuration, not by whoever happens to be bound to the panel."""
    return env_str("ZOE_OMI_OWNER_USER_ID", "").strip()


def audio_dir() -> Path:
    raw = env_str("ZOE_OMI_AUDIO_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "omi-recordings"


def retention_days() -> int:
    return max(1, env_int("ZOE_OMI_AUDIO_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))


def keep_audio_default() -> bool:
    """``ZOE_OMI_KEEP_AUDIO`` - keep every WAV (until its retention expiry) after transcription. Default OFF."""
    return env_bool("ZOE_OMI_KEEP_AUDIO", default=False)


def min_mem_mb() -> int:
    return max(0, env_int("ZOE_OMI_MIN_MEM_MB", 1500))


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


def voice_busy(now_mono: Optional[float] = None) -> bool:
    """A panel voice turn happened in the last ``VOICE_BUSY_S`` seconds. Reads the voice router's session table WITHOUT importing it."""
    mod = sys.modules.get("routers.voice_tts")
    sessions = getattr(mod, "_VOICE_SESSIONS", None) if mod else None
    if not sessions:
        return False
    t = time.monotonic() if now_mono is None else now_mono
    try:
        return any((t - float(e.get("last_at", -1e9))) < VOICE_BUSY_S for e in list(sessions.values()))
    except Exception:  # noqa: BLE001
        return False


def may_transcribe_now(now: Optional[datetime] = None) -> tuple[bool, str]:
    if not enabled():
        return False, "flag off"
    if night_window(now):
        return False, "night window"
    if voice_busy():
        return False, "voice turn in flight"
    mem = mem_available_mb()
    if mem is not None and mem < min_mem_mb():
        return False, f"MemAvailable {mem:.0f} MB < {min_mem_mb()} MB"
    return True, ""


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


def chunk_pcm(pcm: bytes, *, rate: int = SAMPLE_RATE, target_s: float = CHUNK_S, search_s: float = SPLIT_SEARCH_S,
              win_ms: int = 100) -> list[tuple[int, int]]:
    """Cut PCM16 into ``(start_byte, end_byte)`` chunks of about ``target_s``, each cut placed at the QUIETEST 100 ms window within the
    last ``search_s`` of the chunk so a word is not sliced in half. Contiguous, covering, never empty."""
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
            lo = max(pos + win, end - search)
            best, best_e = end, None
            i = lo
            while i + win <= end:
                e = sum(abs(v) for v in a[i:i + win])
                if best_e is None or e < best_e:
                    best, best_e = i + win // 2, e
                i += win
            end = best
        out.append((pos * 2, end * 2))
        pos = end
    return out


def peak(pcm: bytes) -> int:
    a = _samples(pcm)
    return max(map(abs, a)) if len(a) else 0


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
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
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
                          time_quality: str, gap_before: int, wav: bytes, scheduled_at: Optional[int] = None) -> dict:
    """Store one synced recording and return the confirmation the Pi needs. Idempotent by ``(user, device, key)``.

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
        if keep_audio is False:       # un-keeping an already-transcribed recording drops the WAV now
            rows = await _fetch(db, "SELECT id, audio_path, status FROM omi_recordings WHERE id = ? AND user_id = ?", (rid, user_id))
            if rows and rows[0]["status"] == "done" and rows[0]["audio_path"]:
                _unlink(rows[0]["audio_path"])
                await db.execute("UPDATE omi_recordings SET audio_path = '', audio_bytes = 0, audio_expires_at = NULL WHERE id = ?", (rid,))
                await db.commit()
    return await get_recording(user_id, rid)


async def forget_user(user_id: str) -> int:
    """Right to be forgotten: every recording of the member is deleted (audio, transcript, times). Takes the member's erase gate."""
    try:
        from db_compat import get_compat_db
        n = 0
        async with erase_gate.erasing(user_id):
            async with get_compat_db() as db:
                for r in await _fetch(db, "SELECT id, audio_path, status FROM omi_recordings WHERE user_id = ? AND status != 'deleted'", (user_id,)):
                    await _tombstone(db, r, _now())
                    n += 1
                await db.commit()
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


async def transcribe_audio_bytes(wav: bytes, *, gate=None, pause_s: float = 0.4) -> str:
    """Moonshine over a long WAV in ~20 s chunks cut at the quietest point. Silent chunks are skipped. ``gate()`` is asked before every
    chunk (night window / voice turn / RAM) and aborts the job by raising ``GateClosed``; the caller leaves the recording pending."""
    with wave.open(io.BytesIO(wav)) as w:
        pcm = w.readframes(w.getnframes())
    parts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="zoe-omi-") as tmp:
        for i, (a, b) in enumerate(chunk_pcm(pcm)):
            if gate is not None:
                ok, why = gate()
                if not ok:
                    raise GateClosed(why)
            piece = pcm[a:b]
            if peak(piece) < SILENCE_PEAK:
                continue
            text = (await _transcribe_file(_chunk_wav(piece, tmp, i)) or "").strip()
            if text:
                parts.append(text)
            await asyncio.sleep(pause_s)           # a waiting voice turn gets the Moonshine lock between chunks
    return " ".join(parts)


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
                logger.debug("omi_recordings: transcription deferred (%s)", why)
                break
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
    try:
        text = await transcribe_audio_bytes(Path(path).read_bytes(), gate=lambda: may_transcribe_now(now), pause_s=pause_s)
        text = await _redact_forgotten(uid, text)
    except GateClosed as exc:                   # the gate closed mid-job: not a failure, try again later
        err = f"deferred:{exc}"
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
            await db.execute("UPDATE omi_recordings SET status = 'done', transcript = ?, transcribed_at = ?"
                             + ("" if keep else ", audio_path = '', audio_bytes = 0, audio_expires_at = NULL")
                             + " WHERE id = ? AND status = 'transcribing'", (text, _iso(_now()), rid))
            await db.commit()
            if not keep:
                _unlink(path)
    logger.info("OMI_TRANSCRIBED id=%s chars=%d kept_audio=%s", rid, len(text), keep)
    return True


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
