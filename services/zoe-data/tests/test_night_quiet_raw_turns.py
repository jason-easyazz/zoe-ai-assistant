"""K9f class fix: a thread is QUIET only if the owner's raw turns went quiet, not when the model dropped a mention.

A 12B window flagged one thread of a FLAT month as a false notice: the model kept only an early moment of the trip thread, so the code-computed quiet saw
"one mention, 25 days ago" and fired, although the owner talked about the trip again later. K9 (held-out, 12B) is the mirror case: the model dropped the
LAST mention of a thread that really did go quiet, and a veto on any later raw turn kept it open. ``spoken_days`` lets the owner's raw turns (the same
name / shared-word test that continues a thread, no language cue) set the thread's last day and cadence; the normal quiet test then runs on that.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import night_mind as nm  # noqa: E402

pytestmark = pytest.mark.ci_safe

UTC = dt.timezone.utc
TODAY = dt.date(2026, 10, 11)
NOW = dt.datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _turn(i, day, text):
    return nm.Turn(id=f"t{i}", text=text, at=dt.datetime(2026, 10, day, 9, 0, tzinfo=UTC) if day >= 1 else dt.datetime(2026, 9, 30 + day, 9, 0, tzinfo=UTC))


EARLY = _turn(1, -14, "We are going to Oldmere on the 12th for Vesper's birthday.")      # 2026-09-16
LATER = _turn(2, 1, "Booked the train to Oldmere for Vesper's birthday.")                  # 2026-10-01
UNRELATED = _turn(3, 5, "Turn on the kitchen lights and set a timer for ten minutes.")
OTHER_PEOPLE = _turn(4, 6, "Dagny and I walked along the river this morning.")
TITLE = "Vesper's birthday trip to Oldmere"


def _plan(raw_turns, kept=(EARLY,), title=TITLE, who="Vesper"):
    mos = [nm.Moment(mid=f"q{i}", turn=tu, quote=tu.text, kind="plan", who=[who], state="current") for i, tu in enumerate(kept, 1)]      # what the model kept
    g = nm.Group(moments=mos, thread_id="", title=title, model_status="open")
    counts = dict.fromkeys(nm.COUNT_KEYS, 0)
    plan = nm._build_plan("u1", [g], [], [], TODAY, NOW, [], "2026-10-11", counts, [], frozenset(), raw_turns)
    return plan, counts


def test_a_later_raw_turn_that_names_the_thread_vetoes_quiet():
    plan, counts = _plan([EARLY, LATER, UNRELATED])
    assert [t["status"] for t in plan["threads"]] == ["open"]
    assert not [c for c in plan["changes"] if c["type"] == "quiet"] and counts["quiet_moved"] == 1 and counts["threads_quiet"] == 0


def test_without_a_later_mention_the_thread_is_still_quiet():
    """The control: real silence still reads as quiet (the guard does not mute the notice)."""
    for turns in ([EARLY], [EARLY, UNRELATED, OTHER_PEOPLE], []):
        plan, counts = _plan(turns)
        assert [t["status"] for t in plan["threads"]] == ["quiet"], turns
        assert [c["type"] for c in plan["changes"] if c["type"] == "quiet"] == ["quiet"] and counts["threads_quiet"] == 1


def test_a_turn_before_the_last_mention_or_another_persons_story_does_not_veto():
    assert not nm.spoken_since(TITLE, [EARLY.text], [EARLY], "2026-09-16", TODAY)             # the mention itself is not "since"
    assert not nm.spoken_since(TITLE, [EARLY.text], [OTHER_PEOPLE], "2026-09-16", TODAY)      # names somebody else
    assert nm.spoken_since(TITLE, [EARLY.text], [LATER], "2026-09-16", TODAY)
    assert not nm.spoken_since(TITLE, [EARLY.text], [LATER], "2026-10-01", TODAY)              # said on the last day: not after it
    assert not nm.spoken_since("", [], [LATER], "2026-09-16", TODAY)                           # nothing to recognise it by: no veto


def test_the_guard_needs_no_english_cue():
    """Names and shared words only: a Spanish thread is vetoed by a later Spanish turn that names its person."""
    early = _turn(7, -10, "Me voy de viaje a Oldmere con Vesper la semana próxima.")
    later = _turn(8, 3, "Vesper todavía no ha reservado nada.")
    assert nm.spoken_since("Viaje a Oldmere con Vesper", [early.text], [later], early.at.date().isoformat(), TODAY)


RUN_A = _turn(10, -17, "I went for a run with Emeric this morning.")                          # 2026-09-13
RUN_B = _turn(11, -10, "Running with Emeric again, we are building up to the fun run.")      # 2026-09-20
RUN_C = _turn(12, -3, "Emeric and I ran 5k today.")                                            # 2026-09-27: the 12B dropped this moment
RUN_D = _turn(13, 9, "Another run with Emeric before work.")                                   # 2026-10-09: a recent mention (flat month)


def _run_plan(raw_turns):
    return _plan(raw_turns, kept=(RUN_A, RUN_B), title="Running with Emeric", who="Emeric")      # the model kept only 09-13 and 09-20


def test_drift_the_last_raw_mention_sets_the_clock_and_the_thread_still_goes_quiet():
    """K9 (held-out, 12B): the raw 09-27 turn moves the last day to 09-27; 14 days of silence against a 14-day threshold is quiet. A veto on ANY later turn missed this."""
    plan, counts = _run_plan([RUN_A, RUN_B, RUN_C, UNRELATED])
    assert [t["status"] for t in plan["threads"]] == ["quiet"] and counts["threads_quiet"] == 1 and counts["quiet_moved"] == 1
    assert [c["type"] for c in plan["changes"] if c["type"] == "quiet"] == ["quiet"]


def test_flat_a_recent_raw_mention_keeps_the_thread_open():
    """K9f: the model dropped the latest runs, a raw 10-09 turn names Emeric: not quiet."""
    plan, counts = _run_plan([RUN_A, RUN_B, RUN_C, RUN_D])
    assert [t["status"] for t in plan["threads"]] == ["open"] and counts["threads_quiet"] == 0 and counts["quiet_moved"] == 1


def test_negative_control_without_the_raw_turns_the_flat_month_reads_quiet(monkeypatch):
    """Without the raw-turn clock the same flat plan is quiet (last kept 09-20, 21 days): the K9f false notice, so the test above is red without the fix."""
    monkeypatch.setattr(nm, "spoken_days", lambda *a, **k: [])
    plan, _counts = _run_plan([RUN_A, RUN_B, RUN_C, RUN_D])
    assert [t["status"] for t in plan["threads"]] == ["quiet"]
    plan2, _c2 = _plan([EARLY, LATER])
    assert [t["status"] for t in plan2["threads"]] == ["quiet"]                                  # the trip case of the earlier test, same control


DENTIST = _turn(14, 9, "Emeric has a dentist appointment tomorrow.")                        # 2026-10-09: names Emeric, but it is ANOTHER story


def _run_and_dentist_plan(raw_turns, dentist_kept=True):
    """The running thread (kept 09-13 / 09-20) plus, when ``dentist_kept``, a second thread whose moment cites the raw 10-09 dentist turn."""
    mos = [nm.Moment(mid=f"q{i}", turn=tu, quote=tu.text, kind="plan", who=["Emeric"], state="current") for i, tu in enumerate((RUN_A, RUN_B), 1)]
    groups = [nm.Group(moments=mos, thread_id="", title="Running with Emeric", model_status="open")]
    if dentist_kept:
        dm = nm.Moment(mid="q9", turn=DENTIST, quote=DENTIST.text, kind="plan", who=["Emeric"], state="current")
        groups.append(nm.Group(moments=[dm], thread_id="", title="Emeric's dentist appointment", model_status="open"))
    counts = dict.fromkeys(nm.COUNT_KEYS, 0)
    plan = nm._build_plan("u1", groups, [], [], TODAY, NOW, [], "2026-10-11", counts, [], frozenset(), raw_turns)
    return plan, counts


def test_a_raw_turn_the_model_put_in_another_thread_does_not_refresh_this_one():
    """Greptile on #1982 (P1): ``spoken_days`` matched a person-only turn, so the dentist sentence refreshed the old running story and saved 10-09 as its last day."""
    plan, counts = _run_and_dentist_plan([RUN_A, RUN_B, DENTIST])
    running = next(t for t in plan["threads"] if t["title"] == "Running with Emeric")
    assert running["last_day"] == "2026-09-20" and running["status"] == "quiet" and counts["quiet_moved"] == 0
    dentist = next(t for t in plan["threads"] if t["title"] != "Running with Emeric")
    assert dentist["last_day"] == "2026-10-09" and dentist["status"] == "open"                 # its own story is untouched


