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
Never raises: a trace failure is logged at debug level and the gate's decision is unaffected.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_RECALL_GATE_TRACE"
DAYS_ENV = "ZOE_RECALL_GATE_TRACE_DAYS"
_ON = frozenset({"1", "true", "yes", "on"})
MAX_BYTES = 2 * 1024 * 1024        # one file; about 2,000 decisions
KEEP = 3                           # rotated files kept beside the live one
WORDS_CHARS = 512
TEXT_CHARS = 600

#: the trace directory; a module constant so tests can point it at a temp dir. Never inside the repo or ~/.zoe-logs.
TRACE_DIR = Path(os.path.expanduser("~/.zoe/recall-gate-trace"))
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


def _prune(d: Path, now: float) -> None:
    """Delete rotated files older than the retention window, and the live file if it was last written before it."""
    horizon = now - _retention_s()
    for p in [d / FILE_NAME] + [d / f"{FILE_NAME}.{i}" for i in range(1, KEEP + 1)]:
        try:
            if p.exists() and p.stat().st_mtime < horizon:
                p.unlink()
        except OSError:
            pass


def _rotate(d: Path) -> None:
    live = d / FILE_NAME
    try:
        if not live.exists() or live.stat().st_size < MAX_BYTES:
            return
    except OSError:
        return
    oldest = d / f"{FILE_NAME}.{KEEP}"
    if oldest.exists():
        oldest.unlink()
    for i in range(KEEP - 1, 0, -1):
        src = d / f"{FILE_NAME}.{i}"
        if src.exists():
            src.rename(d / f"{FILE_NAME}.{i + 1}")
    live.rename(d / f"{FILE_NAME}.1")


def _append(line: str, now: float) -> None:
    d = TRACE_DIR
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    _prune(d, now)
    _rotate(d)
    fd = os.open(d / FILE_NAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)          # an existing file created looser is tightened, not trusted
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _cut(s: Any, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def record(user_id: str, dec: Any, owner_words: str, cands: Iterable[Any], *, mode: str, now: Optional[float] = None) -> bool:
    """Write one trace line for ``dec`` when the flag is on. ``cands`` are the gate's candidates (``id`` + ``text``), so the dropped
    rows' text is known. Returns True when a line was written. Never raises."""
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
        _append(json.dumps(row, ensure_ascii=False) + "\n", t)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_gate_trace: write failed (%s)", type(exc).__name__)
        return False
