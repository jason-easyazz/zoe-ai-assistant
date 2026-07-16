import pytest
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import intent_router
from intent_router import Intent

pytestmark = pytest.mark.ci_safe


def test_calendar_show_fallback_date_arithmetic_uses_zoe_today(monkeypatch):
    monkeypatch.setattr(intent_router, "today_for_zoe_tz", lambda: date(2026, 6, 29))

    tomorrow = intent_router._build_command(Intent("calendar_show", {"qualifier": "tomorrow"}), "jason")
    this_week = intent_router._build_command(Intent("calendar_show", {"qualifier": "this week"}), "jason")
    default_range = intent_router._build_command(Intent("calendar_show", {"qualifier": ""}), "jason")

    assert "start_date=2026-06-30 end_date=2026-06-30" in tomorrow
    assert "start_date=2026-06-29 end_date=2026-07-05" in this_week
    assert "start_date=2026-06-29 end_date=2026-07-06" in default_range


def test_spoken_day_uses_zoe_today(monkeypatch):
    monkeypatch.setattr(intent_router, "today_for_zoe_tz", lambda: date(2026, 6, 29))

    assert intent_router._spoken_day("2026-06-29") == "today"
    assert intent_router._spoken_day("2026-06-30") == "tomorrow"
