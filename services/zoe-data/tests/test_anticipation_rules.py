"""anticipation_rules: situations -> offers (one sentence, one existing tool with exact arguments, one person). Pure;
placeholder household only.

Negative controls (run when written; each turns the named test red):
  * make ``anticipation_situations.view`` return ``facts`` unchanged -> test_a_private_event_never_leaks_into_another_members_offer;
  * drop ``or (rule.adults_only and not me.adult)`` -> test_a_child_gets_no_alarm_offer_and_a_guest_gets_nothing;
  * drop ``rule.id in disabled or`` -> test_a_rule_can_be_disabled_or_tuned_per_person;
  * drop ``t.quiet or`` -> test_a_rule_can_be_disabled_or_tuned_per_person;
  * drop ``or key in seen`` and the ``seen.add`` -> not needed (keys are unique by construction; the test pins uniqueness);
  * change one ``priority`` in ``RULES`` -> test_the_docs_table_is_the_pack.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anticipation_rules as R  # noqa: E402
import anticipation_situations as S  # noqa: E402

pytestmark = pytest.mark.ci_safe

TZ = ZoneInfo("Australia/Perth")
NOW = datetime(2026, 11, 9, 3, 0, tzinfo=TZ)               # Monday, the night pass
MON, TUE = "2026-11-09", "2026-11-10"
HOUSE = (S.Member("ava", name="Ava"), S.Member("ben", name="Ben"), S.Member("kit", adult=False, name="Kit"),
         S.Member("remy", guest=True, name="Remy"))
WEEKDAYS = frozenset({1, 2, 3, 4, 5})
DOC = ROOT.parents[1] / "docs/knowledge/anticipation.md"


def ev(id, owner, title, day=MON, start="", **kw):
    return S.Ev(id, owner, title, day, start, **kw)


def facts(*events, now=NOW, **kw):
    return S.Facts(now=now, members=HOUSE, events=tuple(events), **kw)


def rules(f, person, **kw):
    return [o.rule for o in R.offers(f, person, **kw)]


def one(f, person, rule):
    hits = [o for o in R.offers(f, person) if o.rule == rule]
    assert len(hits) == 1, rules(f, person)
    return hits[0]


#: one scenario per rule: (facts, person). Every rule of the pack must fire on its own scenario.
SCENARIOS = {
    "wake": (facts(ev("e1", "ava", "Flight to the coast", start="09:40", place="Airport"), alarms=(S.Alarm("a1", "ava", "07:00", WEEKDAYS),)), "ava"),
    "alarm_for_event": (facts(ev("e1", "ava", "Breakfast meeting", start="07:30")), "ava"),
    "leave": (facts(ev("e1", "ava", "Dentist", start="14:00", place="12 Example Street")), "ava"),
    "packing": (facts(ev("e1", "ava", "Trip to the hills", day=TUE, start="09:00", end_day="2026-11-13")), "ava"),
    "flight_checkin": (facts(ev("e1", "ava", "Flight to the coast", day=TUE, start="11:00", place="Airport")), "ava"),
    "double_booked": (facts(ev("e1", "ava", "Review", start="14:00", end="15:00"), ev("e2", "ava", "Call", start="14:30")), "ava"),
    "clash_heads_up": (facts(ev("e1", "ava", "Review", start="15:00", end="16:00"), ev("e2", "ben", "School meeting", start="15:30", attendees=("ava",))), "ava"),
    "child_cover": (facts(ev("k1", "kit", "Swimming", start="15:30", end="16:15"), ev("a1", "ava", "Review", start="15:00", end="16:00"),
                          ev("b1", "ben", "Site visit", start="15:00", end="17:00")), "ben"),
    "birthday_reminder": (facts(birthdays=(S.Birthday("bd1", "ava", "Noa Example", 11, 12),)), "ava"),
    "chore_night_before": (facts(ev("e1", "ava", "Bins out", day=TUE, repeats=True)), "ava"),
    "full_day_prep": (facts(*[ev(f"e{i}", "ava", f"Thing {i}", start=f"{9 + 2 * i:02d}:00") for i in range(4)]), "ava"),
    "empty_day_catchup": (facts(typical={"ava": 3.0}, reminders=(S.Reminder("r1", "ava", "Sort the shed"),)), "ava"),
    "wind_down": (facts(ev("e1", "ava", "Concert", start="20:30", end="23:30"), ev("e2", "ava", "Stand-up", day=TUE, start="07:00"),
                        alarms=(S.Alarm("a1", "ava", "05:00", WEEKDAYS),)), "ava"),
    "tight_gap_leave": (facts(ev("e1", "ava", "Review", start="10:00", end="11:00", place="Office"), ev("e2", "ava", "Check-up", start="11:15", place="Clinic")), "ava"),
}


def test_the_pack_has_at_least_twelve_rules_and_each_fires():
    assert len(R.RULES) >= 12 and len(R.BY_ID) == len(R.RULES) and set(SCENARIOS) == set(R.BY_ID)
    for rule_id, (f, person) in SCENARIOS.items():
        assert rule_id in rules(f, person), (rule_id, rules(f, person))
    assert {k for r in R.RULES for k in r.situations} == set(S.KINDS)          # no situation without a rule, no rule without one


def test_every_offer_is_one_sentence_one_exact_tool_call_for_one_person():
    for rule_id, (f, person) in SCENARIOS.items():
        for o in R.offers(f, person):
            text = o.text(NOW)
            assert text.endswith("?") and text.count("?") == 1 and ". " not in text and "{" not in text, text
            assert o.person == person and o.visibility == "personal"
            assert set(o.args) == set(R.TOOLS[o.tool][1]), (o.rule, o.args)
            assert 0 <= o.priority <= 100 and NOW <= o.surface_from <= o.expires and o.expires > NOW
            assert o.key.startswith(o.rule + ":") and o.sources
            if o.tool != "list.create":
                when = o.args.get("due_time") or o.args["time_hhmm"]
                assert re.fullmatch(r"[0-2]\d:[0-5]\d", when), (o.rule, o.args)


def test_exact_arguments():
    wake = one(*SCENARIOS["wake"], "wake")
    assert (wake.tool, wake.args) == ("alarm.create", {"time_hhmm": "06:10", "once_date": MON, "label": "Early start"})
    assert wake.text(NOW) == "You start at 9:40 am today and your alarm is usually 7:00 am - want a one-off alarm for 6:10 am?"
    leave = one(*SCENARIOS["leave"], "leave")
    assert leave.args == {"title": "Leave for your appointment", "due_date": MON, "due_time": "13:30"}
    bins = one(*SCENARIOS["chore_night_before"], "chore_night_before")
    assert bins.args == {"title": "Bins out", "due_date": MON, "due_time": "19:00"} and bins.expires == datetime(2026, 11, 9, 19, 0, tzinfo=TZ)
    pack = one(*SCENARIOS["packing"], "packing")
    assert pack.args["name"] == "Packing: Trip to the hills" and "Clothes" in pack.args["items"]
    cover = one(*SCENARIOS["child_cover"], "child_cover")
    assert cover.args == {"title": "Sort out who takes Kit at 3:30 pm", "due_date": MON, "due_time": "08:00"}
    assert one(*SCENARIOS["full_day_prep"], "full_day_prep").text(NOW).startswith("Today is fuller than usual, 4 things")


def test_the_day_word_is_made_when_it_is_said():
    o = one(*SCENARIOS["flight_checkin"], "flight_checkin")
    assert "You fly tomorrow at" in o.text(NOW) and "You fly today at" in o.text(NOW + timedelta(days=1))


def test_the_same_situation_makes_the_same_key_every_night_and_keys_are_unique():
    f, person = SCENARIOS["flight_checkin"]
    again = S.Facts(now=NOW + timedelta(hours=6), members=f.members, events=f.events)
    assert [o.key for o in R.offers(f, person)] == [o.key for o in R.offers(again, person)]
    for f, person in SCENARIOS.values():
        keys = [o.key for o in R.offers(f, person)]
        assert len(keys) == len(set(keys))


# -- privacy -----------------------------------------------------------------------------------------------------------------------
SECRET = ("Zebracorn", "Quillfen")       # words that exist only in Ava's personal event


def _private_house():
    return facts(ev("p1", "ava", "Zebracorn specialist", start="15:00", end="16:00", place="Quillfen Clinic", visibility="personal",
                    attendees=("ben",)),
                 ev("p2", "ava", "Zebracorn flight", day=TUE, start="08:00", place="Quillfen Airport", visibility="personal", end_day="2026-11-12"),
                 ev("k1", "kit", "Swimming", start="15:30", end="16:15"),
                 ev("b1", "ben", "Site visit", start="15:00", end="17:00"), ev("b2", "ben", "Review", start="15:10", end="15:40"),
                 alarms=(S.Alarm("a1", "ava", "07:00", WEEKDAYS),), birthdays=(S.Birthday("bd1", "ava", "Zebracorn Quillfen", 11, 10),))


def test_a_private_event_never_leaks_into_another_members_offer():
    f = _private_house()
    assert {"wake", "leave", "packing", "birthday_reminder", "child_cover"} <= set(rules(f, "ava"))      # her own offers are whole
    visible = {"ben": {"event:k1", "event:b1", "event:b2"}, "kit": {"event:k1", "event:b1", "event:b2"}, "remy": set()}
    for person, may in visible.items():
        for o in R.offers(f, person):
            blob = " ".join([o.text(NOW), repr(o.args), o.key, repr(o.sources)])
            assert not any(word.lower() in blob.lower() for word in SECRET), (person, o.rule, blob)
            assert set(o.sources) <= may, (person, o.rule, o.sources)
    # not even by inference: from Ben's side Ava looks free at 3:30, so nothing tells him every adult is busy
    assert rules(f, "ben") == ["double_booked"] and rules(f, "kit") == [] and rules(f, "remy") == []


def test_a_busy_event_gives_a_block_and_no_words():
    f = facts(ev("p1", "ava", "Zebracorn specialist", start="15:00", end="16:00", place="Quillfen Clinic", visibility="busy"),
              ev("k1", "kit", "Swimming", start="15:30", end="16:15"), ev("b1", "ben", "Site visit", start="15:00", end="17:00"))
    o = one(f, "ben", "child_cover")
    assert "event:p1" in o.sources and not any(w in o.text(NOW) + repr(o.args) for w in SECRET)


def test_a_child_gets_no_alarm_offer_and_a_guest_gets_nothing():
    kid = facts(ev("e1", "kit", "Excursion train", start="07:30", place="Station"), ev("e2", "remy", "Flight home", start="09:00", place="Airport"))
    assert rules(kid, "kit") == ["leave"] and rules(kid, "remy") == []
    grown = facts(ev("e1", "ava", "Excursion train", start="07:30", place="Station"))
    assert rules(grown, "ava") == ["alarm_for_event", "leave"]


def test_a_rule_can_be_disabled_or_tuned_per_person():
    f, person = SCENARIOS["wake"]
    base = rules(f, person)
    assert "wake" in base and "wake" not in rules(f, person, disabled=frozenset({"wake"}))
    assert rules(f, "ava", disabled=frozenset({"wake"})) == [r for r in base if r != "wake"]
    assert "wake" not in rules(f, person, tune=lambda r: R.Tuning(quiet=r == "wake"))
    assert "leave" not in rules(f, person, tune=lambda r: R.Tuning(priority=-60 if r == "leave" else 0))   # under MIN_PRIORITY
    lifted = {o.rule: o for o in R.offers(f, person, tune=lambda r: R.Tuning(priority=25 if r == "leave" else 0, lead_h=12))}
    assert list(lifted)[0] == "leave" and lifted["leave"].priority == 95
    late = one(facts(ev("e1", "ava", "Flight", day=TUE, start="14:00", place="Airport")), "ava", "leave")
    assert late.surface_from == late.expires - timedelta(hours=R.SURFACE_LEAD_H) > NOW


def test_the_docs_table_is_the_pack():
    doc = DOC.read_text(encoding="utf-8")
    assert R.rules_table() in doc, "docs/knowledge/anticipation.md is stale: paste the output of `python3 services/zoe-data/anticipation_rules.py`"
