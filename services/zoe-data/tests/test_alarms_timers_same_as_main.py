"""Alarms and timers each have ONE door, and nothing about what any entry point does has changed against ``main``.

``tests/fixtures/alarms_timers_as_on_main.json`` is the record of ``main`` (5533e3b08): for each entry point and action, every
statement with its values (reads too), every hook fired, every receipt, every timer snapshot, the result or refusal, and what the
store holds afterwards. It was made by running ``tests/alarms_timers_scenarios.py`` against an export of that commit
(``git archive origin/main services/zoe-data``), never by this branch's own code. The same scenarios run here and must give the
same record. (The seven scenarios added in review, the erase, anticipation's alarm and the spoken one-day change, were
recorded the same way from main at b2bbe84ca; the other 49 came out byte-identical there.) The clock-radio's fallback event (``alarm_music._log``, driven through ``_fail``) was added in round 2, recorded from main at b2bbe84ca with the other 56 byte-identical; and every recorded alarms entry point must REACH the door through its own function (``tests/door_reach.py``), two named proxies apart. This is also the "before the door" record the kernel-door tests compare flag-off behaviour with.

Covered (the list of entry points is read from the code: ``test_the_fixture_covers_every_scenario_and_every_entry_point``). ALARMS: the screen's REST create / edit / delete / snooze / dismiss / day override and its undo (a member, the panel,
another member, refusals); the spoken path (set, snooze, off while ringing, cancel, cancel all, off and on, a guest away from the
panel); the brain's ``alarm_set`` tool (and its refusal of anything but a set); the ring engine; the spoken one-day change (a day's
exception, its undo, a one-off moved, none to move); anticipation's accepted wake alarm; a member's erasure (``forget_user``). TIMERS: create (spoken, direct,
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


_REST, _VOICE = "routers/alarms.py", "alarm_voice.py"
#: ALARMS: every (module, function, door function of ``alarms.py``) the CODE holds -> the scenarios that record it
ALARMS_RECORDED = {
    (_REST, "create_alarm", "create_alarm"): ("rest_create", "rest_create_at_the_panel"),
    (_REST, "update_alarm", "update_alarm"): ("rest_edit", "rest_edit_disable"),
    (_REST, "delete_alarm", "delete_alarm"): ("rest_delete",),
    (_REST, "snooze_alarm", "snooze"): ("rest_snooze", "rest_snooze_by_the_panel"),
    (_REST, "dismiss_alarm", "dismiss"): ("rest_dismiss", "rest_dismiss_by_the_panel"),
    (_REST, "set_override", "set_override"): ("rest_override",),
    (_REST, "clear_override", "clear_override"): ("rest_override_clear",),
    (_VOICE, "_run", "create_alarm"): ("spoken_set", "spoken_set_repeating", "brain_set"),
    (_VOICE, "_run", "update_alarm"): ("spoken_turn_off_then_on",),
    (_VOICE, "_run", "delete_alarm"): ("spoken_cancel", "spoken_cancel_all"),
    (_VOICE, "_run", "snooze"): ("spoken_snooze",),
    (_VOICE, "_run", "dismiss"): ("spoken_off_while_ringing",),
    (_VOICE, "_run_shift", "set_override"): ("spoken_shift_a_repeating_alarm",),
    (_VOICE, "_run_shift", "clear_override"): ("spoken_shift_undone",),
    (_VOICE, "_run_shift", "update_alarm"): ("spoken_shift_a_one_off",),
    (_VOICE, "_run_shift", "create_alarm"): ("spoken_shift_with_no_alarm_makes_one",),
    ("anticipation.py", "_do", "create_alarm"): ("anticipation_wake_alarm",),
    ("memory_service.py", "MemoryService.delete_user", "forget_user"): ("forget_a_member", "forget_nobody_and_the_household"),
    ("main.py", "lifespan", "run_loop"): ("engine_rings_and_stops_by_itself",),      # the loop is ``tick`` on a timer
    ("alarm_music.py", "_log", "log_event"): ("music_did_not_start_logs_the_fallback",),
}
ALARMS_NOT_RECORDED: dict = {}
#: TIMERS: every function of ``skybridge_service`` outside the store that calls a store method that saves -> its scenarios
_SKY = "skybridge_service.py"
TIMERS_RECORDED = {
    (_SKY, "_resolve_timer", "_TimerStore"): ("timer_create", "timer_create_direct", "timer_cancel", "timer_cancel_all",
                                              "timer_cancel_ambiguous", "timer_cancel_none"),
    (_SKY, "_resolve_timer_change", "_TimerStore"): ("timer_add_time", "timer_take_time_off"),
    (_SKY, "_timer_reply", "_TimerStore"): ("timer_status",),
    (_SKY, "_which_timer_line", "_TimerStore"): ("timer_cancel_ambiguous",),
    (_SKY, "cancel_any_timer", "_TimerStore"): ("timer_panel_tap_cancels_a_running_timer", "timer_panel_dismisses_a_ringing_timer"),
    (_SKY, "cancel_timer_by_id", "_TimerStore"): ("timer_member_cancels_their_own_by_id", "timer_member_cannot_cancel_anothers_by_id"),
    (_SKY, "all_active_timers", "_TimerStore"): ("timer_a_finished_timer_leaves_after_the_grace",),
    (_SKY, "_ring_when_done", "_TimerStore"): ("timer_done_is_announced", "timer_done_when_the_queue_fails", "timer_done_in_quiet_hours"),
}
TIMERS_NOT_RECORDED = {
    (_SKY, "preload_timers", "_TimerStore"): "start-up only: it loads the snapshot and re-arms what it holds; it changes no timer",
    (_SKY, "active_timers_for", "_TimerStore"): "a read for one owner (the store prunes what has ended, as all_active_timers' scenario records)",
}
STORE_NAMES = {"_timers", "store"}       # how ``skybridge_service`` names the store outside the class: the global, or a parameter


def timer_entry_points(source: str) -> dict:
    """``{(module, function): ["_TimerStore"]}``: the functions outside the store that call one of its SAVING methods."""
    import ast

    import door_callers

    saving = {m.split(".", 1)[1] for m in door_callers.door_functions(source, {"_save"}) if m.startswith("_TimerStore.")}
    found = {}
    for name, fn in door_callers._top_functions(ast.parse(source)):
        if not name.startswith("_TimerStore.") and any(
                isinstance(n, ast.Attribute) and n.attr in saving and isinstance(n.value, ast.Name) and n.value.id in STORE_NAMES
                for n in ast.walk(fn)):
            found[(_SKY, name)] = ["_TimerStore"]
    return found


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    """The entry points are READ FROM THE CODE (``tests/door_callers.py``), not picked by name: for alarms, every function of
    the service that names a writing function of ``alarms.py``; for timers, every function that calls a saving method of the
    store. Each is recorded by named scenarios or listed with the reason it is not.
    Controls run: a new ``async def sneak(db): await alarms.delete_alarm(db, {})`` in ``routers/alarms.py`` fails this naming
    it; so does a new ``def sneak(): _timers.cancel_any("t")`` in ``skybridge_service.py``. (The hand-picked prefixes this
    replaces had no ``forget_user``, no anticipation alarm and no spoken one-day change: all three now have scenarios.)"""
    import door_callers
    import sql_scan

    assert set(ON_MAIN) == set(scenarios.alarm_scenarios()) | set(scenarios.timer_scenarios())
    root = Path(__file__).resolve().parents[1]
    door = (root / "alarms.py").read_text("utf-8")
    writers = door_callers.door_functions(door, door_callers.direct_sql_writers(
        door, sql_scan.write_pattern("alarms", "alarm_events", "alarm_overrides")))
    assert {"create_alarm", "update_alarm", "delete_alarm", "snooze", "dismiss", "forget_user", "set_override"} <= writers
    door_callers.check_coverage(door_callers.entry_points(root, "alarms", writers), ALARMS_RECORDED, ALARMS_NOT_RECORDED, set(ON_MAIN))
    door_callers.check_coverage(timer_entry_points((root / _SKY).read_text("utf-8")), TIMERS_RECORDED, TIMERS_NOT_RECORDED, set(ON_MAIN))


def test_the_new_scenarios_record_the_erase_the_anticipation_alarm_and_the_one_day_change(here):
    """What each added scenario holds. Controls run: the ``DELETE FROM alarm_overrides`` dropped from ``forget_user`` ->
    ``forget_a_member``; ``repeat="daily"`` given by anticipation's ``_do`` -> ``anticipation_wake_alarm``; the day's exception
    written for the day after -> ``spoken_shift_a_repeating_alarm``."""
    def writes(name):
        return [(e[1].split(" (")[0].split(" WHERE")[0], e[2]) for e in here[name] if e[0] == "sql" and not e[1].startswith("SELECT")]

    erased = writes("forget_a_member")[-3:]
    assert [w[0] for w in erased] == ["DELETE FROM alarm_events", "DELETE FROM alarm_overrides", "DELETE FROM alarms"]
    assert all(w[1] in (["a00000000001"], [scenarios.ANN]) for w in erased)
    left = here["forget_a_member"][-1][1]
    assert sorted(r[1] for r in left["alarms"]) == sorted([scenarios.BOB, "__household__"]) and left["alarm_overrides"] == []
    assert here["forget_nobody_and_the_household"][-2] == ["returned", [0, 0]] and len(here["forget_nobody_and_the_household"][-1][1]["alarms"]) == 1
    made = here["anticipation_wake_alarm"][-1][1]["alarms"]
    assert len(made) == 1 and made[0][1:6] == [scenarios.ANN, "Womble day", "05:30", "once", "2026-11-05"]
    day = here["spoken_shift_a_repeating_alarm"][-1][1]
    assert [r[:4] for r in day["alarm_overrides"]] == [["a00000000001", "2026-11-05", "shift", "05:30"]] and day["alarms"][0][3] == "06:30"
    assert here["spoken_shift_undone"][-1][1]["alarm_overrides"] == []
    assert here["spoken_shift_a_one_off"][-1][1]["alarms"][0][3] == "06:00"
    assert here["spoken_shift_with_no_alarm_makes_one"][-1][1]["alarms"][0][3:6] == ["05:30", "once", "2026-11-05"]


@pytest.mark.parametrize("key", sorted(ALARMS_RECORDED), ids=lambda k: f"{k[0]}:{k[1]}:{k[2]}")
def test_each_alarm_entry_point_is_driven_through_its_own_code(key):
    """A scenario named for an alarms entry point must REACH ``alarms.py`` through that function (``tests/door_reach.py`` traces
    who called each door function), not only be named for it. Control run: the clock-radio scenario calling ``alarms.log_event``
    itself instead of ``alarm_music._fail`` fails here for ``alarm_music.py:_log``."""
    import door_reach

    root = Path(__file__).resolve().parents[1]
    reached = set()
    for name in ALARMS_RECORDED[key]:
        reached |= door_reach.reached(lambda: scenarios.run_one(name), root, "alarms.py")
    if key in REACHED_BY_PROXY:            # the scenario runs the door itself; its named caller cannot be run here (reason given)
        assert ("<tests>", "<scenario>", REACHED_BY_PROXY[key][0]) in reached, sorted(reached)
        return
    assert key in reached, sorted(reached)


#: The two recorded entry points whose scenario runs the door function itself, not the caller it is named for, and why. Each
#: caller is a single unconditional call of that function (the coverage test reads it from the code).
REACHED_BY_PROXY = {
    ("memory_service.py", "MemoryService.delete_user", "forget_user"):
        ("forget_user", "the whole-member erase runs some forty stores' own forgets before this one; the scenario runs alarms.forget_user"),
    ("main.py", "lifespan", "run_loop"): ("tick", "the app's start-up; the scenario runs the loop's body (tick) on the frozen clock"),
}


def test_the_clock_radio_fallback_writes_one_event_row_and_commits_it(here):
    """``alarm_music._log``: one ``alarm_events`` row (the alarm, ``music_fallback``, the reason, the frozen clock) and its commit
    straight after it. Control run: the ``db.commit()`` dropped from ``_log`` fails here (and the record comparison)."""
    rec = here["music_did_not_start_logs_the_fallback"]
    at = next(i for i, e in enumerate(rec) if e[0] == "sql" and e[1].startswith("INSERT INTO alarm_events") and "music_fallback" in e[2])
    assert rec[at][2][1:] == ["a00000000001", scenarios.NIGHT, "music_fallback", "no panel player"] and rec[at + 1] == ["commit"]
    events = rec[-1][1]["alarm_events"]
    assert ["a00000000001", scenarios.NIGHT, "music_fallback", "no panel player"] in events
    assert rec[-2] == ["returned", {"reason": "no panel player", "since": scenarios.NIGHT, "state": "failed"}]


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

