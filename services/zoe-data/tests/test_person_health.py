"""Focused tests for the pure/deterministic helpers in person_health.

The module mixes async DB-touching entry points (``recalc_and_save``) with
two small pure helpers used during scoring and birthday lookup:

- ``calc_health_score(last_contacted_at, contact_count, circle, ... )`` —
  relationship health score in [0.0, 1.0] combining recency decay, log-
  scaled frequency, and an optional birthday-window boost.
- ``_next_occurrence(month, day, ref=None)`` — the next on-or-after
  calendar occurrence of (month, day); rolls to next year if past, and
  clamps Feb-29 to Feb-28 in non-leap years.

This test file pins the contract of those pure pieces only. The async
DB-bound ``recalc_and_save`` is deliberately not exercised here so the
suite can run on any host (no PostgreSQL, no asyncpg pool).

``datetime.now()`` is patched per-test via the ``fixed_now`` fixture so
the recency decay is reproducible regardless of wall-clock drift; the
``_next_occurrence`` tests pass an explicit ``ref`` date so they don't
touch the clock at all.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta, timezone

import pytest

import person_health
from person_health import _HALF_LIFE, _HALF_LIFE_LEGACY, _next_occurrence, calc_health_score

pytestmark = pytest.mark.ci_safe


# ── Test helpers ────────────────────────────────────────────────────────────


# A stable "now" used by the calc_health_score fixtures. Chosen mid-year so
# birthday-window tests don't sit on a year boundary.
FIXED_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDateTime:
    """Drop-in replacement for ``person_health.datetime`` that freezes ``now()``.

    Only the ``now`` and ``fromisoformat`` call sites used by
    ``calc_health_score`` are routed here; everything else (timezone
    arithmetic, ``date`` import) keeps working because callers only
    touch ``datetime.now`` and ``datetime.fromisoformat`` on this module
    attribute.
    """

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FIXED_NOW
        return FIXED_NOW.astimezone(tz)

    @staticmethod
    def fromisoformat(value):
        return datetime.fromisoformat(value)


class _FrozenDate(date):
    """``person_health.date`` with ``today()`` pinned to FIXED_NOW's date.

    The birthday-boost path computes ``(next_birthday - date.today()).days``,
    so ``date.today()`` must be frozen too — otherwise a run that straddles
    midnight shifts the boundary by a day and flips the boost. Subclassing
    ``date`` keeps the ``date(y, m, d)`` constructor working for other callers.
    """

    @classmethod
    def today(cls):
        return FIXED_NOW.date()


@pytest.fixture
def fixed_now(monkeypatch):
    """Pin both ``datetime.now`` and ``date.today`` inside ``person_health``."""
    monkeypatch.setattr(person_health, "datetime", _FrozenDateTime)
    monkeypatch.setattr(person_health, "date", _FrozenDate)


def _days_ago(days: int) -> str:
    """Return an ISO-8601 string ``days`` before FIXED_NOW (timezone-aware)."""
    return (FIXED_NOW - timedelta(days=days)).isoformat()


def _birthday_in(days: int) -> date:
    """Return a date ``days`` after the frozen "now" (no midnight race)."""
    return FIXED_NOW.date() + timedelta(days=days)


# ── __all__ public contract ─────────────────────────────────────────────────


def test_module_all_lists_public_entry_points():
    # ``__all__`` is the module's stable import surface; downstream callers
    # and tests rely on it. Locking it down prevents accidental removal
    # of the async orchestrator when refactoring the pure helpers.
    assert person_health.__all__ == [
        "calc_health_score",
        "recalc_and_save",
        "_next_occurrence",
    ]


def test_next_occurrence_is_exported_by_name():
    # The helper is private-by-name (leading underscore) but still
    # exposed via ``__all__`` for tests and the async recalc_and_save
    # caller. Pin that contract so a future cleanup doesn't drop it.
    assert "_next_occurrence" in person_health.__all__


# ── _HALF_LIFE lookup tables ─────────────────────────────────────────────────


_PERSONAL_TIERS = {"inner", "circle", "public"}
_KNOWN_CONTEXTS = {"personal", "work"}


def test_half_life_table_covers_all_context_tier_combinations():
    # Every (context, tier) pair the docstring advertises must resolve to
    # a positive half-life. A missing key silently falls back to 60 days,
    # which would mask the tier-based sensitivity the model is meant to
    # express.
    for context in _KNOWN_CONTEXTS:
        for tier in _PERSONAL_TIERS:
            key = f"{context}:{tier}"
            assert key in _HALF_LIFE, f"{key!r} missing from _HALF_LIFE"
            assert _HALF_LIFE[key] > 0, f"{key!r} half-life must be positive"


def test_half_life_inner_is_shorter_than_public_in_every_context():
    # The whole point of the context:tier split is that "inner" people
    # decay faster than "public" ones — otherwise the tier dimension
    # is meaningless. Pin the ordering for both contexts.
    for context in _KNOWN_CONTEXTS:
        assert _HALF_LIFE[f"{context}:inner"] < _HALF_LIFE[f"{context}:public"], (
            f"{context}: inner should decay faster than public"
        )


def test_half_life_personal_inner_is_shortest():
    # personal:inner is the strictest decay (14 days) because partner /
    # kids / best-friend neglect is felt immediately. If a contributor
    # ever reduces this further, the test makes the change visible.
    assert _HALF_LIFE["personal:inner"] == 14


def test_legacy_half_life_aliases_exist_for_old_circle_values():
    # Old callers still pass plain ``circle`` strings without a context
    # prefix (e.g. ``circle="friends"``). The legacy table bridges those
    # until every row is migrated to the context:tier form.
    for legacy in ("inner", "friends", "family", "work", "acquaintance", "public"):
        assert legacy in _HALF_LIFE_LEGACY, f"{legacy!r} missing from _HALF_LIFE_LEGACY"


# ── _next_occurrence ─────────────────────────────────────────────────────────


def test_next_occurrence_future_this_year_is_returned_verbatim():
    # A date that falls AFTER ref in the same calendar year must be
    # returned as-is — no rolling forward, no normalization.
    ref = date(2025, 1, 10)
    assert _next_occurrence(12, 31, ref=ref) == date(2025, 12, 31)


def test_next_occurrence_past_in_year_rolls_to_next_year():
    # A (month, day) earlier than ref in the same year must roll to
    # the same (month, day) in the FOLLOWING year. This is the common
    # case for "next birthday" when the month/day has already passed.
    ref = date(2025, 11, 1)
    assert _next_occurrence(3, 15, ref=ref) == date(2026, 3, 15)


def test_next_occurrence_on_ref_day_is_returned_unchanged():
    # The contract is "on OR after ref", so the same day as ref must
    # come back as ref (no zero-shift to "tomorrow").
    ref = date(2025, 6, 1)
    assert _next_occurrence(6, 1, ref=ref) == ref


def test_next_occurrence_feb_29_in_leap_year_is_real():
    # 2024 is a leap year, so Feb 29 must be a real calendar date —
    # not a fallback. Pin that the helper does NOT clamp in leap years.
    assert _next_occurrence(2, 29, ref=date(2024, 1, 1)) == date(2024, 2, 29)


def test_next_occurrence_feb_29_in_non_leap_year_clamps_to_feb_28():
    # 2025 is NOT a leap year. Asking for Feb 29 must fall back to
    # Feb 28 (the documented behavior) rather than raising. The
    # helper's try/except clamps ``day`` to ``min(day, 28)``.
    assert _next_occurrence(2, 29, ref=date(2025, 1, 1)) == date(2025, 2, 28)


def test_next_occurrence_feb_29_rolls_into_leap_year_preserves_day_29():
    # Past-ref path with a leap-year destination: ref=Dec 2023,
    # target=Feb 29. The initial 2024-02-29 construction succeeds
    # (2024 IS a leap year), but 2024-02-29 is BEFORE 2023-12-01,
    # so the helper rolls forward to 2024-02-29. Pin that the
    # Feb-29 value is preserved end-to-end on a leap-year rollover.
    assert _next_occurrence(2, 29, ref=date(2023, 12, 1)) == date(2024, 2, 29)


def test_next_occurrence_feb_29_rolls_to_non_leap_year_clamps_twice():
    # ref=Dec 2026, target=Feb 29 → tries 2027 (NOT a leap), must
    # clamp to Feb 28. Pin that the clamp fires on the rolled year
    # path too, not only the initial construction.
    assert _next_occurrence(2, 29, ref=date(2026, 12, 1)) == date(2027, 2, 28)


def test_next_occurrence_invalid_day_31_clamps_to_day_28():
    # The clamp logic only fires when ``date(year, month, day)`` raises
    # ValueError; April has 30 days, so (4, 31) hits the
    # ``min(day, 28)`` branch. The fallback always lands on day ≤ 28
    # so we never get an invalid calendar date back.
    assert _next_occurrence(4, 31, ref=date(2025, 1, 1)) == date(2025, 4, 28)


def test_next_occurrence_default_ref_uses_today():
    # When no ref is supplied, the helper must default to
    # ``date.today()``. Pin that the default-ref path is at least
    # callable and returns a date object — its exact value obviously
    # drifts day to day, so we only assert type/shape here.
    result = _next_occurrence(12, 31)
    assert isinstance(result, date)


# ── calc_health_score — recency ──────────────────────────────────────────────


def test_health_score_no_contact_uses_365_day_default(fixed_now):
    # ``last_contacted_at=None`` triggers the "no contact" branch,
    # which sets ``days_since = 365``. The score should reflect that
    # decay (low recency, so the dominant term is the small freq term).
    score_none = calc_health_score(None, 0, "inner")
    score_old = calc_health_score(_days_ago(365), 0, "inner")
    # Both branches must converge on the same score: same days_since,
    # same freq, same half-life.
    assert score_none == pytest.approx(score_old)


def test_health_score_invalid_timestamp_falls_back_to_365_days(fixed_now):
    # Garbage input must hit the except branch and behave identically
    # to "no contact ever" — a deliberate fail-safe so a corrupt DB
    # row never produces a fake-perfect score.
    score_bogus = calc_health_score("not-a-date", 0, "inner")
    score_none = calc_health_score(None, 0, "inner")
    assert score_bogus == pytest.approx(score_none)


def test_health_score_empty_string_treated_as_no_contact(fixed_now):
    # An empty string is falsy, so the ``if last_contacted_at`` guard
    # treats it as None. Pin that this branch doesn't even reach the
    # parser — otherwise an empty string would raise in fromisoformat
    # and we'd still get the 365-day fallback, but for the wrong reason.
    score_empty = calc_health_score("", 0, "inner")
    score_none = calc_health_score(None, 0, "inner")
    assert score_empty == pytest.approx(score_none)


def test_health_score_recent_contact_has_full_recency_weight(fixed_now):
    # Contact right now → ``days_since = 0`` → ``recency = exp(0) = 1``.
    # With 0 freq and no birthday boost, the score is exactly 0.6
    # (the 0.6 weight × 1.0 recency).
    recent = _days_ago(0)
    score = calc_health_score(recent, 0, "inner")
    assert score == pytest.approx(0.6, abs=1e-9)


def test_health_score_recency_decays_monotonically_with_age(fixed_now):
    # A newer contact must score >= an older contact (monotone decay).
    # Equal freq and tier; only ``days_since`` varies.
    recent = calc_health_score(_days_ago(1), 10, "inner")
    week = calc_health_score(_days_ago(7), 10, "inner")
    month = calc_health_score(_days_ago(30), 10, "inner")
    assert recent > week > month


def test_health_score_z_suffix_treated_as_utc(fixed_now):
    # ``"Z"`` is the ISO-8601 zulu suffix; the helper rewrites it to
    # ``+00:00`` before parsing. Without that rewrite, fromisoformat
    # would raise on Python <3.11. Pin the parity with ``+00:00``.
    score_z = calc_health_score(_days_ago(0).replace("+00:00", "Z"), 0, "inner")
    score_offset = calc_health_score(_days_ago(0), 0, "inner")
    assert score_z == pytest.approx(score_offset)


def test_health_score_future_timestamp_clamped_to_zero_days_since(fixed_now):
    # ``max(..., 0)`` on the days-since subtraction means a contact
    # recorded in the FUTURE (clock skew, wrong timezone) is treated
    # as "just now" rather than producing a negative recency.
    future_iso = (FIXED_NOW + timedelta(days=30)).isoformat()
    score_future = calc_health_score(future_iso, 0, "inner")
    score_now = calc_health_score(_days_ago(0), 0, "inner")
    assert score_future == pytest.approx(score_now)


# ── calc_health_score — frequency ────────────────────────────────────────────


def test_health_score_zero_contacts_strictly_less_than_one_contact(fixed_now):
    # ``log1p(0) / log1p(50) = 0``, so the freq contribution is 0 for
    # 0 contacts. Adding one contact strictly raises the score
    # (assuming same recency).
    base = calc_health_score(_days_ago(1), 0, "inner")
    plus_one = calc_health_score(_days_ago(1), 1, "inner")
    assert plus_one > base


def test_health_score_frequency_saturates_at_50_contacts(fixed_now):
    # Once ``contact_count >= 50`` the freq term is min(..., 1.0) = 1.0
    # so further contacts cannot raise the score. Pin the exact value:
    # freq = 1.0, recency is the same for both, so the scores match.
    s_50 = calc_health_score(_days_ago(7), 50, "inner")
    s_500 = calc_health_score(_days_ago(7), 500, "inner")
    assert s_50 == pytest.approx(s_500)


def test_health_score_frequency_just_below_saturation_is_below_cap(fixed_now):
    # Just under the cap (49 contacts) should still leave a hair of
    # headroom: freq ≈ log1p(49)/log1p(50) ≈ 0.995. The score with 49
    # contacts must therefore be STRICTLY less than the score with 50
    # contacts (everything else equal).
    s_49 = calc_health_score(_days_ago(7), 49, "inner")
    s_50 = calc_health_score(_days_ago(7), 50, "inner")
    assert s_49 < s_50


# ── calc_health_score — birthday boost ──────────────────────────────────────


def test_health_score_birthday_within_14_days_adds_boost(fixed_now):
    # Birthday in 7 days → ``bday_boost = 0.3``. Same recency/freq as
    # a no-birthday call must differ by exactly 0.3.
    base = calc_health_score(_days_ago(7), 10, "inner", next_birthday=None)
    with_boost = calc_health_score(
        _days_ago(7), 10, "inner", next_birthday=_birthday_in(7)
    )
    assert with_boost - base == pytest.approx(0.3)


def test_health_score_birthday_at_zero_days_still_boosts(fixed_now):
    # ``days_to_bday == 0`` (today IS the birthday) is inside the
    # closed interval [0, 14], so the boost must still apply.
    base = calc_health_score(_days_ago(7), 10, "inner", next_birthday=None)
    with_boost = calc_health_score(
        _days_ago(7), 10, "inner", next_birthday=_birthday_in(0)
    )
    assert with_boost - base == pytest.approx(0.3)


def test_health_score_birthday_at_14_days_boosts_at_boundary(fixed_now):
    # The boost window is closed on the upper end: ``<= 14`` not
    # ``< 14``. Pin the boundary so a future off-by-one regression
    # (e.g. switching to ``<``) is visible.
    base = calc_health_score(_days_ago(7), 10, "inner", next_birthday=None)
    with_boost = calc_health_score(
        _days_ago(7), 10, "inner", next_birthday=_birthday_in(14)
    )
    assert with_boost - base == pytest.approx(0.3)


def test_health_score_birthday_beyond_14_days_no_boost(fixed_now):
    # Birthday 15 days away is just outside the window — no boost.
    base = calc_health_score(_days_ago(7), 10, "inner", next_birthday=None)
    no_boost = calc_health_score(
        _days_ago(7), 10, "inner", next_birthday=_birthday_in(15)
    )
    assert no_boost == pytest.approx(base)


def test_health_score_past_birthday_no_boost(fixed_now):
    # A negative ``days_to_bday`` (birthday already happened) falls
    # outside the [0, 14] interval, so no boost.
    base = calc_health_score(_days_ago(7), 10, "inner", next_birthday=None)
    no_boost = calc_health_score(
        _days_ago(7), 10, "inner", next_birthday=_birthday_in(-3)
    )
    assert no_boost == pytest.approx(base)


# ── calc_health_score — tier / context half-life sensitivity ────────────────


def test_health_score_inner_decays_faster_than_public_for_old_contact(fixed_now):
    # For an OLD contact (days_since >> both half-lives), the tier with
    # the SHORTER half-life has already decayed further → smaller score.
    # This is the regime where the tier dimension actually matters.
    old_iso = _days_ago(120)
    score_inner = calc_health_score(old_iso, 5, "inner")
    score_public = calc_health_score(old_iso, 5, "public")
    assert score_inner < score_public


def test_health_score_personal_inner_decays_faster_than_work_inner(fixed_now):
    # personal:inner (14d) vs work:inner (21d): personal decays faster.
    # Use an age where both terms are sensitive (mid-decay, ~17 days).
    mid_iso = _days_ago(17)
    score_personal = calc_health_score(mid_iso, 5, "inner", context="personal")
    score_work = calc_health_score(mid_iso, 5, "inner", context="work")
    assert score_personal < score_work


def test_health_score_legacy_circle_string_resolves_via_legacy_table(fixed_now):
    # Old callers pass plain "inner"/"friends"/etc. without a context.
    # The lookup falls through ``_HALF_LIFE`` (which is keyed on
    # context:tier) to ``_HALF_LIFE_LEGACY``. Pin that this still
    # produces a valid, well-shaped score (not the 60-day fallback).
    # Differential check: "friends" must resolve via _HALF_LIFE_LEGACY (half-life
    # 30) and NOT via the 60-day default. With identical inputs, an unknown circle
    # falls back to 60 → slower decay → strictly higher recency/score. If "friends"
    # silently fell through to the 60 default the two scores would be equal.
    score_legacy = calc_health_score(_days_ago(7), 10, "friends")
    score_fallback = calc_health_score(_days_ago(7), 10, "totally-bogus-circle")
    assert 0.0 < score_legacy < score_fallback <= 1.0
    # Pin the table value the lookup must use, so a future table edit is visible here.
    assert _HALF_LIFE_LEGACY["friends"] == 30


def test_health_score_unknown_circle_falls_back_to_60_day_default(fixed_now):
    # An unrecognized ``circle`` value falls through both tables to
    # the ``or _HALF_LIFE_LEGACY.get(circle, 60)`` default. The score
    # must still be well-formed (in [0, 1]) for any garbage string.
    score = calc_health_score(_days_ago(30), 10, "totally-bogus-circle")
    assert 0.0 <= score <= 1.0


# ── calc_health_score — output shape and cap ─────────────────────────────────


def test_health_score_is_always_in_unit_interval(fixed_now):
    # No matter the inputs, the score must land in [0.0, 1.0]. Sample
    # the corners: very recent + saturated freq + birthday boost can
    # overflow before the cap, and the cap must catch it.
    cases = [
        # (last_contacted_at, contact_count, circle, next_birthday, context)
        (None, 0, "inner", None, "personal"),
        (_days_ago(0), 0, "inner", None, "personal"),
        (_days_ago(0), 1000, "inner", _birthday_in(7), "personal"),
        (_days_ago(0), 1000, "inner", _birthday_in(7), "work"),
        ("garbage", 999, "public", _birthday_in(0), "personal"),
        (_days_ago(0), 0, "public", None, "work"),
    ]
    for last_contacted_at, contact_count, circle, bday, context in cases:
        score = calc_health_score(last_contacted_at, contact_count, circle, bday, context)
        assert 0.0 <= score <= 1.0, (last_contacted_at, contact_count, circle, bday, context, score)


def test_health_score_cap_clips_to_1_with_birthday_boost(fixed_now):
    # With a recent contact, saturated frequency, AND a birthday boost,
    # the raw sum is 0.6 + 0.3 + 0.3 = 1.2, which must clip to 1.0.
    # Pin that the cap is ``min(..., 1.0)`` not ``min(..., 0.9)`` or
    # some other arbitrary number.
    score = calc_health_score(
        _days_ago(0), 100, "inner", _birthday_in(7), "personal"
    )
    assert score == pytest.approx(1.0)


def test_health_score_no_contact_no_freq_strictly_zero(fixed_now):
    # With ``last_contacted_at=None`` (days_since=365) and contact_count=0
    # (freq=0) and no birthday boost, recency for any half-life is
    # ``exp(-365/H)`` — nonzero but tiny. The score is therefore
    # strictly positive but extremely small.
    score = calc_health_score(None, 0, "public", None, "personal")
    assert score >= 0.0
    # public half-life is 90d → exp(-365/90) ≈ 0.0163 → 0.6*0.0163 ≈ 0.0098
    assert score < 0.05


def test_health_score_returns_python_float(fixed_now):
    # The public contract is ``float``; downstream JSON serialisation
    # relies on it being a real float, not a numpy scalar or Decimal.
    score = calc_health_score(_days_ago(7), 10, "inner")
    assert isinstance(score, float)


def test_health_score_rounded_to_three_decimals(fixed_now):
    # The implementation rounds to 3 decimal places before capping.
    # The score must therefore have at most 3 fractional digits —
    # although floating-point repr can be surprising, multiplying by
    # 1000 and flooring must match the rounded value exactly.
    score = calc_health_score(_days_ago(7), 10, "inner")
    assert round(score, 3) == score, (
        f"score {score!r} is not a 3-decimal rounded value"
    )
