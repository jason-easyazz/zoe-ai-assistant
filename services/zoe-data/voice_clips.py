"""The panel clip corpus: who each saved clip was, and how long a non-member's clip is kept (named guests, spec items 6 and 9).

``ZOE_VOICE_SAVE_AUDIO`` saves EVERY panel turn to ``~/.zoe-voice-samples``, the OWNER's real-voice regression set; a guest's turn used to
land in it unlabelled, skewing every owner-voice metric and living on forever. This is the missing label and the missing retention.

SIDECAR  ``<clip>.verdict.json`` (flag ``ZOE_VOICE_CLIP_VERDICT``): ``{"v", "kind": member|guest|unknown, "id", "cluster", "ts"}``, written when
         the server has judged the turn. ``cluster`` is the unknown voice (``panel_voice_clusters``, #2343) the turn was filed under, which is
         how a member later names the clip: a pointer to the EXISTING registry, never a second one. NO sidecar = a legacy clip, treated
         exactly as before (history is never relabelled by guessing).
READERS  ``scripts/lib/panel_voice_log.owner_voice_clips``: owner-voice metrics keep member + legacy clips. Said-vs-did replay keeps all.
SWEEP    ``ZOE_VOICE_CLIP_RETENTION`` (off): guest / unknown clips older than ``ZOE_VOICE_CLIP_RETENTION_DAYS`` (30, owner 2026-11-08) are
         deleted with their sidecar; NEVER a member or legacy clip. Counts only in the log. Runbook: ``docs/knowledge/named-guests.md``.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from typed_env import env_bool, env_int, env_str

logger = logging.getLogger(__name__)

SUFFIX = ".verdict.json"
KINDS = ("member", "guest", "unknown")
NON_MEMBER = ("guest", "unknown")
SWEEP_EVERY_S = 6 * 3600

# The clip THIS transcription saved (a fresh holder per transcription, like ``_stt_raw_var``: never a previous turn's clip).
_HOLDER: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("voice_clip_holder", default=None)
#: one writer of the pending-drop list at a time (zoe-data is ONE process; both the append and the retry run in worker threads, never
#: on the event loop): held only for a read, an append or a rewrite of that small file, never across the retry's clip removals
_PENDING_LOCK = threading.Lock()


def verdict_enabled() -> bool:
    return env_bool("ZOE_VOICE_CLIP_VERDICT", default=False)


def retention_enabled() -> bool:
    return env_bool("ZOE_VOICE_CLIP_RETENTION", default=False)


def retention_days() -> int:
    return max(1, env_int("ZOE_VOICE_CLIP_RETENTION_DAYS", 30))


def sample_dir() -> str:
    return env_str("ZOE_VOICE_SAMPLE_DIR", "/home/zoe/.zoe-voice-samples") or "/home/zoe/.zoe-voice-samples"


def sidecar_path(wav: str) -> str:
    return str(wav)[: -len(".wav")] + SUFFIX if str(wav).endswith(".wav") else str(wav) + SUFFIX


def begin() -> None:
    """A new transcription starts: forget the previous turn's clip."""
    _HOLDER.set({})


def note_saved(path: str) -> None:
    h = _HOLDER.get()
    if h is not None:
        h["path"] = str(path)


def current_clip() -> Optional[str]:
    return (_HOLDER.get() or {}).get("path")


def drop_current(member: str = "") -> bool:
    """Remove the clip THIS turn saved (a member delete landed during the turn: the clip is neither kept for the deleted member nor
    kept unattributed). The clip is first LABELLED as ``member``'s (when given and not labelled yet), then removed WAV FIRST and the
    label LAST (``_remove_strict``): a removal that fails RAISES and leaves the clip labelled as theirs, so any re-run of their erase
    (``erase_member``) still finds it. True when there was a clip; "already gone" is fine."""
    clip = current_clip()
    if not clip:
        return False
    drop_clip(clip, member)
    _HOLDER.set({})
    return True


def clear_current() -> None:
    """Forget the clip this turn saved (after ``drop_clip`` ran it off the event loop)."""
    _HOLDER.set({})


