"""The morning check-in fires 07:30-08:30 local (as its comment always said), in the
timezone read PER CALL from ``time_utils.zoe_timezone()``.

Found 2026-10-12: the test was ``7 <= hour < 8 and minute >= 30`` = 07:30-07:59 only, and
the timezone was read once at import from the raw env. ``check()`` reaches the DB only
after the window gate, so a sentinel DB tells us whether a time passed the gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import proactive.triggers.morning_checkin as mc

pytestmark = pytest.mark.ci_safe


class _Reached(Exception):
    pass


class _DB:
    def execute(self, *_a, **_k):
        raise _Reached


def _freeze(monkeypatch, instant_utc: datetime):
    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant_utc.astimezone(tz) if tz else instant_utc

    monkeypatch.setattr(mc, "datetime", _FrozenDT)


async def _passes_window(monkeypatch, instant_utc: datetime) -> bool:
    _freeze(monkeypatch, instant_utc)
    try:
        await mc.MorningCheckInTrigger().check(_DB())
    except _Reached:
        return True
    return False


def _perth(h, m):
    return datetime(2026, 10, 12, h, m, tzinfo=ZoneInfo("Australia/Perth")).astimezone(timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("h,m,expected", [
    (7, 29, False), (7, 30, True), (7, 59, True),
    (8, 0, True), (8, 29, True),          # the second half of the documented window
    (8, 30, False), (9, 0, False), (3, 45, False),
])
async def test_window_is_0730_to_0830_local(monkeypatch, h, m, expected):
    monkeypatch.delenv("ZOE_TIMEZONE", raising=False)
    assert await _passes_window(monkeypatch, _perth(h, m)) is expected


@pytest.mark.asyncio
async def test_timezone_is_read_per_call_not_at_import(monkeypatch):
    # 07:45 in Sydney is 05:45 in Perth: only passes if ZOE_TIMEZONE set AFTER import is honoured.
    instant = datetime(2026, 10, 12, 7, 45, tzinfo=ZoneInfo("Australia/Sydney")).astimezone(timezone.utc)
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    assert await _passes_window(monkeypatch, instant) is False
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Sydney")
    assert await _passes_window(monkeypatch, instant) is True


class _Capture:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        raise _Reached


@pytest.mark.asyncio
async def test_once_per_day_dedup_keys_on_the_local_date_not_utc(monkeypatch):
    """08:05 Perth is already the NEXT UTC date; a UTC `CURRENT_DATE` dedup forgot the 07:35 brief
    and fired a second one (cross-review of this PR, B1)."""
    monkeypatch.delenv("ZOE_TIMEZONE", raising=False)
    _freeze(monkeypatch, _perth(8, 5))
    db = _Capture()
    with pytest.raises(_Reached):
        await mc.MorningCheckInTrigger().check(db)
    sql, params = db.calls[0]
    assert "CURRENT_DATE" not in sql and "AT TIME ZONE" in sql
    assert "claimed" not in sql                       # a claimed brief was still sent today
    assert params == ("morning_checkin", "Australia/Perth", "2026-10-12")

