"""Local append-only JSONL logs that hold household WORDS, and what retention and the forget cascade do to them.

Architecture deep dive A6 (2026-10-18): "forgotten means forever" held for the palace and Postgres but not for the box -
the router's shadow log kept ~10.8k raw utterances beside the hash-keyed record, and nothing aged them out or reached them on
a forget. Two such logs are registered here (stdlib only, so the hourly log-rotation job and the owner scripts can import it):

  ``router_shadow``  ``services/zoe-data/data/router_head_shadow.jsonl`` (+ its rotated ``.N`` segments; ``ZOE_ROUTER_HEAD_LOG``).
                     Every record is keyed by a 12-hex one-way utterance HASH (``utt``); ``utt_text`` is the opt-in plaintext
                     (``ZOE_ROUTER_SHADOW_TEXT=1``) the router self-training miner reads directly. The miner can ALSO recover the
                     words by re-hashing ``chat_messages`` and joining on the hash (labs/router-selftrain), so ``utt_text`` is a
                     coverage convenience, not a dependency: stripping it from an old record loses at most the turns that never
                     reached ``chat_messages``. Expiry therefore STRIPS the text and keeps the hash + routing features.
  ``intent_misses``  ``~/training/data/intent-misses.jsonl`` (``{"text", "ts"}``; names/numbers/emails/URLs crudely masked, a
                     single first name is not). Read for 7 days by ``evolution_notice``. Expiry DROPS the record.

What reaches them:
  * RETENTION   ``sweep()`` - text older than ``ZOE_TEXT_LOG_RETENTION_DAYS`` (default 30; ``keep`` = never; ``0`` = nothing is
                kept). Run hourly by ``scripts/maintenance/rotate_service_logs.py``; owner-run ``scripts/maintenance/
                redact_router_shadow.py`` does the same once, with a backup.
  * ENTITY      ``erase_entity(rx)`` - a record whose text names a forgotten entity is DROPPED whole (the hash of a sentence that
                named them goes too). Wired as the cascade step ``text_logs`` (``memory_forget_cascade``), so a failure is
                recorded and retried from the ledger alone.
  * MEMBER      ``erase_all_text()`` - the logs carry no user id (by design: a hash cannot be attributed), so a member erase
                (``MemoryService.delete_user``) clears the TEXT layer of every record household-wide. Hash-only rows stay: they
                hold no words.

A pass over the router log holds ``locked(log)``: the in-process append lock and an exclusive ``flock`` on ``<log>.lock``, the same
two locks ``semantic_router._append_shadow_line`` takes (same order) around rotate + append. So in or out of process no append and
no rotation interleaves with the pass, which enumerates EVERY existing numeric segment (``.5`` left over from a larger
``ZOE_ROUTER_SHADOW_KEEP`` included) inside the lock. Rewrites are atomic (fsynced temp file + ``os.replace`` + directory fsync)
and keep the file mode. As defence against a writer that does not take the lock (the intent-miss log's writer): the old inode stays
open, rows that reach it before the replace are put THROUGH the same transformation, the path is checked to still name that inode
(else ``RewriteRaced``, and the pass rescans), and after the replace a short settle (``_SETTLE_S``) then the late rows are
transformed and re-appended. A row that names a forgotten entity is therefore never copied through.

BACKUPS an owner script makes of these logs (``redact_router_shadow.py``: the raw copy) are not rewritten by a forget; they are
DELETED by ``prune_adhoc_backups`` (hourly, with the sweep) once older than ``ADHOC_BACKUP_MAX_AGE_DAYS`` (14).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30.0
_HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class TextLog:
    name: str
    text_field: str
    expire: str                      # "strip": remove ``text_field`` and keep the record; "drop": remove the record
    ts_field: str = "ts"


ROUTER_SHADOW = TextLog("router_shadow", "utt_text", "strip")
INTENT_MISSES = TextLog("intent_misses", "text", "drop")
LOGS = (ROUTER_SHADOW, INTENT_MISSES)


def log_path(log: TextLog) -> Path:
    if log is ROUTER_SHADOW:
        return Path(os.environ.get("ZOE_ROUTER_HEAD_LOG") or (_HERE / "data" / "router_head_shadow.jsonl"))
    if log is INTENT_MISSES:
        # the writer (intent_router.detect_intent) is fixed at ~/training/data; the env var exists so a TEST can pin this module off it
        return Path(os.environ.get("ZOE_INTENT_MISS_LOG") or (Path.home() / "training" / "data" / "intent-misses.jsonl"))
    raise KeyError(log.name)


def segments(log: TextLog) -> list[Path]:
    """Every existing file of ``log``, OLDEST first: the router log's rotated segments (EVERY numeric ``.N`` suffix that exists - a
    segment above a since-lowered ``ZOE_ROUTER_SHADOW_KEEP`` is never rotated away again, so it must still be swept) then the live file."""
    base = log_path(log)
    out: list[Path] = []
    if log is ROUTER_SHADOW and base.parent.is_dir():
        rotated = [(int(p.name[len(base.name) + 1:]), p) for p in base.parent.glob(base.name + ".*")
                   if p.name[len(base.name) + 1:].isdigit()]
        out += [p for _n, p in sorted(rotated, reverse=True)]
    if base.exists():
        out.append(base)
    return out


def retention_days() -> Optional[float]:
    """``ZOE_TEXT_LOG_RETENTION_DAYS``: days of text to keep (default 30). ``keep`` / ``off`` / ``forever`` -> None (no limit);
    ``0`` -> 0 (keep none); anything unparseable -> the default (never silently 'keep forever')."""
    raw = (os.environ.get("ZOE_TEXT_LOG_RETENTION_DAYS") or "").strip().lower()
    if not raw:
        return DEFAULT_RETENTION_DAYS
    if raw in ("keep", "off", "forever", "never"):
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("local_text_logs: ZOE_TEXT_LOG_RETENTION_DAYS=%r is not a number - using %s days", raw, DEFAULT_RETENTION_DAYS)
        return DEFAULT_RETENTION_DAYS


def _append_lock():
    """The router's own in-process append lock when this process has it loaded (zoe-data); otherwise a no-op context."""
    mod = sys.modules.get("semantic_router")
    lock = getattr(mod, "_shadow_write_lock", None)
    if lock is not None:
        return lock

    class _Null:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Null()