def drop_clip(clip: str, member: str = "") -> None:
    """``drop_current``'s file work on a GIVEN clip path, with no context state: blocking (writes and unlinks), so a voice turn runs it in
    a worker thread (``asyncio.to_thread``) and never on the event loop. RAISES as ``drop_current`` documents."""
    side = sidecar_path(clip)
    # the label says the clip is the member's AND that it must go (``pending_drop``): if the removal below fails, that marker on disk
    # is the durable retry (``retry_pending_drops``), even after the member's delete completed; a clip saved later is never marked
    marked = True
    try:
        _write(side, dict(v=1, kind="member", id=str(member or "")[:64], cluster="", ts=int(time.time()), pending_drop=1))
    except OSError as exc:      # no marker (a full disk, an unwritable corpus): the removal is still tried - it may well succeed
        marked = False
        logger.error("VOICE_CLIP_DROP_MARK_FAILED reason=%s", type(exc).__name__)
    try:
        _remove_strict(side, clip)
    except OSError:
        if not marked:          # neither a marker nor a removal: the clip's PATH goes into the pending-drop list instead
            try:
                _note_pending(clip)
            except OSError as exc:
                logger.error("VOICE_CLIP_DROP_UNRECORDED reason=%s", type(exc).__name__)
        raise


def pending_file() -> str:
    """The fallback retry record of a clip drop that could neither mark nor remove its clip: one clip PATH per line (a capture-time
    file name, no member id, no words), 0600. Read and pruned by ``retry_pending_drops``."""
    return env_str("ZOE_VOICE_CLIP_PENDING_FILE", "") or os.path.expanduser("~/.zoe/voice-clip-pending-drops.txt")


def _note_pending(clip: str) -> None:
    path = pending_file()
    with _PENDING_LOCK:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(str(clip).replace("\n", "") + "\n")


