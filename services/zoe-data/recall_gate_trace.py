"""Opt-in private trace of the recall gate's decisions (``ZOE_RECALL_GATE_TRACE``, default OFF).

Why it exists. The ``RECALL_GATE`` log line carries 8-character row ids and counts, never text (by design: the normal logs must not hold
household content). That makes the enforce decision unscorable: judging "would this added row have helped this reply" needs the row's
text and the owner's words (docs/research/recall-gate-shadow-replay-2026-11-05.md). This trace is the owner's switch for collecting
exactly that, privately, for a bounded time.

What it writes, only when the flag is on: one JSON line per gate decision (packet or hop surface, shadow or enforce) to
``~/.zoe/recall-gate-trace/trace.jsonl`` - the time, user id, surface, turn, mode, the owner's words (cut at 512 characters), the
selected ids, and the full id + text of every row the gate would ADD and every row it would DROP. Never to the repo, never to the
normal logs (``~/.zoe-logs``), never to stdout.

Privacy and bounds (each pinned by a test):
* the directory is created 0700 and the file 0600 (an existing file is re-chmodded to 0600 on every open);
* rotation: past ``MAX_BYTES`` the file moves to ``trace.jsonl.1`` (older ones shift up to ``KEEP`` files; the oldest is deleted);
* retention: a rotated file older than ``ZOE_RECALL_GATE_TRACE_DAYS`` days (default 7) is deleted on the next write, and so is the live
  file when its last write is older than that (an abandoned trace does not linger);
* off (the default) = no directory, no file, no I/O; turning it off stops writing, it does not delete what was written (delete the
  directory by hand when the scoring is done).
Forget (forgotten means forever): the trace shares ``local_text_logs``' erase barrier. ``erase_all`` (member erase) and
``erase_entity`` (entity forget) run INSIDE that module's erase interval; an append is refused while an erase runs, and a decision whose
rows were read before an erase that has since finished is not written (``epoch``). Both erasers RAISE on an I/O failure, so the forget
path records the step as failed. Symlinked trace paths are refused, for writing and for erasing.

``record`` never raises and is SILENT on failure (no log line at any level): the trace never leaves a trace in the normal logs.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

_LOCK = threading.Lock()           # appends (event loop) and the forget path's rewrites (a worker thread) never interleave

ENV = "ZOE_RECALL_GATE_TRACE"
DAYS_ENV = "ZOE_RECALL_GATE_TRACE_DAYS"
_ON = frozenset({"1", "true", "yes", "on"})
MAX_BYTES = 2 * 1024 * 1024        # one file; about 2,000 decisions
KEEP = 3                           # rotated files kept beside the live one
WORDS_CHARS = 512
TEXT_CHARS = 600

#: the trace directory. Never inside the repo or ~/.zoe-logs (the off-box backup excludes the name ``recall-gate-trace``).
DEFAULT_DIR = "~/.zoe/recall-gate-trace"
TRACE_DIR: Optional[Path] = None     # a test override; None = ``ZOE_RECALL_GATE_TRACE_DIR`` (read per call; the test conftest pins it) or the default


def trace_dir() -> Path:
    if TRACE_DIR is not None:
        return Path(TRACE_DIR)
    try:
        from typed_env import env_str

        raw = env_str("ZOE_RECALL_GATE_TRACE_DIR", "").strip()
    except Exception:  # noqa: BLE001
        raw = ""
    return Path(os.path.expanduser(raw or DEFAULT_DIR))
FILE_NAME = "trace.jsonl"


def enabled() -> bool:
    """True only when ``ZOE_RECALL_GATE_TRACE`` is explicitly on. Per-call read; default off."""
    try:
        from typed_env import env_str

        return env_str("ZOE_RECALL_GATE_TRACE", "").strip().lower() in _ON
    except Exception:  # noqa: BLE001
        return False


def _retention_s() -> float:
    try:
        from typed_env import env_int

        days = env_int("ZOE_RECALL_GATE_TRACE_DAYS", 7)
    except Exception:  # noqa: BLE001
        days = 7
    return max(1, int(days)) * 86400.0


class _Unsafe(OSError):
    """The trace directory or a trace file is a symlink or not what it should be: nothing is written or read through it."""


def _no_link(p: Path) -> None:
    if p.is_symlink():
        raise _Unsafe(f"symlink refused: {p.name}")


def _safe_dir() -> Path:
    """The trace directory, created 0700; a symlinked directory, a symlinked ANCESTOR, or a non-directory path is REFUSED (a pre-planted
    link must not redirect the owner's words into the repo or anywhere else)."""
    d = trace_dir()
    parent = d.parent
    if os.path.realpath(parent) != os.path.abspath(parent):
        raise _Unsafe("a symlinked ancestor of the trace directory")    # e.g. ~/.zoe pointing into the repo or a synced folder
    _no_link(d)
    d.mkdir(mode=0o700, exist_ok=True)                                  # never creates ancestors: the parent must exist as itself
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode):
        raise _Unsafe("trace path is not a directory")
    os.chmod(d, 0o700)
    return d


def _paths(d: Path) -> list:
    return [d / FILE_NAME] + [d / f"{FILE_NAME}.{i}" for i in range(1, KEEP + 1)]


