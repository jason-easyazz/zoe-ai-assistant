"""Rate-limited logging for failures that repeat on a poll loop.

Why this exists (2026-10-04 log review, ``docs/knowledge/log-review-2026-10-04.md``):
a kiosk polls ``/api/ha/entities`` and the panel-config resolver every few
seconds. When the Home Assistant bridge stalled for ~3 hours, every poll logged
a full ~5 KB traceback (``logger.exception`` / ``exc_info=True``) for an
*expected, self-healing* condition: 121 + 20 tracebacks in one outage, each
written twice (app log + stderr). The traceback of an ``httpx.ReadTimeout``
says nothing the exception class does not, and an outage drowns the one line
that matters under thousands that repeat it.

The class fix is two-part and lives here so call sites stay one line:

* **Upstream-transport failures** (timeouts, connection errors, upstream HTTP
  status errors) log ONE line — exception class + short message, no traceback.
  Anything else is a genuine bug and keeps its full traceback.
* **Repeats are throttled** per ``key``: the first occurrence logs at once, then
  at most one line per ``ZOE_LOG_REPEAT_WINDOW_S`` (default 60 s), carrying the
  count it suppressed — so an outage is still visible as "N more", never silent.

Stdlib + httpx only; safe to import from anywhere.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

import httpx

__all__ = ["log_throttled", "log_upstream_failure", "is_upstream_transport_error"]

DEFAULT_WINDOW_S = 60.0
_MAX_KEYS = 256  # a key space this large means callers are building keys from data

_lock = threading.Lock()
# key -> [last_emit_monotonic, suppressed_since_last_emit]
_state: dict[str, list[float]] = {}


def _window_s() -> float:
    raw = os.environ.get("ZOE_LOG_REPEAT_WINDOW_S")
    if not raw:
        return DEFAULT_WINDOW_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_S
    return value if value >= 0 else DEFAULT_WINDOW_S


def log_throttled(
    logger: logging.Logger,
    level: int,
    key: str,
    msg: str,
    *args: object,
    window_s: float | None = None,
) -> bool:
    """Log ``msg % args`` unless ``key`` already logged within the window.

    Returns True when a record was emitted, False when it was suppressed. A
    window of 0 disables throttling. When a line is emitted after suppression,
    ``" (+N similar suppressed)"`` is appended so the repeat count survives.
    """
    window = _window_s() if window_s is None else window_s
    now = time.monotonic()
    suppressed = 0
    with _lock:
        entry = _state.get(key)
        if entry is not None and window > 0 and (now - entry[0]) < window:
            entry[1] += 1
            return False
        if entry is not None:
            suppressed = int(entry[1])
        if len(_state) >= _MAX_KEYS and key not in _state:
            _state.pop(next(iter(_state)))  # oldest-inserted; dicts keep insertion order
        _state[key] = [now, 0]
    if suppressed:
        logger.log(level, msg + " (+%d similar suppressed)", *args, suppressed)
    else:
        logger.log(level, msg, *args)
    return True


def is_upstream_transport_error(exc: BaseException) -> bool:
    """True for the failures a dependency outage produces (not programming errors)."""
    return isinstance(
        exc,
        (
            httpx.TransportError,  # ConnectError, ReadTimeout, PoolTimeout, ...
            httpx.HTTPStatusError,
            asyncio.TimeoutError,
            ConnectionError,
            TimeoutError,
        ),
    )


def log_upstream_failure(
    logger: logging.Logger,
    what: str,
    exc: BaseException,
    *,
    key: str | None = None,
    level: int = logging.WARNING,
) -> None:
    """Log a failed call to a dependency without a traceback storm.

    Transport/status failures: one throttled line ``"<what>: <ExcClass>: <msg>"``.
    Anything else: a normal ``ERROR`` with the full traceback (a real bug must
    never be throttled into silence).
    """
    if is_upstream_transport_error(exc):
        detail = str(exc).strip().replace("\n", " ")[:200]
        log_throttled(
            logger,
            level,
            key or what,
            "%s: %s%s",
            what,
            type(exc).__name__,
            f": {detail}" if detail else "",
        )
        return
    logger.error("%s", what, exc_info=(type(exc), exc, exc.__traceback__))