def test_a_turn_the_model_captured_in_no_thread_still_counts():
    """The residual (K9f safety trade-off, open-problems.md): a dropped mention must not make a live thread look abandoned, even if it is only the person's name."""
    plan, counts = _run_and_dentist_plan([RUN_A, RUN_B, DENTIST], dentist_kept=False)
    running = next(t for t in plan["threads"] if t["title"] == "Running with Emeric")
    assert running["status"] == "open" and running["last_day"] == "2026-10-09" and counts["quiet_moved"] == 1


def _two_nights():
    """Night 1 (2026-10-11): the model kept RUN_A/RUN_B, the raw turns hold RUN_D. Night 2 (2026-10-13): night 1's rows fed back, no new groups, RUN_D has left the loader's window."""
    plan1, _c1 = _run_plan([RUN_A, RUN_B, RUN_C, RUN_D])
    row = next(iter(plan1["threads"]))
    today2, now2 = dt.date(2026, 10, 13), dt.datetime(2026, 10, 13, 3, 0, tzinfo=UTC)
    counts2 = dict.fromkeys(nm.COUNT_KEYS, 0)
    plan2 = nm._build_plan("u1", [], [dict(row)], list(plan1["all_obs"]), today2, now2, [], "2026-10-13", counts2, [], frozenset(), [RUN_A, RUN_B, RUN_C])
    return plan1, plan2, counts2


