"""The calendar has ONE door, and nothing about what any entry point does has changed against ``main``.

``tests/fixtures/calendar_as_on_main.json`` is the record of ``main`` (re-captured from b2bbe84ca): for each entry point and action, every
statement with its values (reads too), every commit, hook, broadcast and receipt, the result or refusal, and the rows the three
event tables hold afterwards, every column including attendee and alarm ids and the stored stamps (the clock is frozen, ids
are counted; nothing is masked). It was made by running ``tests/calendar_scenarios.py`` against an export of that commit
(``git archive origin/main services/zoe-data``), never by this branch's own code. The same scenarios run here and must give the
same record. This is also the "before the door" record the kernel-door tests compare flag-off behaviour with.

Covered: the screen's REST create / edit / delete (a member on their own and on a household event, another member, the panel, a
stranger, a fed read-only event, a missing one); the brain's ``calendar_create_event`` / ``calendar_update_event`` /
``calendar_delete_event`` tools; the spoken create, move and delete (and their guest, read-only, repeating and not-yours answers);
the spoken quick-add; an accepted suggestion; the pendant day review's event; an ``.ics`` import.

Negative controls run (each broke the code, saw the named scenario fail, restored): ``updated_at = NOW()`` dropped from
``delete_event_record`` -> ``rest_delete_own``; ``AND user_id=?`` dropped from ``tool_update_event_record`` ->
``tool_update``; ``all_day = 0`` dropped from ``spoken_move_event_record`` -> ``spoken_move_own``; ``'family'`` made
``'personal'`` in ``suggested_create_event_record`` -> ``suggestion_accepted``; an extra read added to
``spoken_create_event_record`` -> ``spoken_create``; the ``events.created_at`` default moved one second (the stored stamp the
recorder once masked) -> every scenario that leaves a row.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import calendar_scenarios as scenarios

pytestmark = pytest.mark.ci_safe
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "calendar_as_on_main.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


_REST, _TOOLS, _SKY = "routers/calendar.py", ("mcp_server.py", "_execute_tool"), "skybridge_service.py"
#: every (module, function, door function of ``calendar_service``) the CODE holds -> the scenarios that record it
RECORDED = {
    (_REST, "create_event", "create_event_record"): ("rest_create", "rest_create_private", "rest_create_by_the_panel"),
    (_REST, "create_event", "replace_attendees"): ("rest_create_with_notes_and_guests",),
    (_REST, "update_event", "update_event_record"): ("rest_edit_own", "rest_edit_household_event_by_another_member"),
    (_REST, "update_event", "replace_attendees"): ("rest_edit_guests_only",),
    (_REST, "delete_event", "delete_event_record"): ("rest_delete_own", "rest_delete_household_event_by_another_member"),
    (*_TOOLS, "create_event_record"): ("tool_create", "tool_create_a_health_event"),
    (*_TOOLS, "tool_update_event_record"): ("tool_update", "tool_update_another_members_household_event"),
    (*_TOOLS, "tool_delete_event_record"): ("tool_delete", "tool_delete_another_members_household_event"),
    (_SKY, "_resolve_calendar_create", "spoken_create_event_record"): ("spoken_create", "spoken_create_all_day"),
    (_SKY, "_resolve_calendar_update_time", "spoken_move_event_record"): ("spoken_move_own", "spoken_move_own_from_the_card"),
    (_SKY, "_resolve_calendar_delete_event", "spoken_delete_event_record"): ("spoken_delete_own", "spoken_delete_the_households_event"),
    ("intent_router.py", "_execute_calendar_create_direct", "create_event_record"): ("spoken_quick_add", "spoken_quick_add_today"),
    ("pending_suggestions.py", "_execute_action", "suggested_create_event_record"): ("suggestion_accepted",),
    ("omi_review_apply.py", "write_calendar_event", "create_event_record"): ("day_review_event",),
    ("calendar_ical.py", "store_parsed", "create_event_record"): ("ics_import",),
    ("calendar_ical.py", "store_parsed", "replace_attendees"): ("ics_import",),
    ("calendar_ical.py", "store_parsed", "replace_alarms"): ("ics_import",),
}
_FEED = "the subscribed feed's own reconcile (nobody is acting): it writes its rows itself and is named in test_calendar_one_door.py"
NOT_RECORDED = {
    ("calendar_feed.py", "_write_items", "replace_attendees"): _FEED,
    ("calendar_feed.py", "_write_items", "replace_alarms"): _FEED,
}


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    """The entry points are READ FROM THE CODE (``tests/door_callers.py``): every function of the service that names a writing
    function of ``calendar_service``. Each is recorded by named scenarios or listed with the reason it is not, so a new caller
    of the door fails here until it has one or the other (the hand-picked name prefixes this replaces could not see one).
    Control run: ``async def sneak(db): await calendar_service.delete_event_record(db, {}, "e")`` added to
    ``routers/calendar.py`` fails this naming it."""
    import door_callers
    import sql_scan

    assert set(ON_MAIN) == set(scenarios.scenarios())
    root = Path(__file__).resolve().parents[1]
    door = (root / "calendar_service.py").read_text("utf-8")
    writers = door_callers.door_functions(door, door_callers.direct_sql_writers(
        door, sql_scan.write_pattern("events", "event_attendees", "event_alarms", "calendars")))
    assert len(writers) >= 11
    door_callers.check_coverage(door_callers.entry_points(root, "calendar_service", writers), RECORDED, NOT_RECORDED, set(ON_MAIN))


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_calendar_entry_point_does_exactly_what_main_did(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_shows_writes_hooks_and_the_store_not_only_replies(here):
    """The comparison has something to bite on: a create's record holds its INSERT with values, the commit, the receipt, the
    broadcast and the rows left behind; the raw-connection paths record theirs the same way."""
    assert {"sql", "commit", "feature", "receipt", "broadcast", "returned", "rows"} <= {e[0] for e in here["rest_create"]}
    inserts = [e for e in here["rest_create"] if e[0] == "sql" and e[1].startswith("INSERT INTO events")]
    assert len(inserts) == 1 and "womble" in inserts[0][2] and "shed" in inserts[0][2]
    assert any(r["title"] == "womble" for r in here["rest_create"][-1][1]["events"])
    moved = [e for e in here["spoken_move_own"] if e[0] == "pg" and e[1].startswith("UPDATE events")]
    assert moved and moved[0][2] == ["09:00", "ev-ann-private", "ann-0001"]
    assert here["ics_import"][-1][1]["event_attendees"] and here["ics_import"][-1][1]["event_alarms"]
    # every kind of ending is in the record: a result, an HTTP refusal with its words, and a spoken "no"
    assert here["rest_edit_private_event_by_another_member"][-2] == ["raised", 404, "Event not found"]
    assert "cannot delete it" in here["spoken_delete_another_members_household_event"][-2][1]["spoken_summary"]