def _prune(d: Path, now: float) -> None:
    """Delete rotated files older than the retention window, and the live file if it was last written before it. RAISES when
    retention cannot be enforced, so the append is abandoned rather than adding to an unbounded trace."""
    horizon = now - _retention_s()
    for p in _paths(d):
        _no_link(p)
        if p.exists() and p.stat().st_mtime < horizon:
            p.unlink()


def _rotate(d: Path) -> None:
    live = d / FILE_NAME
    if not live.exists() or live.stat().st_size < MAX_BYTES:
        return
    oldest = d / f"{FILE_NAME}.{KEEP}"
    if oldest.exists():
        oldest.unlink()
    for i in range(KEEP - 1, 0, -1):
        src = d / f"{FILE_NAME}.{i}"
        if src.exists():
            src.rename(d / f"{FILE_NAME}.{i + 1}")
    live.rename(d / f"{FILE_NAME}.1")


def _erase_state() -> tuple:
    """``(erase in progress, erase epoch)`` of the forget path's text-log barrier (``local_text_logs``), which the trace shares."""
    import local_text_logs

    return local_text_logs.erase_in_progress(), local_text_logs.erase_epoch()


def epoch() -> Optional[int]:
    """The forget barrier's epoch, captured by the gate BEFORE it reads the rows a decision is built on. Never raises."""
    try:
        return _erase_state()[1]
    except Exception:  # noqa: BLE001
        return None


def _append(line: str, now: float, ep: Optional[int] = None) -> bool:
    """Append under the lock the erasers hold. Refused (False) while a forget runs, or when one ran since ``ep`` was captured: the
    decision's words and rows may predate a forget that has already finished."""
    with _LOCK:
        busy, cur = _erase_state()
        if busy or ep is None or cur != ep:
            return False
        d = _safe_dir()
        _prune(d, now)
        _rotate(d)
        fd = os.open(d / FILE_NAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)          # an existing file created looser is tightened, not trusted
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return True


def _cut(s: Any, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def record(user_id: str, dec: Any, owner_words: str, cands: Iterable[Any], *, mode: str, ep: Optional[int] = None,
           now: Optional[float] = None) -> bool:
    """Write one trace line for ``dec`` when the flag is on. ``cands`` are the gate's candidates (``id`` + ``text``), so the dropped
    rows' text is known; ``ep`` is ``epoch()`` captured before the gate read them (None = no barrier reading = not written).
    Returns True when a line was written. Never raises."""
    if not enabled():
        return False
    try:
        if getattr(dec, "skipped", ""):
            return False
        text = {str(c.id): str(getattr(c, "text", "") or "") for c in cands}
        for p in getattr(dec, "selected", ()) or ():
            text.setdefault(str(p.id), str(getattr(p, "text", "") or ""))
        t = time.time() if now is None else now
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t)),
            "user": user_id, "surface": dec.surface, "turn": dec.turn, "mode": mode,
            "owner_words": _cut(owner_words, WORDS_CHARS),
            "selected": [str(p.id) for p in dec.selected],
            "added": [{"id": str(i), "text": _cut(text.get(str(i), ""), TEXT_CHARS)} for i in dec.added],
            "dropped": [{"id": str(i), "text": _cut(text.get(str(i), ""), TEXT_CHARS)} for i in dec.dropped],
            "budget_used": dec.budget_used,
        }
        return _append(json.dumps(row, ensure_ascii=False) + "\n", t, ep)
    except Exception:  # noqa: BLE001 - silent by contract: no log line, the gate's decision stands
        return False


def _files() -> list:
    d = trace_dir()
    if d.is_symlink() or os.path.realpath(d.parent) != os.path.abspath(d.parent):
        raise _Unsafe("symlinked trace directory or ancestor")
    if not d.exists():
        return []
    out = []
    for p in _paths(d):
        _no_link(p)
        if p.exists():
            out.append(p)
    return out


def _texts(obj: dict) -> list:
    out = [str(obj.get("owner_words") or "")]
    for k in ("added", "dropped"):
        out += [str((r or {}).get("text") or "") for r in (obj.get(k) or ()) if isinstance(r, dict)]
    return out


def _rewrite(drop) -> int:
    """Keep the lines ``drop(obj)`` rejects; a malformed line is dropped too (it could hold a word nothing here can match). The file is
    replaced atomically and stays 0600. Returns the number of lines dropped. RAISES on an I/O failure."""
    n = 0
    with _LOCK:
        for p in _files():
            kept = []
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    obj = None
                if not isinstance(obj, dict) or drop(obj):
                    n += 1
                    continue
                kept.append(line + "\n")
            tmp = p.with_name(p.name + ".tmp")
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()                  # a stale tmp (or a planted link: unlink removes the link, never its target)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, "".join(kept).encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(tmp, p)
    return n


def erase_all() -> int:
    """Member erase: drop every trace line. Called by ``local_text_logs.erase_all_text`` (from ``MemoryService.delete_user``) INSIDE its
    erase interval, so no append lands meanwhile; like those logs, a member erase clears the trace household-wide. RAISES on failure."""
    return _rewrite(lambda o: True)


def erase_entity(rx: Any) -> int:
    """Entity forget: drop every line whose owner words or row text match ``rx`` (a ``re.Pattern`` or a ledger pattern with
    ``.search``). Called by ``local_text_logs.erase_entity`` (the cascade's ``text_logs`` step) inside its erase interval. RAISES on failure."""
    return _rewrite(lambda o: any(rx.search(t) for t in _texts(o)))