LOCK_WAIT_S = 30.0


def lock_path(log: TextLog) -> Path:
    base = log_path(log)
    return base.with_name(base.name + ".lock")


@contextlib.contextmanager
def locked(log: TextLog):
    """Hold the log's write lock for a whole multi-segment pass: the in-process append lock, then (router log only) an exclusive
    ``flock`` on ``<log>.lock`` - the SAME lock ``semantic_router._append_shadow_line`` takes around rotate + append, in the same order.
    So no append and no rotation can interleave with the rewrite, in this process or another. Fails closed: if the flock cannot be
    had within ``LOCK_WAIT_S`` it raises ``TimeoutError`` (the sweep reports -1, the cascade step fails and is retried). The intent-miss
    log has no cooperating writer lock; the old-inode catch-up in ``rewrite`` covers it."""
    with _append_lock():
        if log is not ROUTER_SHADOW:
            yield
            return
        path = lock_path(log)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path, "a")
        except OSError:
            yield                                           # no lock file possible (read-only dir): nothing is being written there either
            return
        try:
            deadline = time.monotonic() + LOCK_WAIT_S
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"shadow-log write lock busy for {LOCK_WAIT_S:.0f}s") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


_SETTLE_S = 0.05


class RewriteRaced(Exception):
    """The path no longer names the inode we read (a rotation renamed it). Nothing was replaced; the caller rescans."""


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass                                               # a directory that cannot be fsynced (some filesystems) is not a failure


