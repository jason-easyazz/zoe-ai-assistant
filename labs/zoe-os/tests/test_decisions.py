"""The owner's decisions of 2026-11-14, pinned: retention option B, the twin's 28 days, and who
is shown a line about a person. A test here failing means a decision was changed in code
without being changed in the record first.
"""
from __future__ import annotations

from twin_helpers import learn
from world import household

from zoe_os.capability import GUEST, Person
from zoe_os.events import Event, Kind
from zoe_os.retention import CHOSEN, OPTIONS, POLICY, TWIN_WINDOW_DAYS, Rule
from zoe_os.twin import unusual as unusual_module
from zoe_os.twin.baseline import MIN_DAYS, Twin
from zoe_os.twin.days import SAMPLE, generate
from zoe_os.twin.unusual import shown_to, unusual

TODAY = "2030-03-25"


def test_the_kernel_holds_the_chosen_retention_policy_and_does_nothing_until_asked(tmp_path):
    k = household(tmp_path)
    assert CHOSEN == "balanced" and k.retention.policy is POLICY and POLICY is OPTIONS["balanced"]
    assert POLICY[Kind.UTTERANCE] == Rule(30, 335) and POLICY[Kind.PRESENCE] == Rule(14, 351)
    assert POLICY[Kind.ACTION_RECEIPT] == Rule(365, 365) and POLICY[Kind.FORGET] == Rule(None)
    old = k.spine.publish(Event(Kind.UTTERANCE, "voice", subject="sam", payload={"text": "synthetic words"}))
    assert k.retention.stage(old.seq) == "full"               # no scheduler: nothing aged by itself
    assert k.retention.sweep(now=k.clock() + 31 * 86400) == {"expired": 1, "retired": 0}
    assert k.retention.stage(old.seq) == "skeleton"
    k.close()


def test_the_twin_keeps_28_days_which_is_enough_for_both_baselines():
    assert TWIN_WINDOW_DAYS == 28 and Twin().window_days == 28
    assert 20 >= MIN_DAYS["workday"] and 8 >= MIN_DAYS["weekend"]     # four weeks: 20 working, 8 weekend
    assert TWIN_WINDOW_DAYS > POLICY[Kind.PRESENCE].full_days         # the stated cost: it outlives presence


def a_sick_child_day(tmp_path):
    days = generate(SAMPLE, days=22, seed=7, awkward={i: "" for i in range(21)} | {21: "sick_child"})
    k, twin, _ = learn(tmp_path, days)
    people = dict(k.grants.people) | {"other": Person("other"), "visitor": Person("visitor", GUEST)}
    return k, unusual(twin, TODAY), people


def test_a_line_is_shown_to_the_person_it_is_about_and_to_a_childs_guardians_only(tmp_path):
    k, result, people = a_sick_child_day(tmp_path)
    assert {d.person for d in result.departures} == {"kid", "sam"}            # what there is to show
    assert {d.person for d in shown_to(result, "sam", people)} == {"kid", "sam"}     # own, and their child
    assert {d.person for d in shown_to(result, "alex", people)} == {"kid"}    # the child's; NOT sam's
    assert shown_to(result, "kid", people) and all(d.person == "kid" for d in shown_to(result, "kid", people))
    for nobody in ("other", "visitor", "twin", "no-such-person"):             # an adult outside, a guest, a skill
        assert shown_to(result, nobody, people) == []
    k.close()


def test_negative_control_without_the_rule_one_adult_is_shown_another_adults_day(tmp_path, monkeypatch):
    k, result, people = a_sick_child_day(tmp_path)
    monkeypatch.setattr(unusual_module, "_may_show", lambda viewer, about: True)
    assert "sam" in {d.person for d in shown_to(result, "alex", people)}
    k.close()
