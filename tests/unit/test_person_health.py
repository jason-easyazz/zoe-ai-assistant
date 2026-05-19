"""Unit tests for person_health.py — calc_health_score() determinism and edge cases."""

import sys
import os
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add services/zoe-data to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/zoe-data'))

from person_health import calc_health_score, _next_occurrence


class TestNextOccurrence:
    def test_future_this_year(self):
        today = date.today()
        future = today + timedelta(days=10)
        result = _next_occurrence(future.month, future.day, ref=today)
        assert result == future

    def test_past_wraps_to_next_year(self):
        today = date.today()
        past = today - timedelta(days=10)
        result = _next_occurrence(past.month, past.day, ref=today)
        assert result.year == today.year + 1 or result == today
        assert result > today or result == today


class TestCalcHealthScore:
    def test_personal_inner_contacted_yesterday_is_high(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        score = calc_health_score(yesterday, contact_count=20, circle='inner', context='personal')
        assert score >= 0.6, f"Expected high score, got {score}"

    def test_personal_inner_no_contact_60_days_is_low(self):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        score = calc_health_score(old, contact_count=5, circle='inner', context='personal')
        assert score < 0.3, f"Expected low score, got {score}"

    def test_personal_public_no_contact_30_days_is_reasonable(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        score = calc_health_score(old, contact_count=2, circle='public', context='personal')
        # public half-life is 90 days — 30 days should be moderate
        assert 0.1 < score < 0.9

    def test_work_inner_has_separate_halflife(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
        personal_score = calc_health_score(ts, 10, 'inner', context='personal')  # half-life=14
        work_score = calc_health_score(ts, 10, 'inner', context='work')          # half-life=21
        assert work_score > personal_score, "Work inner half-life is longer (21d vs 14d)"

    def test_work_public_longer_halflife(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        personal = calc_health_score(ts, 2, 'public', context='personal')   # half-life=90
        work = calc_health_score(ts, 2, 'public', context='work')            # half-life=120
        assert work > personal, "Work public should decay slower"

    def test_birthday_boost_applied(self):
        yesterday_contact = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        today = date.today()
        next_bday = today + timedelta(days=7)
        score_with = calc_health_score(yesterday_contact, 10, 'circle', next_bday, 'personal')
        score_without = calc_health_score(yesterday_contact, 10, 'circle', None, 'personal')
        assert score_with >= score_without, "Birthday boost should not decrease score"

    def test_birthday_too_far_no_boost(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        far_bday = date.today() + timedelta(days=30)
        score_with = calc_health_score(yesterday, 10, 'circle', far_bday, 'personal')
        score_without = calc_health_score(yesterday, 10, 'circle', None, 'personal')
        assert abs(score_with - score_without) < 0.01, "No boost expected for birthday > 14 days away"

    def test_no_contact_defaults_365_days(self):
        score = calc_health_score(None, contact_count=0, circle='inner', context='personal')
        assert score < 0.1, f"No contact inner should be very low, got {score}"

    def test_score_is_deterministic(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        s1 = calc_health_score(ts, 15, 'circle', context='personal')
        s2 = calc_health_score(ts, 15, 'circle', context='personal')
        assert s1 == s2

    def test_score_capped_at_1(self):
        now = datetime.now(timezone.utc).isoformat()
        score = calc_health_score(now, contact_count=1000, circle='inner',
                                  next_birthday=date.today(), context='personal')
        assert score <= 1.0

    def test_score_non_negative(self):
        score = calc_health_score(None, contact_count=0, circle='public', context='personal')
        assert score >= 0.0

    def test_malformed_date_treated_as_365_days(self):
        score = calc_health_score("not-a-date", contact_count=5, circle='circle', context='personal')
        assert score < 0.2, "Malformed date should behave like 365 days ago"

    def test_legacy_circle_values_still_work(self):
        """Legacy single-dim circle values fall back gracefully."""
        ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        score = calc_health_score(ts, 5, 'friends')  # no context → defaults to personal
        assert 0.0 <= score <= 1.0
