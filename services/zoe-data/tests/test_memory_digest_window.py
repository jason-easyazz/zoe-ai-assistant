"""The nightly digest must select a ROLLING window, not calendar-today.

Regression test for a silent failure: the digest job fires at 03:00 and asked
for users with activity "today", which at that hour is the 00:00-03:00 window.
The conversations it exists to digest happened the previous day and were
excluded by definition. Ten consecutive nightly runs (2026-07-11..07-20) each
completed cleanly and reported 0 users processed with all-zero effects, while
the weekly consolidation — same helper, today_only=False — saw 23-24 users.

These are SQL-shape assertions, not DB tests: they run without Postgres and
pin the clause the job actually issues.
"""
from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory_digest as md  # noqa: E402


def test_lookback_produces_a_rolling_interval_clause():
    sql = md._message_owner_users_sql(today_only=False, lookback_hours=30)
    assert "make_interval" in sql, "rolling window lost its interval clause"
    assert "now()::timestamptz -" in sql, "window no longer ends at now()"
    assert "::date =" not in sql, "rolling window must not pin a calendar date"


def test_lookback_takes_precedence_over_today_only():
    """today_only=True must not resurrect the calendar-day clause that caused
    the outage when a lookback is supplied."""
    sql = md._message_owner_users_sql(today_only=True, lookback_hours=30)
    assert "make_interval" in sql
    assert "::date =" not in sql


def test_calendar_today_clause_still_available_for_other_callers():
    """The old behaviour is not deleted — only the nightly job stops using it."""
    sql = md._message_owner_users_sql(today_only=True)
    assert "::date =" in sql
    assert "make_interval" not in sql


def test_placeholder_count_matches_bound_args():
    """The compat layer counts '?' literally. A mismatch here is exactly the
    class of bug that silently zeroes the query instead of raising."""
    assert md._message_owner_users_sql(today_only=False, lookback_hours=30).count("?") == 1
    assert md._message_owner_users_sql(today_only=True).count("?") == 2
    assert md._message_owner_users_sql(today_only=False).count("?") == 0


def test_default_lookback_covers_the_previous_day_from_0300():
    """Must exceed 27h — a 03:00 run needs the full previous calendar day."""
    assert md._DIGEST_LOOKBACK_HOURS >= 27, (
        f"lookback {md._DIGEST_LOOKBACK_HOURS}h cannot cover the previous day from a 03:00 run"
    )


@pytest.mark.asyncio
async def test_nightly_job_actually_issues_the_rolling_window(monkeypatch):
    """THE test that matters: assert the JOB's query, not just the helper's.

    An earlier version of this file only exercised _message_owner_users_sql, so
    reverting run_digest_for_all_active_users to today_only=True — the exact
    regression — left the suite green. Caught by mutation testing. This pins the
    SQL the nightly job actually issues.
    """
    captured: dict = {}

    async def _fake_list_user_ids(sql, args=(), db=None):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(md, "_list_user_ids", _fake_list_user_ids)
    await md.run_digest_for_all_active_users(db=object())

    assert captured, "run_digest_for_all_active_users did not query for users at all"
    assert "make_interval" in captured["sql"], (
        "nightly digest is NOT using the rolling window — this is the 03:00 "
        "calendar-today regression that processed 0 users for 10 nights"
    )
    assert "::date =" not in captured["sql"], "nightly digest pinned a calendar date again"
    assert captured["args"] == (md._DIGEST_LOOKBACK_HOURS,), (
        f"bound args {captured['args']!r} do not match the single interval placeholder"
    )


@pytest.mark.asyncio
async def test_extraction_path_uses_the_same_window_as_discovery(monkeypatch):
    """The gap Greptile caught, pinned.

    Widening only the DISCOVERY query is not a fix: the job then selects a user
    with previous-day activity and _load_todays_messages still loads
    calendar-today, so extraction finds nothing and the run skips with
    insufficient activity. Both halves must use the same window.

    Every earlier test in this file passed with that bug present, because they
    asserted the helper and the discovery call and never the extraction path.
    """
    captured: dict = {}

    class _Db:
        async def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

            class _R:
                async def fetchall(self_inner):
                    return []
            return _R()

    await md._load_todays_messages("user-1", _Db())

    assert captured, "_load_todays_messages issued no query"
    assert "make_interval" in captured["sql"], (
        "EXTRACTION still uses a calendar-day window while discovery uses a "
        "rolling one — the job will select a user and then extract nothing"
    )
    assert "::date =" not in captured["sql"]
    assert captured["params"] == ("user-1", md._DIGEST_LOOKBACK_HOURS)


@pytest.mark.parametrize(
    "raw,expected_is_default",
    [("30h", True), ("0", True), ("5", True), ("", True), ("48", False)],
)
def test_lookback_env_cannot_silently_break_the_digest(monkeypatch, raw, expected_is_default):
    """A typo must not raise at import; a too-small value must not recreate the
    empty window. Both were silent failure modes with a bare int()."""
    monkeypatch.setenv("ZOE_MEMORY_DIGEST_LOOKBACK_HOURS", raw)
    hours = md._digest_lookback_hours()
    assert hours >= md._DIGEST_LOOKBACK_MIN
    assert (hours == md._DIGEST_LOOKBACK_DEFAULT) is expected_is_default