def _write(path: str, body: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".verdict-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(body, f, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record(kind: str, ident: str = "", cluster: str = "") -> bool:
    """Label the clip this turn saved with the server's verdict. False when the flag is off, no clip was saved or kind is invalid.
    Never raises (a label is never worth a turn)."""
    try:
        clip = current_clip()
        if not verdict_enabled() or not clip or kind not in KINDS or not os.path.isfile(clip):
            return False
        _write(sidecar_path(clip), dict(v=1, kind=kind, id=str(ident or "")[:64], cluster=str(cluster or "")[:64], ts=int(time.time())))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice_clips: verdict not recorded (%s)", type(exc).__name__)
        return False


def read(wav: str) -> Optional[dict]:
    """The clip's verdict, or None (legacy / unreadable / unknown kind): the caller then treats it as it always did."""
    try:
        with open(sidecar_path(wav)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("kind") in KINDS else None
    except (OSError, ValueError):
        return None


def _labelled(d: str):
    """``(sidecar path, clip path, meta)`` for every readable sidecar in the corpus (the one place sidecars are listed)."""
    try:
        names = [e for e in os.listdir(d) if e.endswith(SUFFIX) and not e.startswith(".")]
    except OSError:
        return
    for name in sorted(names):
        side = os.path.join(d, name)
        try:
            meta = json.loads(Path(side).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            yield side, os.path.join(d, name[: -len(SUFFIX)] + ".wav"), meta


def _remove(side: str, wav: str) -> bool:
    """LENIENT removal (retention sweep, guest forget): the WAV first, the label only once the WAV is gone - a WAV that cannot be
    removed keeps its label, so the next sweep / forget finds it again (``isfile`` was not used: it reads an unreadable WAV as absent
    and dropped the label of a clip that stayed). A failure is logged and returns False."""
    try:
        try:
            os.unlink(wav)
        except FileNotFoundError:
            pass
        os.unlink(side)
        return True
    except OSError as exc:
        logger.warning("voice_clips: could not remove a clip (%s)", type(exc).__name__)
        return False


def _labelled_strict(d: str, ident: str):
    """The ERASE enumeration: like ``_labelled`` but nothing is swallowed. A corpus directory that does not exist holds nothing; one
    that cannot be listed RAISES, and so does a sidecar that cannot be read (a permission or I/O failure must fail the member delete,
    which is then retried, never read as done). A sidecar that is read but is not a verdict object (corrupt JSON, a torn write) yields
    ``meta = None`` with ``named`` = whether its raw text carries ``ident`` as a JSON string: such a label may be the member's, so the
    erase removes it only when it can name them (the clip behind a label that cannot name them is not theirs to delete here)."""
    try:
        names = [e for e in os.listdir(d) if e.endswith(SUFFIX) and not e.startswith(".")]
    except FileNotFoundError:
        return
    for name in sorted(names):
        side = os.path.join(d, name)
        try:
            raw = Path(side).read_bytes().decode("utf-8", errors="replace")
        except FileNotFoundError:
            continue                                    # removed meanwhile (a sweep): nothing left to erase
        wav = os.path.join(d, name[: -len(SUFFIX)] + ".wav")
        try:
            meta = json.loads(raw)
        except ValueError:
            meta = None
        if isinstance(meta, dict):
            yield side, wav, meta, False
        else:
            yield side, wav, None, json.dumps(ident) in raw


def _remove_strict(side: str, wav: str) -> None:
    """Remove a clip and then its sidecar; RAISES on any failure but "already gone" (the label goes last, so a retry still finds it)."""
    try:
        os.unlink(wav)
    except FileNotFoundError:
        pass
    try:
        os.unlink(side)
    except FileNotFoundError:
        pass


def sweep(directory: Optional[str] = None, *, now: Optional[float] = None, days: Optional[int] = None) -> dict:
    """Delete guest / unknown clips older than the retention (and their sidecars). Counts only. Member and legacy clips (no sidecar) are
    never touched."""
    cutoff = (now if now is not None else time.time()) - 86400 * (days if days is not None else retention_days())
    removed = kept = 0
    for side, wav, meta in _labelled(directory or sample_dir()):
        if meta.get("kind") not in NON_MEMBER:
            continue                                   # member (or an unknown kind): never deleted
        ts = meta.get("ts")
        if not isinstance(ts, (int, float)) or ts > cutoff:
            kept += 1
        elif _remove(side, wav):
            removed += 1
    if removed:
        logger.info("VOICE_CLIPS retention removed=%d kept_non_member=%d", removed, kept)
    return dict(removed=removed, kept=kept)


def relabel_cluster(cluster_id: str, kind: str, ident: str, directory: Optional[str] = None) -> int:
    """A cluster was named: its clips are now that guest's (still non-member, still on the 30-day clock). Returns how many."""
    n = 0
    for side, _wav, meta in _labelled(directory or sample_dir()):
        if meta.get("kind") == "unknown" and meta.get("cluster") == cluster_id:
            try:
                _write(side, dict(meta, kind=kind, id=str(ident)[:64], cluster=""))
                n += 1
            except OSError:
                continue
    return n


def erase_guests(person_ids, directory: Optional[str] = None) -> int:
    """Forget: delete every saved clip labelled as one of these guests (and the sidecar). Counts only."""
    ids = {str(i) for i in person_ids}
    n = sum(1 for side, wav, meta in _labelled(directory or sample_dir())
            if meta.get("kind") == "guest" and str(meta.get("id")) in ids and _remove(side, wav))
    if n:
        logger.info("VOICE_CLIPS forget removed=%d", n)
    return n


def erase_member(member_id: str, directory: Optional[str] = None) -> int:
    """A member delete: every saved clip whose verdict sidecar says it was THIS member (and the sidecar). Located by the label the server
    wrote, never by a file name. STRICT (``_labelled_strict``): a corpus that cannot be listed, a sidecar that cannot be read, or a clip
    that cannot be removed RAISES (the member delete fails closed and is retried); a sidecar that is not a valid verdict is removed with
    its clip only when its text names the member. Legacy clips with no sidecar name nobody and stay. Counts only. Blocking file work:
    callers on the event loop run it in a thread."""
    ident = str(member_id or "")
    if not ident:
        return 0
    n = 0
    for side, wav, meta, named in _labelled_strict(directory or sample_dir(), ident):
        if (meta is not None and meta.get("kind") == "member" and str(meta.get("id")) == ident) or (meta is None and named):
            _remove_strict(side, wav)
            n += 1
    if n:
        logger.info("VOICE_CLIPS member erase removed=%d", n)
    return n


def newest_unknown_clip(directory: Optional[str] = None, valid=None) -> Optional[tuple[str, str]]:
    """``(wav, cluster_id)`` of the newest saved clip filed under an unknown voice that still exists (``valid`` = the live cluster ids), else None."""
    best = None
    for side, wav, meta in _labelled(directory or sample_dir()):          # sorted by name = by capture time
        c = str(meta.get("cluster") or "")
        if meta.get("kind") == "unknown" and c and (valid is None or c in valid) and os.path.isfile(wav):
            best = (wav, c)
    return best


def _retry_pending_file() -> int:
    """The clips listed in ``pending_file`` (a drop that could not even mark its clip): removed with any sidecar; the ones that still
    cannot go stay listed for the next pass. Counts only. ``_PENDING_LOCK`` is held only to READ the list and, at the end, to REWRITE it
    (re-read under the lock, so an append made while the clips were being removed is kept) - never across the removals themselves, so
    an append (a voice turn's failed drop) waits at most for one small read or rewrite, never for the sweep."""
    path = pending_file()
    with _PENDING_LOCK:
        listed = _read_lines(path)
    if not listed:
        return 0
    removed, n = set(), 0
    for clip in dict.fromkeys(listed):
        try:
            _remove_strict(sidecar_path(clip), clip)
            removed.add(clip)
            n += 1
        except OSError:
            pass
    with _PENDING_LOCK:
        now = _read_lines(path)
        left = list(dict.fromkeys(c for c in (now or []) if c not in removed))
        try:
            if left:
                _write_lines(path, left)
            elif now is not None:
                os.unlink(path)
        except OSError as exc:
            logger.warning("VOICE_CLIPS pending-drop list not rewritten (%s)", type(exc).__name__)
    return n


def _read_lines(path: str) -> Optional[list]:
    """The pending list's lines; None when there is no list (or it cannot be read: logged, retried next pass)."""
    try:
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("VOICE_CLIPS pending-drop list unreadable (%s)", type(exc).__name__)
        return None


def _write_lines(path: str, lines: list) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".pending-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("".join(x + "\n" for x in lines))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def retry_pending_drops(directory: Optional[str] = None) -> int:
    """The durable RETRY of a clip drop that failed: every clip whose label says ``pending_drop`` (written by ``drop_current`` just
    before it removes the clip, only for a turn whose member was erased at or after it began) goes. It covers a clip whose speaker was
    resolved AFTER the member delete completed (``routers/voice_tts._label_member_clip``): the marker on disk IS the retry record, so
    no new store is needed, and a clip saved later (a new turn, a member re-created with the same id) carries no marker and stays. One
    clip that cannot go yet never holds up another (the next pass retries it). Counts only. Blocking file work: run it in a thread."""
    n = 0
    for side, wav, meta in _labelled(directory or sample_dir()):
        if not meta.get("pending_drop"):
            continue
        try:
            _remove_strict(side, wav)
            n += 1
        except OSError as exc:
            logger.warning("VOICE_CLIPS pending-drop retry failed (%s)", type(exc).__name__)
    n += _retry_pending_file()
    if n:
        logger.info("VOICE_CLIPS pending-drop retry removed=%d", n)
    return n


async def run_retention_loop() -> None:
    """Started by ``main.lifespan``. Every pass first retries the clip drops that failed (``retry_pending_drops``, always on: it
    finishes a member delete, it is not retention), then the non-member retention (its flag is read per pass, so a flip needs no
    restart). Never raises."""
    while True:
        try:
            await asyncio.get_running_loop().run_in_executor(None, retry_pending_drops)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice_clips: pending-drop retry failed (%s)", type(exc).__name__)
        await asyncio.sleep(SWEEP_EVERY_S)
        try:
            if retention_enabled():
                await asyncio.get_running_loop().run_in_executor(None, sweep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice_clips: retention pass failed (%s)", type(exc).__name__)
