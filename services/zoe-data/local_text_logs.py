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

Rewrites are atomic (temp file + ``os.replace``), keep the file mode, and re-append anything the writer added while the
rewrite ran. The residual window is the gap between that last read and the replace (microseconds); a shadow-log line lost in it
is a statistic, never a memory. In a process that imported ``semantic_router`` the rewrite also takes its append lock; a
separate process (the hourly job, the owner script) cannot, so a size rotation by the writer DURING a rewrite (the live file is a few
MB against a 16 MB cap) could leave the pre-rotation content in two segments: duplicate rows in a statistics log, not a leak.
"""
from __future__ import annotations

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
    """Every existing file of ``log``: the live file plus, for the router log, its rotated ``.1`` .. ``.KEEP`` segments."""
    base = log_path(log)
    out: list[Path] = []
    if log is ROUTER_SHADOW:
        try:
            keep = int(os.environ.get("ZOE_ROUTER_SHADOW_KEEP", "4"))
        except ValueError:
            keep = 4
        out += [Path(f"{base}.{n}") for n in range(max(keep, 1), 0, -1) if Path(f"{base}.{n}").exists()]
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
    """The router's own append lock when this process has it loaded (zoe-data); otherwise a no-op context."""
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


def rewrite(path: Path, fn: Callable[[str], Optional[str]]) -> int:
    """Rewrite ``path`` line by line. ``fn(line)`` returns the new line (the same object when unchanged) or None to drop it.
    Returns how many lines changed or were dropped; the file is replaced only when that is non-zero."""
    try:
        with _append_lock():
            raw = path.read_bytes()
            size = len(raw)
            mode = path.stat().st_mode & 0o777
            changed = 0
            out: list[str] = []
            for line in raw.decode("utf-8", errors="surrogateescape").split("\n"):
                if not line.strip():
                    continue
                new = fn(line)
                if new is None:
                    changed += 1
                    continue
                if new is not line and new != line:
                    changed += 1
                out.append(new)
            if not changed:
                return 0
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(("\n".join(out) + "\n" if out else "").encode("utf-8", errors="surrogateescape"))
                    with open(path, "rb") as cur:        # lines a writer appended while we worked
                        cur.seek(size)
                        fh.write(cur.read())
                os.chmod(tmp, mode)
                os.replace(tmp, path)
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
    n = 0
    for seg in segments(log):
        n += rewrite(seg, lambda line: _strip_or_drop(log, line, **kw))
    return n


def sweep(now: Optional[float] = None) -> dict[str, int]:
    """Apply the retention window to every registered log. ``{log name: records stripped or dropped}``. Never raises."""
    days = retention_days()
    out: dict[str, int] = {}
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
