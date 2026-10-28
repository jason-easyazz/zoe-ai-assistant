"""Which member a panel's wake KV warm should render for: the panel's LIKELY next speaker, kept across restarts.

The brain's cacheable prefix carries the member's manner block + user-model card AHEAD of the tool block, so a warm
rendered for the wrong person (or a guest) cannot be resumed by the member's real turn: it re-prefills ~1k+ tokens
(brain TTFT ~3.8 s instead of ~1.2 s, 2026-10-28). The guess used to live only in a process memo, empty after every
zoe-data restart. This module keeps it - ``{panel_id: [member_id, epoch_s]}``, ids and a time only - in
``~/.zoe/warm-speaker.json`` (``ZOE_WARM_SPEAKER_STATE`` overrides).

What it is NOT: an identity, an auth input, or a record of what anyone said. It is written only where the voice path
already holds a server-verified member for the turn (``routers.voice_tts._note_voice_speaker``: an unrecognised voice is
a guest and never recorded), and it is read only to choose which envelope a THROWAWAY replay-marked warm turn renders
(``zoe_flue_client._kv_warm_outbound``). A guest's real turn never takes this memo's envelope, so nothing of a member's
manner or card can reach a guest's turn through it. An entry older than ``ZOE_WARM_SPEAKER_MAX_AGE_S`` (default and hard maximum 12 h; the env can only shorten it)
reads as no entry and is pruned from the file on the next save (the warm degrades to the guest envelope). ``forget_user`` (forget / voice-profile delete / consent
revoke) removes a member from memory and file. Never raises. ``note`` does no I/O (the save runs on a daemon thread, which also does the first load);
``get`` / ``forget_user`` do the one lazy load of a few hundred bytes, so async callers run them via ``asyncio.to_thread``.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Optional

import user_filters
from typed_env import env_float, env_str

logger = logging.getLogger(__name__)

_MAX_PANELS = 64
HARD_MAX_AGE_S = 43200.0   # 12 h: an upper bound no setting lifts
_REWRITE_AFTER_S = 600.0   # an unchanged entry is not rewritten more often than this (a hot panel must not write every turn)
_lock = threading.RLock()      # guards the in-memory dicts only; never held across disk I/O
_load_lock = threading.Lock()  # serialises the one lazy file read
_io_lock = threading.Lock()    # serialises the file writes
_mem: "dict[str, tuple[str, float]]" = {}
_loaded = False
_last_written: "dict[str, tuple[str, float]]" = {}


def _nobody(member: str) -> bool:
    """The repo's own guest predicate (every sentinel and any ``guest-<id>`` principal): never a recordable speaker."""
    return user_filters.is_unidentified(member)


def max_age_s() -> float:
    v = env_float("ZOE_WARM_SPEAKER_MAX_AGE_S", HARD_MAX_AGE_S)   # a bad value warns once and falls back to the cap
    if not math.isfinite(v):
        logger.warning("warm_speaker: ZOE_WARM_SPEAKER_MAX_AGE_S is not finite; using the %.0f s cap", HARD_MAX_AGE_S)
        return HARD_MAX_AGE_S
    return min(HARD_MAX_AGE_S, max(0.0, v))   # the env can shorten the cap, never lift it


def _path() -> "Optional[Path]":
    override = env_str("ZOE_WARM_SPEAKER_STATE")
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # an unrelated test must never touch the live state file: only an explicit override OUTSIDE ~/.zoe counts
        live = (Path.home() / ".zoe").resolve()
        if not override or Path(override).resolve() == live or live in Path(override).resolve().parents:
            return None
    return Path(override) if override else Path.home() / ".zoe" / "warm-speaker.json"


def reset() -> None:
    """Forget the in-process view and re-read the file on next use (tests; also exactly what a fresh process looks like)."""
    global _loaded
    with _lock:
        _mem.clear()
        _last_written.clear()
        _loaded = False


