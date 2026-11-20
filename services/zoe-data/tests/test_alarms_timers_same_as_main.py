"""Alarms and timers each have ONE door, and nothing about what any entry point does has changed against ``main``.

``tests/fixtures/alarms_timers_as_on_main.json`` is the record of ``main`` (5533e3b08): for each entry point and action, every
statement with its values (reads too), every hook fired, every receipt, every timer snapshot, the result or refusal, and what the
store holds afterwards. It was made by running ``tests/alarms_timers_scenarios.py`` against an export of that commit
(``git archive origin/main services/zoe-data``), never by this branch's own code. The same scenarios run here and must give the
same record. This is also the "before the door" record the kernel-door tests compare flag-off behaviour with.

Covered. ALARMS: the screen's REST create / edit / delete / snooze / dismiss / day override and its undo (a member, the panel,
another member, refusals); the spoken path (set, snooze, off while ringing, cancel, cancel all, off and on, a guest away from the
panel); the brain's ``alarm_set`` tool (and its refusal of anything but a set); the ring engine. TIMERS: create (spoken, direct,
for a guest, from a panel voice turn), add and take off time, cancel (one, all, ambiguous, none), status, the panel's tap on a
running timer and its dismiss of a ringing one, a member's cancel by id (their own, another's), the grace running out, and the
timer-done task itself (``_ring_when_done``, the one production path this change touches): the line queued, the queue failing,
quiet hours, a second run, and a timer cancelled first: each with the queue call, the snapshot written and the flags left.

Negative controls run (each broke the code, saw the named scenario fail, restored): ``snooze_count = snooze_count + 1`` dropped
from the alarm snooze statement -> ``rest_snooze``; the event row dropped from ``dismiss`` -> ``rest_dismiss``; an extra read
added to ``delete_alarm`` -> ``rest_delete``; ``set_seconds`` changed by ``adjust`` -> ``timer_add_time``; the receipt dropped
from the timer ``create`` -> ``timer_create``; the two writes in ``_TimerStore.spoke`` swapped -> ``timer_done_is_announced``
and ``timer_done_when_the_queue_fails``; the save dropped from ``spoke`` -> ``timer_done_is_announced``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import alarms_timers_scenarios as scenarios

pytestmark = pytest.mark.ci_safe
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "alarms_timers_as_on_main.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    assert set(ON_MAIN) == set(scenarios.alarm_scenarios()) | set(scenarios.timer_scenarios())
    for prefix in ("rest_create", "rest_edit", "rest_delete", "rest_snooze", "rest_dismiss", "rest_override", "spoken_set",
                   "spoken_snooze", "spoken_off", "spoken_cancel", "brain_set", "engine_", "timer_create", "timer_add",
                   "timer_take", "timer_cancel", "timer_panel_tap", "timer_panel_dismisses", "timer_member_cancels", "timer_done"):
        assert any(name.startswith(prefix) for name in ON_MAIN), prefix


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_alarm_and_timer_entry_point_does_exactly_what_main_did(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_shows_writes_hooks_and_the_store_not_only_replies(here):
    """The comparison has something to bite on: a create's record holds its INSERT with values, the event row, the shadow hook,
    and the rows left behind; a timer's holds the snapshot, the receipt and the store."""
    kinds = {e[0] for e in here["rest_create"]}
    assert {"sql", "commit", "shadow", "returned", "rows"} <= kinds
    inserts = [e for e in here["rest_create"] if e[0] == "sql" and e[1].startswith("INSERT INTO alarms")]
    assert len(inserts) == 1 and "womble" in inserts[0][2] and here["rest_create"][-1][1]["alarms"]
    assert {"save", "arm", "receipt", "returned", "store"} <= {e[0] for e in here["timer_create"]}
    assert here["timer_create"][-1] == ["store", here["timer_create"][0][1]]


def test_the_timer_done_task_ran_and_its_two_endings_differ(here):
    """The pin runs the task: a queued line leaves ``announced`` and one snapshot AFTER the queue call; a failed queue (and
    quiet hours, which never reaches the queue) leaves ``voice_failed``; a second run and a cancelled timer add nothing."""
    queued, failed, quiet = (here[f"timer_done_{n}"] for n in ("is_announced", "when_the_queue_fails", "in_quiet_hours"))
    assert [e[0] for e in queued[-4:]] == ["announce", "save", "returned", "store"] and queued[-2][1] == [["t00000000001", True, False]]
    assert queued[-4][1]["panel_id"] == "panel-zed" and queued[-4][1]["dedupe_key"].startswith("timer_done:t00000000001:")
    assert failed[-2][1] == [["t00000000001", False, True]] and [e[0] for e in failed].count("announce") == 1
    assert quiet[-2][1] == [["t00000000001", False, True]] and "announce" not in [e[0] for e in quiet]
    assert here["timer_done_is_announced_once"] == queued
    assert here["timer_done_after_a_cancel_says_nothing"][-2][1] == [] and "announce" not in [e[0] for e in here["timer_done_after_a_cancel_says_nothing"]]

