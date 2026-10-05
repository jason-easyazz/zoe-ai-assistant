"""Parsing and bounding of a restore's ``captured_at`` instant. Pure stdlib, no I/O.

Shared by ``MemoryService._build_metadata`` and ``scripts/maintenance/restore_memories_from_export.py`` so
the tool and the service agree on what a usable capture instant is. A value is usable when it parses as
ISO-8601 and is not more than ``FUTURE_SKEW`` ahead of now (a future date would pin a row to the top of
every ``added_at`` / ``added_ts`` recency ordering).
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

FUTURE_SKEW = datetime.timedelta(minutes=5)

OK = "ok"
EMPTY = "empty"
UNPARSEABLE = "unparseable"
FUTURE = "future_date"


def value_shape(value: Any) -> str:
    """A log-safe description of ``value``: digits -> ``d``, letters -> ``a``, length-capped. Never the value."""
    s = str(value)
    shape = "".join("d" if c.isdigit() else "a" if c.isalpha() else c for c in s[:40])
    return f"type={type(value).__name__} len={len(s)} shape={shape!r}"


def parse_iso_utc(value: Any) -> Optional[datetime.datetime]:
    """ISO-8601 -> NAIVE UTC datetime (offset-aware inputs converted, naive inputs taken as UTC), or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def parse_captured_at(value: Any, *, now: Optional[datetime.datetime] = None) -> tuple[Optional[datetime.datetime], str]:
    """``(naive-UTC datetime | None, reason)``; reason is ``ok``, ``empty``, ``unparseable`` or ``future_date``."""
    if value is None or not str(value).strip():
        return None, EMPTY
    parsed = parse_iso_utc(value)
    if parsed is None:
        return None, UNPARSEABLE
    current = now if now is not None else datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if parsed > current + FUTURE_SKEW:
        return None, FUTURE
    return parsed, OK
