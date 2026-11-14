"""Rehearsal: a changed skill against generated and recorded days, privacy failures first."""
from __future__ import annotations

from replay_skills import EagerLeavingCatch, Gossip, PushyHeadsUp
from test_replay import a_day

from zoe_os.skills.calendar_headsup import CalendarHeadsUp
from zoe_os.skills.leaving_catch import LeavingCatch
from zoe_os.twin.days import SAMPLE, generate
from zoe_os.twin.rehearse import rehearse

DAYS = generate(SAMPLE, days=8, seed=7)

GOSSIP = """\
# zoe-os rehearsal v1
# current: leaving-catch | changed: leaving-catch
# days: 8 generated (seed 7), 0 recorded
PRIVACY: 3 new problems in the changed version (0 already in the current one). DO NOT TRUST THIS CHANGE.
  2030-03-06 wed [forgotten_phone] 07:44:00 @18    CROSSED  leaving-catch made an offer to kid from alex's private event
  2030-03-06 wed [forgotten_phone] 07:44:00 @18    LEAKED   leaving-catch showed kid words that are in the private events of alex, sam and not in their own
  2030-03-06 wed [forgotten_phone] 07:44:00 @18    CROSSED  leaving-catch made an offer to sam from alex's private event
SUMMARY              current  changed   change
  offers                   1        3       +2
  actions                  0        0       +0
  refusals                 0        0       +0
  claims                   0        0       +0
  findings                 0        0       +0
DIFFERENCES: 1 of 8 days differ
== 2030-03-06 wed [forgotten_phone]
+ 07:44:00 @18    OFFER    leaving-catch -> kid [card, unanswered] "alex left without: phone"
+ 07:44:00 @18    OFFER    leaving-catch -> sam [card, unanswered] "alex left without: phone"
"""


def test_a_change_that_tells_one_person_about_another_is_reported_first_and_in_full():
    result = rehearse([LeavingCatch], [Gossip], generated=DAYS)
    assert result.text == GOSSIP
    assert not result.ok and len(result.privacy) == 3 and result.differing_days == 1
    assert result.counts["offers"] == (1, 3)
    lines = result.text.splitlines()
    assert lines[3].startswith("PRIVACY:") and lines.index("SUMMARY              current  changed   change") > 6


def test_the_kernel_allowed_it_which_is_why_rehearsal_has_to_look():
    """The gossip offers are not refused: both people enabled the skill, so the kernel lets it
    derive one person's offer from the other's event. Zero refusals; still a privacy failure."""
    result = rehearse([LeavingCatch], [Gossip], generated=DAYS)
    assert result.counts["refusals"] == (0, 0) and not result.ok


def test_negative_control_without_the_privacy_pass_the_same_change_looks_like_two_more_offers(monkeypatch):
    monkeypatch.setattr("zoe_os.twin.rehearse._privacy", lambda k, facts, skills: [])
    result = rehearse([LeavingCatch], [Gossip], generated=DAYS)
    assert result.ok and "PRIVACY: no new problem" in result.text and result.counts["offers"] == (1, 3)


def test_a_change_the_kernel_had_to_stop_is_a_privacy_failure_even_though_nothing_happened():
    result = rehearse([LeavingCatch, CalendarHeadsUp], [LeavingCatch, PushyHeadsUp], generated=DAYS)
    assert result.privacy == ["2030-03-07 thu 19:00:00 @31    REFUSED  calendar-heads-up tried to act "
                              "alarm.move: the person has not accepted an offer for this"]
    assert result.counts["actions"] == (0, 0) and result.counts["refusals"] == (0, 1)


def test_a_harmless_change_and_no_change_report_no_problem_and_only_the_real_differences(tmp_path):
    same = rehearse([LeavingCatch], [LeavingCatch], generated=DAYS)
    assert same.ok and same.differing_days == 0 and same.text.endswith("DIFFERENCES: 0 of 8 days differ\n")
    recorded = a_day(tmp_path)                       # the hand-recorded day: one unsure "left"
    eager = rehearse([LeavingCatch], [EagerLeavingCatch], generated=DAYS, recorded=[recorded])
    assert eager.ok and eager.differing_days == 1 and "# days: 8 generated (seed 7), 1 recorded" in eager.text
    assert eager.text.endswith('== recorded day 9\n+ 18:12:00 @7     OFFER    leaving-catch -> alex '
                               '[card, declined] "You left without: laptop"\n')


def test_a_new_skill_is_rehearsed_against_nothing():
    result = rehearse([], [LeavingCatch], generated=DAYS)
    assert "# current: (nothing) | changed: leaving-catch" in result.text
    assert result.ok and result.counts["offers"] == (0, 1) and result.differing_days == 1


def test_the_same_rehearsal_gives_the_same_text():
    assert rehearse([LeavingCatch], [Gossip], generated=DAYS).text == GOSSIP


def test_an_existing_problem_is_counted_but_not_blamed_on_the_change():
    result = rehearse([Gossip], [Gossip], generated=DAYS)
    assert result.ok and "PRIVACY: no new problem in the changed version (3 already in the current one)." in result.text