def _through(data: bytes, fn: Callable[[str], Optional[str]]) -> tuple[list[str], int]:
    """``data`` line by line through ``fn``: ``(kept lines, how many changed or dropped)``."""
    changed = 0
    out: list[str] = []
    for line in data.decode("utf-8", errors="surrogateescape").split("\n"):
        if not line.strip():
            continue
        new = fn(line)
        if new is None:
            changed += 1
            continue
        if new is not line and new != line:
            changed += 1
        out.append(new)
    return out, changed


def _blob(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n" if lines else "").encode("utf-8", errors="surrogateescape")


def rewrite(path: Path, fn: Callable[[str], Optional[str]]) -> int:
    """Rewrite ``path`` line by line. ``fn(line)`` returns the new line (the same object when unchanged) or None to drop it.
    Returns how many lines changed or were dropped; the file is replaced only when that is non-zero.

    The CALLER holds ``locked(log)`` for a cooperating writer (``_apply`` does). Without it, rows can still arrive in the old inode
    while we work: every such row is put through ``fn`` too (the tail before the replace, and whatever a stalled writer adds after
    it), so an appended row that names a forgotten entity is erased, not copied through. If the path stopped naming the inode we read
    (a rotation renamed it) nothing is replaced and ``RewriteRaced`` is raised for the caller to rescan."""
    try:
        with open(path, "rb") as old:                          # ``old`` stays open: it is the inode a late writer may still hit
            st = os.fstat(old.fileno())
            out, changed = _through(old.read(), fn)
            mode = st.st_mode & 0o777
            if not changed:
                return 0
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    tail, tchanged = _through(old.read(), fn)  # rows appended while we worked
                    changed += tchanged
                    fh.write(_blob(out + tail))
                    fh.flush()
                    os.fsync(fh.fileno())
                os.chmod(tmp, mode)
                try:
                    cur = os.stat(path)
                except FileNotFoundError:
                    cur = None
                if cur is None or (cur.st_ino, cur.st_dev) != (st.st_ino, st.st_dev):
                    raise RewriteRaced(str(path))
                os.replace(tmp, path)
                _fsync_dir(path.parent)
                time.sleep(_SETTLE_S)                          # a writer that opened the old inode before the replace finishes its append
                late, lchanged = _through(old.read(), fn)
                changed += lchanged
                if late:
                    with open(path, "ab") as fh:
                        fh.write(_blob(late))
                        fh.flush()
                        os.fsync(fh.fileno())
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return changed
    except FileNotFoundError:
        return 0


def _record(line: str) -> Optional[dict[str, Any]]:
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _strip_or_drop(log: TextLog, line: str, *, older_than: Optional[float] = None, match: Any = None,
                   everything: bool = False) -> Optional[str]:
    """One line through the log's expiry rule when the line is selected (by age, by name match, or unconditionally)."""
    rec = _record(line)
    if rec is None:
        return line                                   # a line that is not a record is not ours to touch
    text = rec.get(log.text_field)
    if not isinstance(text, str) or not text:
        # a text-less record (router hash-only row) is not selected by name; an age/all rule may still DROP a text-less miss row
        if log.expire == "drop" and (everything or (older_than is not None and _ts(rec, log) < older_than)):
            return None
        return line
    selected = everything
    if not selected and older_than is not None:
        selected = _ts(rec, log) < older_than
    if not selected and match is not None:
        selected = bool(match.search(text))
        if selected:
            return None                               # a record that NAMED a forgotten entity goes whole, hash included
    if not selected:
        return line
    if log.expire == "drop":
        return None
    rec.pop(log.text_field, None)
    return json.dumps(rec, sort_keys=True)


def _ts(rec: dict[str, Any], log: TextLog) -> float:
    try:
        return float(rec.get(log.ts_field))
    except (TypeError, ValueError):
        return 0.0                                    # no timestamp = oldest: it ages out rather than living forever


def _apply(log: TextLog, **kw: Any) -> int:
    """One full pass over every file of ``log`` under its write lock; a segment that was renamed under us is retried after a rescan."""
    def fn(line: str) -> Optional[str]:
        return _strip_or_drop(log, line, **kw)

    total = 0
    with locked(log):
        for _attempt in range(4):
            raced = False
            for seg in segments(log):
                try:
                    total += rewrite(seg, fn)
                except RewriteRaced:
                    raced = True
            if not raced:
                return total
    raise RewriteRaced(f"{log.name}: still racing a rotation after 4 passes")


ADHOC_BACKUP_MAX_AGE_DAYS = 14
#: directory-name prefixes under the backup root that owner scripts write and that hold words or secrets (docs/knowledge/forget-reach.md)
ADHOC_BACKUP_PREFIXES = ("router-shadow-", "migrate-hermes-env-")


def adhoc_backup_root() -> Path:
    return Path(os.environ.get("ZOE_ADHOC_BACKUP_DIR") or (Path.home() / ".zoe" / "backups"))


def prune_adhoc_backups(now: Optional[float] = None) -> int:
    """Delete the owner scripts' one-off backups (raw text, copies of env files) older than ``ADHOC_BACKUP_MAX_AGE_DAYS``. A forget
    cannot rewrite them, so their age bound is the whole guarantee - it is ENFORCED here, not left to a note. Returns the count
    removed. Raises on a failure to delete (the sweep reports it)."""
    import shutil

    root = adhoc_backup_root()
    if not root.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - ADHOC_BACKUP_MAX_AGE_DAYS * 86400.0
    removed = 0
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.is_symlink() and d.name.startswith(ADHOC_BACKUP_PREFIXES) and d.stat().st_mtime < cutoff:
            shutil.rmtree(d)
            removed += 1
    return removed


def sweep(now: Optional[float] = None) -> dict[str, int]:
    """Apply the retention window to every registered log, and expire the owner scripts' one-off backups.
    ``{name: records stripped or dropped | backups removed}``; a value of -1 means that part FAILED (the hosting job must fail
    too: a retention pass that silently did not run leaves words behind). Never raises."""
    out: dict[str, int] = {}
    try:
        out["adhoc_backups"] = prune_adhoc_backups(now)
    except Exception as exc:  # noqa: BLE001
        logger.error("local_text_logs: backup expiry failed (%s)", type(exc).__name__)
        out["adhoc_backups"] = -1
    days = retention_days()
    if days is None:
        return out
    cutoff = (now if now is not None else time.time()) - days * 86400.0
    for log in LOGS:
        try:
            out[log.name] = _apply(log, older_than=cutoff)
        except Exception as exc:  # noqa: BLE001 - a retention pass must never break the job that hosts it
            logger.error("local_text_logs: sweep of %s failed (%s)", log.name, type(exc).__name__)
            out[log.name] = -1
    if any(v > 0 for v in out.values()):
        logger.info("local_text_logs: retention sweep %s", " ".join(f"{k}={v}" for k, v in out.items()))
    return out


def erase_entity(rx: Any) -> int:
    """Drop every record whose text matches ``rx`` (a ``re.Pattern`` or ``memory_forgotten.LedgerPattern``). RAISES on an I/O
    failure so the cascade records the step as failed (a forget that left words behind must not read as done)."""
    return sum(_apply(log, match=rx) for log in LOGS)


def erase_all_text() -> int:
    """Member erase: the logs cannot be attributed to one member, so clear the text layer of every record. RAISES on failure."""
    return sum(_apply(log, everything=True) for log in LOGS)


def count_text(log: TextLog) -> tuple[int, int]:
    """``(records, records still carrying text)`` across the log's files - counts only, for dry runs and reports."""
    total = with_text = 0
    for seg in segments(log):
        for line in seg.read_text(encoding="utf-8", errors="replace").splitlines():
            rec = _record(line)
            if rec is None:
                continue
            total += 1
            if isinstance(rec.get(log.text_field), str) and rec.get(log.text_field):
                with_text += 1
    return total, with_text
