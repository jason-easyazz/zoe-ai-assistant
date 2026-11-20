"""Moving every reminder write into ``reminder_service`` changed NOTHING at any entry point (the three named fault fixes aside,
which have their own tests in ``test_reminders_one_door.py``).

``tests/fixtures/reminders_as_on_main.json`` is the record of ``main`` (5533e3b08, the commit this work starts from): for each
entry point and action, every statement with its values, every scheduled-job touch, every announcement, the role check, and the
result or refusal. It was made by running ``tests/reminders_scenarios.py`` against an export of that commit
(``git archive origin/main services/zoe-data``), never by this branch's own code, so a change that alters this branch is caught
even if it alters every caller alike. The same scenarios run here and must give the same record.

Covered: the screen's REST create / edit / delete / snooze / done (own rows, a family row by another member, recurring rows, a
form that re-sends the row, a re-sent past day); the brain's MCP create / update / delete / snooze; the spoken create. There is
no MCP "done" tool, skybridge writes no reminder, and the background makers (commitments, anticipation, the pendant review, the
panel cards) all call ``create_reminder_record``, whose record is the ``rest_create`` one.

Negative controls run (each broke the code, saw the named scenario fail, restored): ``is_active=0`` dropped from the delete
tool's statement -> ``tool_delete``; ``acknowledged = 0`` added to the screen's snooze -> ``rest_snooze_a_done_reminder``; the
edit tool's value normalised -> ``tool_update``; the role check added to the delete tool -> ``tool_delete``; the receipt moved
after the read-back in create -> ``rest_create``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import reminder_service as rs
import reminders_scenarios as scenarios

pytestmark = pytest.mark.ci_safe
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "reminders_as_on_main.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    assert set(ON_MAIN) == set(scenarios.scenarios())
    for prefix, actions in (("rest_", ("create", "edit", "delete", "snooze", "done")), ("tool_", ("create", "update", "delete", "snooze")),
                            ("spoken_", ("create",))):
        for action in actions:
            assert any(name.startswith(prefix + action) for name in ON_MAIN), prefix + action


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_moved_entry_point_does_exactly_what_main_did(here, name):
    assert here[name] == ON_MAIN[name]


def _writes(record) -> list:
    return [(e[1], e[2]) for e in record if e[0] == "sql" and not e[1].startswith("SELECT")]


def test_what_each_delete_path_sets_is_what_it_set_on_main(here):
    """The screen's delete marks the row deleted and leaves ``is_active``; the brain's tool also switches it off. Both as on main."""
    (rest_sql, _), = _writes(here["rest_delete"])
    (tool_sql, tool_params), = _writes(here["tool_delete"])
    assert rest_sql == "UPDATE reminders SET deleted = 1, updated_at = NOW() WHERE id = ?" and "is_active" not in rest_sql
    assert tool_sql == "UPDATE reminders SET deleted=1, is_active=0, updated_at=NOW() WHERE id=? AND user_id=?"
    assert tool_params == [scenarios.RID, scenarios.OWNER]
    assert ["cancel_jobs", [scenarios.RID], {}] in here["rest_delete"] and not any(e[0] == "feature" for e in here["tool_delete"])


def test_the_screens_snooze_sets_only_the_snooze_and_never_re_arms_a_done_reminder(here):
    for name in ("rest_snooze", "rest_snooze_a_done_reminder"):
        (sql, params), _notice = _writes(here[name])
        assert sql == ("UPDATE reminders SET snoozed_until = ?, updated_at = NOW(), "
                       "schedule_generation = COALESCE(schedule_generation, 0) + 1 WHERE id = ?")
        assert params == ["2026-06-10T04:10:00.123456Z", scenarios.RID]
    assert here["rest_snooze_a_done_reminder"][-1][1]["acknowledged"] is True


@pytest.mark.asyncio
async def test_the_snooze_tools_row_is_mains_due_date_time_snooze_and_done_mark():
    """On main the snooze TOOL never reaches its write: ``mcp_server._execute_tool`` binds ``datetime`` locally further down, so
    the tool raises before it (``tool_snooze*`` in the fixture: one read, then that error, no write). That is reproduced as it
    is. The row main's code WOULD write is kept statement for statement in ``tool_snooze_reminder_record``: the due date and
    time moved to the snoozed local time, the snooze instant, the done mark cleared; ``is_active`` and ``deleted`` untouched."""
    for name in ("tool_snooze", "tool_snooze_default_minutes_a_done_reminder", "tool_snooze_another_members_row"):
        assert _writes(ON_MAIN[name]) == [] and ON_MAIN[name][-1][0] == "raised"
    log: list = []
    await rs.tool_snooze_reminder_record(scenarios.DB(log), reminder_id=scenarios.RID, due_date="2026-06-10", due_time="12:30",
                                         snoozed_until="2026-06-10T04:30:00Z")
    assert log == [["sql", "UPDATE reminders SET due_date=?, due_time=?, snoozed_until=?, acknowledged=0, updated_at=NOW() WHERE id=?",
                    ["2026-06-10", "12:30", "2026-06-10T04:30:00Z", scenarios.RID]]]
    assert "is_active" not in log[0][1] and "deleted" not in log[0][1]


def test_the_edit_tool_writes_the_values_as_given_and_touches_no_job(here):
    (sql, params), = _writes(here["tool_update"])
    assert sql == "UPDATE reminders SET title=?,due_date=?,due_time=?,priority=?,updated_at=NOW() WHERE id=? AND user_id=?"
    assert params[1:3] == ["tomorrow", "9am"]                      # as on main: not normalised (unifying is a later decision)
    assert not any(e[0] in ("cancel_jobs", "schedule", "schedule_due", "feature", "broadcast") for e in here["tool_update"])
