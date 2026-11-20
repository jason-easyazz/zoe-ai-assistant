"""The calendar has ONE door, and nothing about what any entry point does has changed against ``main``.

``tests/fixtures/calendar_as_on_main.json`` is the record of ``main`` (5533e3b08): for each entry point and action, every
statement with its values (reads too), every commit, hook, broadcast and receipt, the result or refusal, and the rows the three
event tables hold afterwards. It was made by running ``tests/calendar_scenarios.py`` against an export of that commit
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
``spoken_create_event_record`` -> ``spoken_create``.
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


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    assert set(ON_MAIN) == set(scenarios.scenarios())
    for prefix in ("rest_create", "rest_edit", "rest_delete", "tool_create", "tool_update", "tool_delete", "spoken_create",
                   "spoken_move", "spoken_delete", "spoken_quick_add", "suggestion_accepted", "day_review_event", "ics_import"):
        assert any(name.startswith(prefix) for name in ON_MAIN), prefix


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
