import pytest
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from time_utils import today_for_zoe_tz

pytestmark = pytest.mark.ci_safe


def test_today_for_zoe_tz_uses_configured_timezone_not_server_timezone(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")

    utc_now = datetime(2026, 6, 28, 23, 0, tzinfo=timezone.utc)

    assert today_for_zoe_tz(utc_now).isoformat() == "2026-06-29"


def test_today_for_zoe_tz_falls_back_to_perth_for_invalid_timezone(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Not/A_Zone")

    utc_now = datetime(2026, 6, 28, 23, 0, tzinfo=timezone.utc)

    assert today_for_zoe_tz(utc_now).isoformat() == "2026-06-29"


def test_today_for_zoe_tz_falls_back_for_path_like_timezone(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "/usr/share/zoneinfo/Australia/Perth")

    utc_now = datetime(2026, 6, 28, 23, 0, tzinfo=timezone.utc)

    assert today_for_zoe_tz(utc_now).isoformat() == "2026-06-29"