def test_the_later_raw_mention_is_kept_on_the_thread_row_for_the_next_night():
    """Greptile round 4 on #1982: the raw turn moved the last day only for the night's quiet test; once it left the loader's rolling window the next night read the thread quiet."""
    plan1, plan2, counts2 = _two_nights()
    assert [t["status"] for t in plan1["threads"]] == ["open"] and plan1["threads"][0]["last_day"] == "2026-10-09"
    assert [t["status"] for t in plan2["threads"]] == ["open"] and plan2["threads"][0]["last_day"] == "2026-10-09"      # 4 days against the threshold
    assert counts2["threads_quiet"] == 0


def test_the_control_a_row_that_kept_only_the_models_last_day_reads_quiet_on_night_two():
    """The row as it was before the fix (last day = the last KEPT moment, 09-20) with RUN_D gone from the raw turns: quiet. This is what the test above guards against."""
    plan1, _c1 = _run_plan([RUN_A, RUN_B, RUN_C, RUN_D])
    row = dict(plan1["threads"][0], last_day="2026-09-20")
    counts2 = dict.fromkeys(nm.COUNT_KEYS, 0)
    plan2 = nm._build_plan("u1", [], [row], list(plan1["all_obs"]), dt.date(2026, 10, 13), dt.datetime(2026, 10, 13, 3, 0, tzinfo=UTC), [], "2026-10-13", counts2, [],
                           frozenset(), [RUN_A, RUN_B, RUN_C])
    assert [t["status"] for t in plan2["threads"]] == ["quiet"]
