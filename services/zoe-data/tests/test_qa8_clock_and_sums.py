"""The time expert answers only a question whose sole subject is the clock (QA round 8: "what day is bowls" -> "It's 6:05 PM.")."""
import pytest

import expert_dispatch

pytestmark = pytest.mark.ci_safe

CLOCK = ["what time is it", "got the time?", "do you know what time it is", "what's the time zoe", "time please", "time",
         "hey zoe what is the time right now", "what hour is it"]
DATE = ["what day is it today", "what's the date", "what year is it"]
NOT_CLOCK = ["what day is bowls", "which day is max at kindy", "what day is bin day", "what time is it in tokyo",
             "what time does the chemist shut", "how many days until my exam", "what day did we go to the dentist",
             "when is the footy on", "what time is dinner", "is it time to leave for school"]


@pytest.mark.parametrize("text", CLOCK)
def test_bare_clock_question_is_answered_by_the_clock(text):
    assert expert_dispatch._plan("time", text) == ("time_query", {}, "read")


@pytest.mark.parametrize("text", DATE)
def test_bare_date_question_is_answered_by_the_date(text):
    assert expert_dispatch._plan("time", text) == ("date_query", {}, "read")


@pytest.mark.parametrize("text", NOT_CLOCK)
def test_a_question_about_anything_else_goes_to_the_brain(text):
    assert expert_dispatch._plan("time", text) is None


def test_long_rambling_text_is_not_a_clock_question():
    assert not expert_dispatch._is_bare_clock_ask("what is the time of day you know the day of the week it is right now today please")


# ── everyday sums (same QA round): money slang, units and a weekly rate ─────────────────────────────────────────────────────
import calc_speech  # noqa: E402

SUMS = [("whats 12000 plus 450 a week for 10 weeks", 16500.0), ("whats 12 grand plus 450 a week for 10 weeks", 16500.0),
        ("whats 240 volts times 10 amps", 2400.0), ("how much is 13.5k minus 12k", 1500.0), ("12 grand plus $450", 12450.0),
        ("what is 5 km plus 3 km", 8.0)]
NOT_SUMS = ["450 a week for 10 weeks", "whats 450 a week for 3 months", "what a grand day", "im paying 450 a week for 10 weeks",
            "10 kilos of spuds plus 2", "the grand final is on at 2",
            # adding different units is not 52 / 305 / 2050: no fixed answer, the brain takes it
            "whats 2 dollars plus 50 cents", "what is 5 km plus 300 metres", "$2 plus 50 cents", "2 kg minus 500 grams"]


@pytest.mark.parametrize("text,value", SUMS)
def test_everyday_sum_is_worked_out(text, value):
    got = calc_speech.parse(text)
    assert got is not None and got.value == value


@pytest.mark.parametrize("text", NOT_SUMS)
def test_talk_that_only_looks_like_a_sum_is_left_alone(text):
    assert calc_speech.parse(text) is None


# ── a record expert never answers a request for advice, or a possession question that is not about a list ──────────────────
ADVICE = [("reminders", "is there anything you think i should be thinking about this week"),
          ("calendar", "is there anything i should be getting ready this week"),
          ("reminders", "anything i should be doing this week"), ("calendar", "should i move my dentist appointment"),
          ("lists", "what do you reckon we need"), ("lists", "what am i saving for and how much have i got")]
STILL_READS = [("lists", "whats on my shopping list", "list_show"), ("lists", "do i have milk on the list", "list_show"),
               ("lists", "have i got anything to buy", "list_show"), ("lists", "whats on my todo", "list_show"),
               ("reminders", "what reminders do i have", "reminder_list"), ("calendar", "whats on this week", "calendar_show"),
               ("calendar", "do i have anything on friday", "calendar_show")]


@pytest.mark.parametrize("domain,text", ADVICE)
def test_advice_and_non_list_questions_go_to_the_brain(domain, text):
    assert expert_dispatch._plan(domain, text) is None


@pytest.mark.parametrize("domain,text,intent", STILL_READS)
def test_real_record_reads_still_read(domain, text, intent):
    plan = expert_dispatch._plan(domain, text)
    assert plan is not None and plan[0] == intent and plan[2] == "read"
