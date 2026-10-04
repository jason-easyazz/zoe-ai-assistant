"""Class 1 — numeric dates are DAY first in this (Australian) household, from ONE helper.

Every reader of a numeric date in user text goes through ``date_locale``: the person
extractor, the memory extractor/digest prompts, the reminder grammar and the brain's
turn. Negative controls: a month-first (US) household and an explicit override flip the
order, and the same helper then answers the other way — proving the order comes from the
helper and not from a hard-coded constant at any call site.
"""
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep: pure functions + the regex/prompt call sites

import date_locale as dl


@pytest.fixture(autouse=True)
def _perth(monkeypatch):
    monkeypatch.delenv("ZOE_DATE_ORDER", raising=False)
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")


# ── the three spec cases ─────────────────────────────────────────────────────

def test_ambiguous_numeric_date_is_day_first():
    d = dl.parse_numeric_date("7/8/1991")
    assert (d.day, d.month, d.year) == (7, 8, 1991)
    assert dl.render_date(d.day, d.month, d.year) == "7 August 1991"


def test_unambiguous_date_still_parses():
    d = dl.parse_numeric_date("26/10/1985")
    assert (d.day, d.month, d.year) == (26, 10, 1985)
    assert dl.parse_numeric_date("26/10").month == 10  # year-less, as a reminder says it


def test_iso_is_left_alone():
    assert dl.normalize_numeric_dates("due 2026-10-05 sharp") == "due 2026-10-05 sharp"
    assert dl.find_numeric_dates("on 2026-10-05") == []


# ── free text ────────────────────────────────────────────────────────────────

def test_normalize_rewrites_numeric_dates_in_words():
    assert dl.normalize_numeric_dates("his birthday is 7/8/1991.") == "his birthday is 7 August 1991."
    assert dl.normalize_numeric_dates("Ann - 30/05/2014") == "Ann - 30 May 2014"
    assert dl.normalize_numeric_dates("born 7.8.1991") == "born 7 August 1991"
    assert dl.normalize_numeric_dates("born 7-8-91") == "born 7 August 1991"


def test_written_months_are_unchanged_and_month_slash_year_keeps_its_month():
    assert dl.normalize_numeric_dates("on 20 February 1988") == "on 20 February 1988"
    assert dl.normalize_numeric_dates("Cy - 13/January/2022") == "Cy - 13 January 2022"


def test_month_first_date_that_cannot_be_day_first_still_parses():
    # a US-style 10/26/1985 is only a real date one way round
    d = dl.parse_numeric_date("10/26/1985")
    assert (d.day, d.month) == (26, 10) and d.ambiguous is False


@pytest.mark.parametrize("text", [
    "add 1/2 a cup of sugar", "we won 5/20", "version 1.2.3", "call 9/11", "10-12 pm",
    "price $7/8/1991", "31/4/2020 is no date", "see 7/8/19912",
])
def test_non_dates_are_not_rewritten(text):
    assert dl.normalize_numeric_dates(text) == text


def test_normalize_is_idempotent():
    once = dl.normalize_numeric_dates("on 7/8/1991 and 26/10")
    assert dl.normalize_numeric_dates(once) == once


# ── one place decides the order (negative controls) ──────────────────────────

def test_us_timezone_reads_month_first(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "America/Chicago")
    assert dl.date_order() == "mdy"
    d = dl.parse_numeric_date("7/8/1991")
    assert (d.day, d.month) == (8, 7)  # July 8 — the control that proves the helper decides


def test_explicit_override_wins_over_timezone(monkeypatch):
    monkeypatch.setenv("ZOE_DATE_ORDER", "mdy")
    assert dl.date_order() == "mdy"
    monkeypatch.setenv("ZOE_DATE_ORDER", "dmy")
    monkeypatch.setenv("ZOE_TIMEZONE", "America/Chicago")
    assert dl.date_order() == "dmy"


def test_misread_pattern_finds_the_month_first_rendering():
    d = dl.parse_numeric_date("7/8/1991")
    rx = dl.misread_pattern(d)
    for wrong in ("Jordan's birthday is July 8th, 1991.", "born 8 July 1991", "dob 1991-07-08",
                  "July 8, 1991"):
        assert rx.search(wrong), wrong
    assert not rx.search("born 7 August 1991")
    assert dl.misread_pattern(dl.parse_numeric_date("26/10/1985")) is None  # nothing to misread


# ── the brain's turn ─────────────────────────────────────────────────────────

def test_brain_hint_only_on_a_turn_with_a_numeric_date():
    hint = dl.day_first_hint("Jordan, his birthday is 7/8/1991")
    assert "7/8/1991 = 7 August 1991" in hint and "day-first" in hint
    assert dl.day_first_hint("what's the weather") == ""
    assert dl.day_first_hint("remind me on 2026-10-05") == ""


def test_flue_client_appends_the_hint_through_the_shared_helper():
    import zoe_flue_client as zfc

    assert "7/8/1991 = 7 August 1991" in zfc._day_first_hint("his birthday is 7/8/1991")
    assert zfc._day_first_hint("hello there") == ""


# ── call sites ───────────────────────────────────────────────────────────────

def test_person_extractor_parses_numeric_birthdays_day_first():
    import person_extractor as pe

    assert pe._parse_birthday("7/8/1991") == (8, 7, 1991)
    assert pe._parse_birthday("26/10/1985") == (10, 26, 1985)
    assert pe._parse_birthday("2026-10-05") == (10, 5, 2026)
    assert pe._parse_birthday("7 August 1991") == (8, 7, None)  # written year: caller decides (pinned)
    assert pe._parse_birthday("March 15") == (3, 15, None)


def test_reminder_grammar_reads_numeric_dates_day_first():
    import datetime
    from intent_router import _parse_date

    today = datetime.date(2026, 10, 4)
    assert _parse_date("7/8/2026", today=today) == "2026-08-07"
    assert _parse_date("26/10", today=today) == "2026-10-26"
    assert _parse_date("2026-10-05", today=today) == "2026-10-05"
    assert _parse_date("october 3", today=today) == "2026-10-03"


def test_extraction_prompts_carry_the_day_first_rule():
    import memory_digest as md
    import person_extractor_llm as pel

    rendered = (
        md._TURN_EXTRACTION_PROMPT.format(user_message="x"),
        md._EXTRACTION_PROMPT.format(chat_text="x"),
        pel._EXTRACTION_PROMPT.format(text="x", rules=pel._EXTRACTION_RULES),
        pel._EXTRACTION_PROMPT_CONF.format(text="x", rules=pel._EXTRACTION_RULES),
    )
    for prompt in rendered:
        assert dl.PROMPT_RULE in prompt


def test_memory_extractor_mines_a_numeric_birthday_in_words():
    import memory_extractor as mx

    cands = mx.extract_candidates("my birthday is 7/8/1991")
    texts = [c.text for c in cands]
    assert any("7 August 1991" in t for t in texts), texts
    assert not any("7/8/1991" in t for t in texts)


async def test_person_llm_prompt_shows_the_model_words_not_digits(monkeypatch):
    """The 4B model reads 7/8/1991 month-first; it must only ever see '7 August 1991'."""
    import person_extractor_llm as pel

    sent = {}

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            sent["prompt"] = json["messages"][-1]["content"]
            return _R()

    monkeypatch.setattr(pel.httpx, "AsyncClient", lambda **k: _C())
    await pel.process_text_llm("Pat Brown, his birthday is 7/8/1991 and he lives nearby",
                               user_id="demo-dates")
    assert "7 August 1991" in sent["prompt"] and "7/8/1991" not in sent["prompt"].split("Text:")[1]