def _ensure_loaded() -> None:
    """One lazy read of the file. The read happens under ``_load_lock`` only (never ``_lock``), so ``note()`` never waits on it."""
    global _loaded
    with _load_lock:
        if _loaded:
            return
        p = _path()
        loaded: "dict[str, tuple[str, float]]" = {}
        if p is not None:
            try:
                raw = json.loads(p.read_text())
                for panel, ent in (raw or {}).items():
                    if isinstance(ent, list) and len(ent) == 2 and isinstance(ent[0], str) and not _nobody(ent[0]) and math.isfinite(float(ent[1])):
                        loaded[str(panel)] = (ent[0], float(ent[1]))
            except FileNotFoundError:
                pass
            except Exception:  # noqa: BLE001 - a corrupt file is no memo (not re-read every turn)
                logger.warning("warm_speaker: state unreadable; starting empty", exc_info=True)
        with _lock:
            for panel, ent in loaded.items():
                _mem.setdefault(panel, ent)            # a newer in-process note wins
                _last_written.setdefault(panel, ent)   # what the FILE holds; an unsaved note is not "written"
            _loaded = True


def _persist() -> None:
    """Write the current memo atomically. Disk work holds only ``_io_lock`` (writers serialise, each snapshotting the
    latest memo), never ``_lock``, so ``note()`` on the request path does not wait on storage."""
    p = _path()
    if p is None:
        return
    written: "dict[str, tuple[str, float]]" = {}
    try:
        _ensure_loaded()   # on the save thread: merges what the file holds under whatever was noted before the first load
        with _io_lock:
            with _lock:
                now = time.time()
                for k in [k for k, (_m, t) in _mem.items() if now - t > max_age_s()]:
                    _mem.pop(k, None)                               # an expired entry does not stay on disk
                snap = {k: [m, t] for k, (m, t) in _mem.items()}
                written = dict(_mem)
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = p.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # private from the first byte, whatever the umask
            with os.fdopen(fd, "w") as fh:
                os.fchmod(fh.fileno(), 0o600)                       # a leftover temp file from a crash keeps its old mode otherwise
                fh.write(json.dumps(snap))
            os.replace(tmp, p)
            with _lock:
                _last_written.clear()                               # only a SUCCESSFUL write counts: a failed one is retried next turn
                _last_written.update(written)
    except Exception:  # noqa: BLE001
        with _lock:
            for k, v in written.items():
                if _last_written.get(k) != v:
                    _last_written.pop(k, None)   # this snapshot never reached disk: the next turn for it must save again
        logger.warning("warm_speaker: state not saved", exc_info=True)


def _save(sync: bool) -> None:
    if _path() is None:
        return
    if sync:
        _persist()
    else:
        threading.Thread(target=_persist, name="warm-speaker-save").start()   # non-daemon: joined at interpreter exit on a graceful stop; a hard kill inside the few-ms window loses one write, which the next verified turn rewrites


def note(panel_id: str, member: str, *, now: "float | None" = None, sync: bool = False) -> None:
    """Record ``member`` (server-verified by the caller) as ``panel_id``'s likely next speaker."""
    try:
        m = (member or "").strip()
        if not panel_id or _nobody(m):
            return
        t = time.time() if now is None else float(now)
        with _lock:
            prev = _last_written.get(panel_id)
            _mem.pop(panel_id, None)
            _mem[panel_id] = (m, t)
            while len(_mem) > _MAX_PANELS:
                _mem.pop(next(iter(_mem)))
            changed = prev is None or prev[0] != m or t - prev[1] >= _REWRITE_AFTER_S
        if changed:
            _save(sync)
    except Exception:  # noqa: BLE001
        pass


def get(panel_id: str, *, now: "float | None" = None) -> str:
    """The panel's likely speaker, or '' (none, or older than the age cap). A cache-shaping hint, never an identity."""
    try:
        if not panel_id:
            return ""
        _ensure_loaded()
        t = time.time() if now is None else float(now)
        with _lock:
            ent = _mem.get(panel_id)
        if not ent or t - ent[1] > max_age_s() or ent[1] - t > 300:   # too old, or stamped in the future (clock jump)
            return ""
        return ent[0]
    except Exception:  # noqa: BLE001
        return ""


def forget_user(member_id: str, *, sync: bool = True) -> int:
    """Remove ``member_id`` from every panel's entry, in memory and in the file. Returns entries removed. Never raises."""
    try:
        m = (member_id or "").strip()
        if not m:
            return 0
        _ensure_loaded()
        with _lock:
            gone = [k for k, (u, _) in _mem.items() if u == m]
            for k in gone:
                _mem.pop(k, None)
        if gone:
            _save(sync)
        return len(gone)
    except Exception:  # noqa: BLE001
        return 0
