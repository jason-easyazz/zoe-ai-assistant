"""Write-time rejection ledger: every candidate the memory pipeline REFUSES is counted.

The audit trail records what was written (``ingest`` rows). It cannot show what was refused
before a write: the write-quality gate, the PII scrubber, a forget tombstone, an opt-out, a
dedup skip. Until this module the two highest-volume writers — the nightly digest and the
idle consolidation — dropped gate rejects with NO log line and NO counter
(``memory_digest._passes_quality_gate`` and ``memory_idle_consolidation`` discarded the
reason), the other sites logged the candidate TEXT at INFO, and the Prometheus counter died
with every restart. So "was this fact rejected, or never produced, or lost?" had no answer
(docs/knowledge/memory-loss-audit-2026-10-05.md, bucket b).

``record_reject(source, reason)`` is the one call every refusal site makes. It keeps COUNTS
only — never the text, never a user id — per UTC day, in memory and in a small JSON file so a
restart does not zero the day. The nightly digest pass logs one line from it::

    MEMORY_REJECT_SUMMARY window=1d rejected=3 reasons=question_mark:2,weather_report:1 sources=digest:2,idle_consolidation:1

(``rejected=0`` is logged too: silence must be distinguishable from "nothing refused").
Stdlib-only; every function is best-effort and never raises into a write path.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
from collections import Counter

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_KEEP_DAYS = 14
# "<day>" -> {"<source>|<reason>": n}
_MEM: dict[str, dict[str, int]] = {}


def _path() -> str | None:
    """Where the day counters persist. ``ZOE_MEMORY_REJECT_LEDGER`` overrides (tests pin it);
    the default is ``~/.zoe/memory-reject-ledger.json``. A non-service process (pytest /
    harness) with NO explicit override never persists: it must not write the household's home."""
    override = os.environ.get("ZOE_MEMORY_REJECT_LEDGER")
    if override:
        return override
    try:
        from live_store_guard import non_service_context
        if non_service_context():
            return None
    except Exception:  # noqa: BLE001
        pass
    return os.path.join(os.path.expanduser("~"), ".zoe", "memory-reject-ledger.json")


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _load(path: str | None) -> dict[str, dict[str, int]]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — missing / unreadable → start clean
        return {}


def _save(path: str | None, data: dict[str, dict[str, int]]) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory_reject_ledger: persist skipped: %s", exc)


def _clean(label: str) -> str:
    """Counts are keyed by short stable labels; never let free text or a separator in."""
    return "".join(ch for ch in str(label or "unknown").lower() if ch.isalnum() or ch in "_-:.")[:48] or "unknown"


_FLUSH_EVERY_S = 30.0
_last_flush = 0.0


def _flush_locked(path: str | None, *, force: bool = False) -> None:
    """Merge the in-memory delta into the file (caller holds ``_LOCK``). Rate-limited so a
    burst of dedup skips is one write, not hundreds; a crash loses at most one window."""
    global _last_flush
    if not path or not _MEM:
        return
    now = time.monotonic()
    if not force and now - _last_flush < _FLUSH_EVERY_S:
        return
    data = _load(path)
    for day, counts in _MEM.items():
        bucket = data.setdefault(day, {})
        for key, n in counts.items():
            bucket[key] = int(bucket.get(key, 0)) + int(n)
    for old in sorted(data)[:-_KEEP_DAYS]:
        data.pop(old, None)
    _save(path, data)
    _MEM.clear()
    _last_flush = now


def record_reject(source: str, reason: str) -> None:
    """Count one refused candidate. ``source`` = the writer (digest, idle_consolidation,
    voice_fact, ...), ``reason`` = the gate's stable label (question_mark, pii_reject,
    tombstone_drop, opt_out, dedup, ...). Never raises."""
    try:
        key = f"{_clean(source)}|{_clean(reason)}"
        with _LOCK:
            bucket = _MEM.setdefault(_today(), {})
            bucket[key] = int(bucket.get(key, 0)) + 1
            _flush_locked(_path())
        try:
            from memory_metrics import memory_quality_reject_count
            memory_quality_reject_count.labels(source=_clean(source), reason=_clean(reason)).inc()
        except Exception:  # noqa: BLE001 — metrics are optional
            pass
    except Exception:  # noqa: BLE001 — a bookkeeping failure must never block a write path
        pass


def summary(days: int = 1) -> dict:
    """Totals over the last ``days`` UTC days (today included): ``{"rejected", "reasons",
    "sources"}``. Counts only."""
    path = _path()
    with _LOCK:
        _flush_locked(path, force=True)
        data = _load(path) if path else {d: dict(c) for d, c in _MEM.items()}
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max(days, 1) - 1)
              ).strftime("%Y-%m-%d")
    reasons: Counter = Counter()
    sources: Counter = Counter()
    for day, counts in data.items():
        if day < cutoff:
            continue
        for key, n in counts.items():
            src, _, why = key.partition("|")
            reasons[why] += int(n)
            sources[src] += int(n)
    return {"rejected": sum(reasons.values()), "reasons": dict(reasons), "sources": dict(sources)}


def format_summary(days: int = 1) -> str:
    s = summary(days)

    def fmt(c: dict) -> str:
        return ",".join(f"{k}:{v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))) or "-"

    return (f"MEMORY_REJECT_SUMMARY window={days}d rejected={s['rejected']} "
            f"reasons={fmt(s['reasons'])} sources={fmt(s['sources'])}")


def log_nightly_summary(days: int = 1) -> str:
    """The nightly line. Emitted even when zero. Never raises."""
    try:
        line = format_summary(days)
        logger.info(line)
        return line
    except Exception:  # noqa: BLE001
        return ""


def reset_for_tests() -> None:
    global _last_flush
    with _LOCK:
        _MEM.clear()
        _last_flush = 0.0
