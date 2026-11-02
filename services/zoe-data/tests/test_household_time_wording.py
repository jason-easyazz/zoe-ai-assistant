"""Household-local time wording (time_utils.humanize_when / clock_stamp / now_line).

Owner report 2026-11-02: something said the evening before was voiced as "this morning" -
the proactive composer / brain was handed a bare fact with NO time and invented one. The
rule now: wording comes from code, in the household timezone, day boundaries at local
midnight. Synthetic timestamps only."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import time_utils as tu

pytestmark = pytest.mark.ci_safe

PERTH = ZoneInfo("Australia/Perth")
SYDNEY = ZoneInfo("Australia/Sydney")  # has DST


def _p(day, hh, mm=0, month=11):
    return datetime(2026, month, day, hh, mm, tzinfo=PERTH)


NOW = _p(2, 8, 0)  # Mon 2 Nov 2026 08:00 AWST


@pytest.fixture(autouse=True)
def _tz(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")


def test_owner_case_evening_yesterday_is_never_this_morning():
    out = tu.humanize_when(_p(1, 20, 30), now=NOW)
    assert out in ("yesterday evening", "last night")
    assert "this morning" not in out


def test_late_night_yesterday_is_last_night():
    assert tu.humanize_when(_p(1, 22, 0), now=NOW) == "last night"


def test_small_hours_today_read_as_last_night_by_day():
    assert tu.humanize_when(_p(2, 1, 30), now=NOW) == "last night"


def test_utc_midnight_straddle():
    # 06:30 AWST on the 2nd is 22:30 UTC on the 1st: the UTC dates differ, the LOCAL day is the same.
    then = datetime(2026, 11, 1, 22, 30, tzinfo=timezone.utc)
    assert tu.humanize_when(then, now=NOW) == "this morning"
    # 23:30 AWST on the 1st (15:30 UTC) is yesterday night, not today.
    assert tu.humanize_when(datetime(2026, 11, 1, 15, 30, tzinfo=timezone.utc), now=NOW) == "last night"


def test_naive_timestamps_are_utc_not_local():
    # Naive "2026-11-01T12:30:00" is 20:30 AWST yesterday, not 12:30 local.
    assert tu.humanize_when("2026-11-01T12:30:00", now=NOW) == "yesterday evening"
    assert tu.humanize_when("2026-11-01T12:30:00Z", now=NOW) == "yesterday evening"


def test_same_day_dayparts():
    now = _p(2, 21, 30)
    assert tu.humanize_when(_p(2, 8, 0), now=now) == "this morning"
    assert tu.humanize_when(_p(2, 13, 0), now=now) == "this afternoon"
    assert tu.humanize_when(_p(2, 18, 0), now=now) == "this evening"
    assert tu.humanize_when(_p(2, 21, 0), now=now) == "a little while ago"
    assert tu.humanize_when(_p(2, 21, 29), now=now) == "just now"


def test_yesterday_dayparts_and_weekdays():
    assert tu.humanize_when(_p(1, 9, 0), now=NOW) == "yesterday morning"
    assert tu.humanize_when(_p(1, 14, 0), now=NOW) == "yesterday afternoon"
    assert tu.humanize_when(_p(1, 2, 0), now=NOW) == "yesterday"
    assert tu.humanize_when(_p(30, 12, 0, month=10), now=NOW) == "on Friday"  # 3 days back


def test_a_week_ago_and_older():
    assert tu.humanize_when(_p(26, 12, 0, month=10), now=NOW) == "last week"
    assert tu.humanize_when(_p(19, 12, 0, month=10), now=NOW) == "two weeks ago"
    assert tu.humanize_when(_p(12, 12, 0, month=10), now=NOW) == "three weeks ago"
    assert tu.humanize_when(_p(4, 12, 0, month=10), now=NOW) == "on 4 October"
    assert tu.humanize_when(datetime(2025, 10, 12, 12, tzinfo=PERTH), now=NOW) == "on 12 October 2025"


def test_future_dated_never_claims_the_past():
    assert tu.humanize_when(NOW + timedelta(minutes=3), now=NOW) == "just now"  # skew
    out = tu.humanize_when(NOW + timedelta(days=3), now=NOW)
    assert out == "on 5 November"
    for bad in ("ago", "yesterday", "this morning", "last"):
        assert bad not in out


def test_unparseable_is_empty():
    assert tu.humanize_when("not a date", now=NOW) == ""
    assert tu.humanize_when(None, now=NOW) == ""


def test_dst_zone_boundary_and_perth_has_no_dst():
    now = datetime(2026, 10, 5, 8, 0, tzinfo=SYDNEY)  # just after DST began (4 Oct 2026)
    assert tu.humanize_when(datetime(2026, 10, 4, 20, 30, tzinfo=SYDNEY), now=now, tz=SYDNEY) == "yesterday evening"
    assert tu.humanize_when(datetime(2026, 10, 5, 6, 0, tzinfo=SYDNEY), now=now, tz=SYDNEY) == "this morning"
    assert tu.clock_stamp(datetime(2026, 1, 15, 12, tzinfo=timezone.utc)).endswith("(UTC+8, Australia/Perth)")
    assert tu.clock_stamp(datetime(2026, 7, 15, 12, tzinfo=timezone.utc)).endswith("(UTC+8, Australia/Perth)")


def test_env_timezone_drives_the_boundary(monkeypatch):
    then = datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc)
    now = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")  # 04:30 same day as 08:00 -> small hours
    assert tu.humanize_when(then, now=now) == "last night"
    monkeypatch.setenv("ZOE_TIMEZONE", "America/Los_Angeles")  # 13:30 vs 17:00, same local day
    assert tu.humanize_when(then, now=now) == "this afternoon"


def test_clock_stamp_and_now_line_exact_local():
    assert tu.clock_stamp(NOW) == "Mon 2 Nov 2026 08:00 (UTC+8, Australia/Perth)"
    assert tu.now_line(NOW) == "[Now: Mon 2 Nov 2026 08:00 (UTC+8, Australia/Perth)]"
    assert tu.clock_stamp("junk") == ""


def test_negative_control_utc_date_logic_would_get_it_wrong():
    """What UTC-date reasoning says for 06:30 AWST today: 'yesterday'. Local truth differs."""
    then = datetime(2026, 11, 1, 22, 30, tzinfo=timezone.utc)
    assert (NOW.astimezone(timezone.utc).date() - then.date()).days == 1
    assert tu.humanize_when(then, now=NOW) == "this morning"


# ── the paths that use it ────────────────────────────────────────────────────

def test_raise_block_carries_the_clock_and_forbids_inventing_when():
    from proactive import selector as sel

    r = sel.Raise(user_id="u", session_id="s", candidate_id="c", kind="open_loop", shape="cue",
                  text="a thing", hint="ask", token="t")
    assert "It is now " in r.block and "UTC+8" in r.block and "never say when" in r.block
    ev = sel.Raise(user_id="u", session_id="s", candidate_id="c", kind="event", shape="cue",
                   text="a thing (Mon 09:00)", hint="ask", token="t")
    assert "It is now" not in ev.block  # a calendar event states its own time


def test_the_core_lane_gets_the_same_clock_rule_as_flue():
    """Cross-review: the core lane wrapped ``raised.body`` bare, so only Flue carried the clock / no-invented-when rule."""
    import inspect

    import zoe_core_client as core
    from proactive import selector as sel

    r = sel.Raise(user_id="u", session_id="s", candidate_id="c", kind="open_loop", shape="cue",
                  text="a thing", hint="ask", token="t")
    assert r.content.startswith(r.body) and "It is now " in r.content and r.content in r.block
    msg = core._compose_message("hi", history=None, db_memory_context=None, portrait=None, voice_mode=False, raise_body=r.content)
    assert "It is now " in msg and "never say when" in msg
    assert "raise_body=raised.content" in inspect.getsource(core)  # the call site passes the rule-carrying text, not the bare body


def test_composer_prompt_carries_the_clock(monkeypatch):
    from proactive import composer

    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok msg"}}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            seen["prompt"] = json["messages"][1]["content"]
            seen["system"] = json["messages"][0]["content"]
            return _Resp()

    monkeypatch.setattr(composer.httpx, "AsyncClient", _Client)
    out = asyncio.run(composer.compose_message("emotional_followup", {"mentioned": "last night"}, "fb"))
    assert out == "ok msg"
    assert "Now (household local time):" in seen["prompt"] and "UTC+8" in seen["prompt"]
    assert "'mentioned': 'last night'" in seen["prompt"]
    assert "never 'this morning'" in seen["system"]


def test_recall_dates_already_use_local_calendar_days():
    """recall_evidence.render_date was already household-local; pinned so it stays so."""
    import recall_evidence as rev

    epoch = _p(1, 20, 30).timestamp()  # 12:30 UTC on the 1st, yesterday locally
    assert rev.render_date(epoch, now=NOW.timestamp()) == "Sun 1 Nov, yesterday"


def test_elapsed_time_is_real_across_a_dst_transition():
    # Sydney springs forward 2026-10-04 02:00 -> 03:00. 01:30 and 03:30 wall-clock are 30 min apart,
    # not 2 h: that is "a little while ago", not "this night".
    then = datetime(2026, 10, 4, 1, 30, tzinfo=SYDNEY)
    now = datetime(2026, 10, 4, 3, 30, tzinfo=SYDNEY)
    assert (now.astimezone(timezone.utc) - then.astimezone(timezone.utc)).total_seconds() == 3600
    assert tu.humanize_when(then, now=now, tz=SYDNEY) != "just now"
    just = datetime(2026, 10, 4, 3, 1, tzinfo=SYDNEY)  # 01:59 -> 03:01 wall = 2 min real
    assert tu.humanize_when(datetime(2026, 10, 4, 1, 59, tzinfo=SYDNEY), now=just, tz=SYDNEY) == "just now"


def test_far_future_names_the_year():
    assert tu.humanize_when(_p(5, 12, 0, month=2).replace(year=2027), now=NOW) == "on 5 February 2027"
