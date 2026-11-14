"""anticipation_learning: accept / decline counts tune a rule per person - deterministic, decaying, and explainable to the count.

Negative controls (run when written; each turns the named test red):
  * ``QUIET_AFTER = 99`` -> test_three_noes_in_a_row_go_quiet_then_one_more_try;
  * count an ignored offer as a no (``if o.outcome != "ignored"`` removed) -> test_an_ignored_offer_neither_counts_nor_breaks_the_run;
  * drop the age weight (``0.5 ** (...)`` -> ``1``) -> test_an_old_answer_fades;
  * ``lead_h=0`` always -> test_a_rule_that_keeps_being_accepted_rises_and_is_said_earlier.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anticipation_learning as L  # noqa: E402
import anticipation_rules as R  # noqa: E402

pytestmark = pytest.mark.ci_safe

NOW = datetime(2026, 11, 9, 9, 0, tzinfo=ZoneInfo("Australia/Perth"))


def h(*items):
    """``("leave", "no", days_ago)`` ... -> history."""
    word = {"yes": "acted", "no": "dismissed", "left": "ignored"}
    return [L.Outcome(rule, word[what], NOW - timedelta(days=ago)) for rule, what, ago in items]


def test_no_history_changes_nothing():
    st = L.state([], "leave", NOW)
    assert st.tuning == R.Tuning() and (st.acted, st.dismissed, st.ignored, st.quiet_until) == (0, 0, 0, None)
    assert L.explain([], "leave", NOW)["status"] == "offering"


def test_three_noes_in_a_row_go_quiet_then_one_more_try():
    two = h(("leave", "no", 9), ("leave", "no", 5))
    assert not L.state(two, "leave", NOW).tuning.quiet
    three = two + h(("leave", "no", 2))
    st = L.state(three, "leave", NOW)
    assert st.tuning.quiet and st.no_run == 3 and st.quiet_until == NOW - timedelta(days=2) + timedelta(days=L.QUIET_DAYS)
    assert not L.state(three, "wake", NOW).tuning.quiet                           # per rule
    after = NOW + timedelta(days=L.QUIET_DAYS)                                    # the quiet has run out: one more try
    assert not L.state(three, "leave", after).tuning.quiet
    fourth = three + [L.Outcome("leave", "dismissed", after)]
    assert L.state(fourth, "leave", after + timedelta(days=1)).tuning.quiet       # another no: quiet again from that day
    yes = three + [L.Outcome("leave", "acted", after)]
    st = L.state(yes, "leave", after + timedelta(days=1))
    assert not st.tuning.quiet and st.no_run == 0 and st.yes_run == 1             # a yes ends the run


def test_an_ignored_offer_neither_counts_nor_breaks_the_run():
    assert not L.state(h(("leave", "no", 9), ("leave", "no", 5), ("leave", "left", 2)), "leave", NOW).tuning.quiet
    assert L.state(h(("leave", "no", 9), ("leave", "no", 5), ("leave", "left", 3), ("leave", "no", 2)), "leave", NOW).tuning.quiet
    assert L.state(h(("leave", "left", 1)), "leave", NOW).tuning.priority == -5   # but it does cost half a no


def test_an_old_answer_fades():
    assert L.state(h(("leave", "no", 0)), "leave", NOW).tuning.priority == -10
    assert L.state(h(("leave", "no", L.HALF_LIFE_DAYS)), "leave", NOW).tuning.priority == -5
    assert L.state(h(("leave", "no", 300)), "leave", NOW).tuning.priority == 0
    assert L.state(h(*[("leave", "no", 0)] * 2, ("leave", "yes", 0), *[("leave", "no", 0)] * 2), "leave", NOW).tuning.priority == -L.PRIORITY_CAP


def test_a_rule_that_keeps_being_accepted_rises_and_is_said_earlier():
    st = L.state(h(("leave", "yes", 4), ("leave", "yes", 1)), "leave", NOW)
    assert st.yes_run == 2 and st.tuning.lead_h == 12 and 15 <= st.tuning.priority <= 20
    many = L.state(h(*[("leave", "yes", i) for i in range(6)]), "leave", NOW)
    assert many.tuning == R.Tuning(priority=L.PRIORITY_CAP, lead_h=L.LEAD_CAP_H)
    assert L.state(h(("leave", "yes", 4), ("leave", "no", 1)), "leave", NOW).tuning.lead_h == 0


def test_it_is_deterministic_and_ignores_the_future():
    hist = h(("leave", "no", 9), ("leave", "yes", 5), ("wake", "no", 2), ("leave", "no", 1))
    assert L.state(hist, "leave", NOW) == L.state(list(reversed(hist)), "leave", NOW)
    assert L.state(hist + [L.Outcome("leave", "dismissed", NOW + timedelta(days=1))], "leave", NOW) == L.state(hist, "leave", NOW)
    tune = L.tuner(hist, NOW)
    assert tune("leave") == L.state(hist, "leave", NOW).tuning and tune("packing") == R.Tuning()


def test_why_has_an_exact_answer_from_the_counts():
    quiet = L.explain(h(("leave", "yes", 40), ("leave", "no", 9), ("leave", "no", 5), ("leave", "no", 2)), "leave", NOW)
    assert quiet["status"] == "quiet" and (quiet["acted"], quiet["dismissed"], quiet["no_run"]) == (1, 3, 3)
    assert quiet["why"] == ("You said no 3 times in a row (the last on 2026-11-07), so I stopped offering it. "
                            "I'll offer it once more after 2027-01-06.") and quiet["quiet_until"] == "2027-01-06"
    low = L.explain(h(("empty_day_catchup", "no", 0)), "empty_day_catchup", NOW)
    assert low["status"] == "below_threshold" and low["priority"] == 10
    assert low["why"] == "Your answers (yes 0, no 1, left unanswered 0) put it at priority 10, under the 20 I need to offer it."
    on = L.explain(h(("wake", "yes", 0)), "wake", NOW)
    assert on["status"] == "offering" and on["why"] == ("Its priority is 100 (base 90, +10 from your answers: yes 1, no 0, left unanswered 0), "
                                                        "and I may say it 6 hours earlier because you said yes 1 time in a row.")
    assert L.explain([], "wake", NOW, disabled=True) == {**L.explain([], "wake", NOW), "status": "off", "why": "You switched this kind of offer off."}
