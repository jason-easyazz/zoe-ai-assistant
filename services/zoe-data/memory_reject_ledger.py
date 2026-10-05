"""Write-time rejection ledger: every candidate the memory pipeline REFUSES is counted.

The audit trail records what was written (``ingest`` rows). It cannot show what was refused before a
write: the write-quality gate, the PII scrubber, a forget tombstone, an opt-out, a dedup skip. Until
this module the two highest-volume writers — the nightly digest and the idle consolidation — dropped
gate rejects with NO log line and NO counter (``memory_digest._passes_quality_gate`` and
``memory_idle_consolidation`` discarded the reason), the other sites logged the candidate TEXT at INFO,
and the Prometheus counter died with every restart. So "was this fact rejected, or never produced, or
lost?" had no answer (docs/knowledge/memory-loss-audit-2026-10-05.md, bucket b).

``record_reject(source, reason)`` is the one call every refusal site makes. It keeps COUNTS only — never
the text, never a user id — per UTC HOUR, in memory and in a small JSON file so a restart does not zero
the window. The nightly digest pass logs one line from it::

    MEMORY_REJECT_SUMMARY window=24h rejected=3 reasons=question_mark:2,weather_report:1 sources=digest:2,idle_consolidation:1

``window=24h`` is the last 24 hours ending at the run (not a UTC date: a date-bucket drops the band
between the previous run and the date change). ``rejected=0`` is logged too (silence must be
distinguishable from "nothing refused"), and ``persist_failed=1`` is appended when the counts could not
be written to disk — the in-memory delta is KEPT then, never cleared, so a failing disk cannot produce a
false zero.

Metrics: only genuine write-quality-gate rejects (``gate=True``, the default) feed
``zoe_memory_quality_reject_count``; MemoryService refusals (dedup / pii / opt_out / tombstone_drop) pass
``gate=False`` — they already have ``memory_write_count{status}`` and their own counters.

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
_KEEP_HOURS = 14 * 24
# "<UTC hour>" (YYYY-MM-DDTHH) -> {"<source>|<reason>": n}   — the not-yet-persisted delta
_MEM: dict[str, dict[str, int]] = {}
_FLUSH_EVERY_S = 30.0
_last_flush = 0.0
_persist_failed = False
_warned_persist = False


def _path() -> str | None:
    """Where the counters persist. ``ZOE_MEMORY_REJECT_LEDGER`` overrides (tests pin it); the default is
    ``~/.zoe/memory-reject-ledger.json``. A non-service process (pytest / harness) with NO explicit
    override never persists: it must not write the household's home."""
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


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _hour(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


def _load(path: str | None) -> dict[str, dict[str, int]]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — missing / unreadable → start clean
        return {}


def _save(path: str | None, data: dict[str, dict[str, int]]) -> bool:
    """True only when the file was durably replaced."""
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001
        global _warned_persist
        if not _warned_persist:
            _warned_persist = True
            logger.warning("memory_reject_ledger: cannot persist reject counts (%s) — keeping them in "
                           "memory; the nightly summary will say persist_failed=1", exc)
        return False


def _clean(label: str) -> str:
    """Counts are keyed by short stable labels; never let free text or a separator in."""
    return "".join(ch for ch in str(label or "unknown").lower() if ch.isalnum() or ch in "_-:.")[:48] or "unknown"


def _merge_into(data: dict[str, dict[str, int]], delta: dict[str, dict[str, int]]) -> None:
    for hour, counts in delta.items():
        bucket = data.setdefault(hour, {})
        for key, n in counts.items():
            bucket[key] = int(bucket.get(key, 0)) + int(n)


def _flush_locked(path: str | None, *, force: bool = False) -> None:
    """Merge the in-memory delta into the file (caller holds ``_LOCK``). Rate-limited so a burst of
    dedup skips is one write. The delta is cleared ONLY after a successful replace."""
    global _last_flush, _persist_failed
    if not path or not _MEM:
        return
    now = time.monotonic()
    if not force and now - _last_flush < _FLUSH_EVERY_S:
        return
    data = _load(path)
    _merge_into(data, _MEM)
    for old in sorted(data)[:-_KEEP_HOURS]:
        data.pop(old, None)
    if _save(path, data):
        _MEM.clear()
        _persist_failed = False
    else:
        _persist_failed = True
    _last_flush = now


def record_reject(source: str, reason: str, *, gate: bool = True) -> None:
    """Count one refused candidate. ``source`` = the writer (digest, idle_consolidation, voice_fact, ...),
    ``reason`` = the stable label (question_mark, pii_reject, tombstone_drop, opt_out, dedup, ...).
    ``gate`` = it was refused by the write-quality gate (feeds the gate's Prometheus counter; service
    refusals pass False). Never raises."""
    try:
        key = f"{_clean(source)}|{_clean(reason)}"
        with _LOCK:
            bucket = _MEM.setdefault(_hour(_now()), {})
            bucket[key] = int(bucket.get(key, 0)) + 1
            _flush_locked(_path())
        if gate:
            try:
                from memory_metrics import memory_quality_reject_count
                memory_quality_reject_count.labels(source=_clean(source), reason=_clean(reason)).inc()
            except Exception:  # noqa: BLE001 — metrics are optional
                pass
    except Exception:  # noqa: BLE001 — a bookkeeping failure must never block a write path
        pass


def summary(hours: int = 24) -> dict:
    """Totals over the last ``hours`` hours ending now (file + the unflushed in-memory delta):
    ``{"rejected", "reasons", "sources", "persist_failed"}``. Counts only."""
    path = _path()
    with _LOCK:
        _flush_locked(path, force=True)
        data = _load(path) if path else {}
        _merge_into(data, _MEM)     # empty after a successful flush; the pending delta otherwise
        failed = _persist_failed
    cutoff = _hour(_now() - datetime.timedelta(hours=max(hours, 1)))
    reasons: Counter = Counter()
    sources: Counter = Counter()
    for hour, counts in data.items():
        if hour < cutoff:           # whole-hour buckets: err on the side of 24-25 h, never drop a band
            continue
        for key, n in counts.items():
            src, _, why = key.partition("|")
            reasons[why] += int(n)
            sources[src] += int(n)
    return {"rejected": sum(reasons.values()), "reasons": dict(reasons), "sources": dict(sources),
            "persist_failed": failed}


def format_summary(hours: int = 24) -> str:
    s = summary(hours)

    def fmt(c: dict) -> str:
        return ",".join(f"{k}:{v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))) or "-"

    line = (f"MEMORY_REJECT_SUMMARY window={hours}h rejected={s['rejected']} "
            f"reasons={fmt(s['reasons'])} sources={fmt(s['sources'])}")
    return line + " persist_failed=1" if s["persist_failed"] else line


def log_nightly_summary(hours: int = 24) -> str:
    """The nightly line. Emitted even when zero. Never raises."""
    try:
        line = format_summary(hours)
        logger.info(line)
        return line
    except Exception:  # noqa: BLE001
        return ""


def reset_for_tests() -> None:
    global _last_flush, _persist_failed, _warned_persist
    with _LOCK:
        _MEM.clear()
        _last_flush = 0.0
        _persist_failed = False
        _warned_persist = False
