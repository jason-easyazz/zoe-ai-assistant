from __future__ import annotations

from datetime import date as real_date
from datetime import datetime as real_datetime
from datetime import timedelta, timezone

import pytest

import person_health
from person_health import _next_occurrence, calc_health_score


FROZEN_NOW = real_datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)
FROZEN_TODAY = FROZEN_NOW.date()


class FrozenDate(real_date):
    @classmethod
    def today(cls) -> real_date:
        return FROZEN_TODAY


class FrozenDateTime(real_datetime):
    @classmethod
    def now(cls, tz=None) -> real_datetime:
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)

    @classmethod
    def fromisoformat(cls, value: str) -> real_datetime:
        return real_datetime.fromisoformat(value)


@pytest.fixture(autouse=True)
def freeze_person_health_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(person_health, "date", FrozenDate)
    monkeypatch.setattr(person_health, "datetime", FrozenDateTime)


class TestNextOccurrence:
    def test_returns_future_occurrence_in_same_year(self) -> None:
        assert _next_occurrence(12, 25, ref=real_date(2026, 6, 18)) == real_date(2026, 12, 25)

    def test_past_date_rolls_to_next_year(self) -> None:
        assert _next_occurrence(1, 1, ref=real_date(2026, 6, 18)) == real_date(2027, 1, 1)

    def test_february_29_falls_back_to_28_on_non_leap_year(self) -> None:
        assert _next_occurrence(2, 29, ref=real_date(2025, 1, 10)) == real_date(2025, 2, 28)


class TestCalcHealthScore:
    def test_no_contact_defaults_to_low_recency(self) -> None:
        score = calc_health_score(None, contact_count=0, circle="inner", context="personal")

        assert 0.0 <= score <= 1.0
        assert score < 0.1

    def test_recent_contact_timestamp_scores_higher_than_no_contact(self) -> None:
        recent = (FROZEN_NOW - timedelta(days=1)).isoformat()

        recent_score = calc_health_score(recent, contact_count=5, circle="inner", context="personal")
        no_contact_score = calc_health_score(None, contact_count=5, circle="inner", context="personal")

        assert 0.0 <= recent_score <= 1.0
        assert recent_score > no_contact_score

    def test_invalid_timestamp_falls_back_to_365_days_without_exception(self) -> None:
        fallback_score = calc_health_score(None, contact_count=7, circle="circle", context="personal")
        invalid_score = calc_health_score("not-a-real-timestamp", contact_count=7, circle="circle", context="personal")

        assert invalid_score == fallback_score

    def test_frequency_term_saturates_as_contact_count_grows(self) -> None:
        low = calc_health_score(None, contact_count=1, circle="public", context="personal")
        medium = calc_health_score(None, contact_count=10, circle="public", context="personal")
        saturated = calc_health_score(None, contact_count=50, circle="public", context="personal")
        above_cap = calc_health_score(None, contact_count=5_000, circle="public", context="personal")

        assert low < medium < saturated
        assert saturated == above_cap

    def test_birthday_boost_applies_only_inside_zero_to_fourteen_day_window(self) -> None:
        baseline = calc_health_score(None, contact_count=0, circle="public", context="personal")
        boosted = calc_health_score(
            None,
            contact_count=0,
            circle="public",
            next_birthday=FROZEN_TODAY + timedelta(days=14),
            context="personal",
        )
        outside_window = calc_health_score(
            None,
            contact_count=0,
            circle="public",
            next_birthday=FROZEN_TODAY + timedelta(days=15),
            context="personal",
        )

        assert boosted == pytest.approx(baseline + 0.3, abs=1e-9)
        assert outside_window == baseline

    def test_context_tier_and_legacy_half_life_lookups_are_used(self) -> None:
        fourteen_days_ago = (FROZEN_NOW - timedelta(days=14)).isoformat()

        personal_inner = calc_health_score(fourteen_days_ago, contact_count=0, circle="inner", context="personal")
        legacy_inner = calc_health_score(fourteen_days_ago, contact_count=0, circle="inner")
        work_inner = calc_health_score(fourteen_days_ago, contact_count=0, circle="inner", context="work")

        assert personal_inner == legacy_inner
        assert work_inner > personal_inner

    def test_scores_stay_in_bounds(self) -> None:
        scores = [
            calc_health_score(None, contact_count=0, circle="public", context="personal"),
            calc_health_score(
                (FROZEN_NOW - timedelta(days=45)).isoformat(),
                contact_count=3,
                circle="circle",
                context="personal",
            ),
            calc_health_score(
                FROZEN_NOW.isoformat(),
                contact_count=5_000,
                circle="inner",
                next_birthday=FROZEN_TODAY,
                context="personal",
            ),
        ]

        assert all(0.0 <= score <= 1.0 for score in scores)

    def test_score_is_capped_at_one(self) -> None:
        score = calc_health_score(
            FROZEN_NOW.isoformat(),
            contact_count=5_000,
            circle="inner",
            next_birthday=FROZEN_TODAY,
            context="personal",
        )

        assert score == 1.0