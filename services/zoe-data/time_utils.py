"""Shared timezone helpers for Zoe runtime services."""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZOE_TIMEZONE = "Australia/Perth"


def zoe_timezone() -> ZoneInfo:
    """Return Zoe's configured timezone, falling back to the canonical default."""
    name = (os.environ.get("ZOE_TIMEZONE") or DEFAULT_ZOE_TIMEZONE).strip() or DEFAULT_ZOE_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZOE_TIMEZONE)


def today_for_zoe_tz(now: datetime | None = None) -> date:
    """Return today's date in Zoe's configured timezone.

    `now` is injectable so tests and follow-up call sites can verify boundaries
    without relying on the host timezone.
    """
    tz = zoe_timezone()
    if now is None:
        return datetime.now(tz).date()
    if now.tzinfo is None:
        return now.replace(tzinfo=tz).date()
    return now.astimezone(tz).date()
