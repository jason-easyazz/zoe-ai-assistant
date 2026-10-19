import pytest
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import intent_router
from intent_router import Intent

pytestmark = pytest.mark.ci_safe


def test_calendar_show_date_arithmetic_uses_zoe_today(monkeypatch):
    import asyncio
    import contextlib

    monkeypatch.setattr(intent_router, "today_for_zoe_tz", lambda: date(2026, 6, 29))
    seen = []

    class _Cursor:
        async def fetchall(self):
            return []

    class _Db:
        async def execute(self, _sql, params):
            seen.append(params[1:])
            return _Cursor()

    @contextlib.asynccontextmanager
    async def ctx():
        yield _Db()

    monkeypatch.setattr("database.get_db_ctx", ctx)
    for qualifier in ("tomorrow", "this week", "next fortnight"):
        asyncio.run(intent_router._execute_calendar_show_direct(Intent("calendar_show", {"qualifier": qualifier}), "jason"))

    assert seen == [
        ("2026-06-30", "2026-06-30"),
        ("2026-06-29", "2026-07-05"),
        ("2026-06-29", "2026-07-06"),
    ]


def test_spoken_day_uses_zoe_today(monkeypatch):
    monkeypatch.setattr(intent_router, "today_for_zoe_tz", lambda: date(2026, 6, 29))

    assert intent_router._spoken_day("2026-06-29") == "today"
    assert intent_router._spoken_day("2026-06-30") == "tomorrow"
