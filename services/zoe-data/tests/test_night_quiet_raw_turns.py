"""K9f class fix: a thread is QUIET only if the owner's raw turns went quiet, not when the model dropped a mention.

A 12B window flagged one thread of a FLAT month as a false notice: the model kept only an early moment of the trip thread, so the code-computed quiet saw
"one mention, 25 days ago" and fired, although the owner talked about the trip again later. ``spoken_since`` asks the owner's raw turns (the same
name / shared-word test that continues a thread, no language cue) before a thread is called quiet.
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


def _plan(raw_turns):
    mo = nm.Moment(mid="q1", turn=EARLY, quote=EARLY.text, kind="plan", who=["Vesper"], state="current")      # the model kept ONLY the early mention
    g = nm.Group(moments=[mo], thread_id="", title=TITLE, model_status="open")
    counts = dict.fromkeys(nm.COUNT_KEYS, 0)
    plan = nm._build_plan("u1", [g], [], [], TODAY, NOW, [], "2026-10-11", counts, [], frozenset(), raw_turns)
    return plan, counts


def test_a_later_raw_turn_that_names_the_thread_vetoes_quiet():
    plan, counts = _plan([EARLY, LATER, UNRELATED])
    assert [t["status"] for t in plan["threads"]] == ["open"]
    assert not [c for c in plan["changes"] if c["type"] == "quiet"] and counts["quiet_vetoed"] == 1 and counts["threads_quiet"] == 0


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


def test_the_veto_is_what_keeps_the_thread_open(monkeypatch):
    """Negative control: with the guard switched off the same plan reads quiet - so the first test is red without the fix."""
    monkeypatch.setattr(nm, "spoken_since", lambda *a, **k: False)
    plan, _counts = _plan([EARLY, LATER])
    assert [t["status"] for t in plan["threads"]] == ["quiet"]
