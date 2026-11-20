"""Notes and the journal each have ONE door, and nothing about what any entry point does has changed against ``main``.

``tests/fixtures/notes_journal_as_on_main.json`` is the record of ``main`` (5533e3b08): for each entry point and action, every
statement with its values (reads too), every commit, hook, memory mirror, broadcast and receipt, the result or refusal, and the
rows both tables hold afterwards. It was made by running ``tests/notes_journal_scenarios.py`` against an export of that commit
(``git archive origin/main services/zoe-data``), never by this branch's own code. The same scenarios run here and must give the
same record. This is also the "before the door" record the kernel-door tests compare flag-off behaviour with.

Covered. NOTES: the screen's create (personal, for the house), edit, patch and delete (the author, another member on a household
and on a private note, a missing note); the brain's ``note_create`` / ``note_update`` / ``note_delete``; the spoken note; an
accepted suggestion. JOURNAL: the screen's create, edit and delete (the writer, another member); the brain's
``journal_create_entry``; the spoken entry.

Negative controls run (each broke the code, saw the named scenario fail, restored): ``updated_at = NOW()`` dropped from
``note_service.delete_note_record`` -> ``note_rest_delete``; ``AND user_id=?`` dropped from ``tool_update_note_record`` ->
``note_tool_update``; ``"personal"`` made ``"family"`` in ``said_note_record`` -> ``note_spoken_create``; an extra read added
to ``suggested_note_record`` -> ``note_suggestion_accepted``; ``'personal'`` made ``'family'`` in
``journal_service.create_entry_record`` -> ``journal_rest_create``; ``mood`` and ``title`` swapped in ``said_entry_record`` ->
``journal_spoken_create``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import notes_journal_scenarios as scenarios

pytestmark = pytest.mark.ci_safe
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "notes_journal_as_on_main.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    assert set(ON_MAIN) == set(scenarios.scenarios())
    for prefix in ("note_rest_create", "note_rest_edit", "note_rest_patch", "note_rest_delete", "note_tool_create", "note_tool_update",
                   "note_tool_delete", "note_spoken_create", "note_suggestion_accepted", "journal_rest_create", "journal_rest_edit",
                   "journal_rest_delete", "journal_tool_create", "journal_spoken_create"):
        assert any(name.startswith(prefix) for name in ON_MAIN), prefix


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_notes_and_journal_entry_point_does_exactly_what_main_did(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_shows_writes_hooks_and_the_store_not_only_replies(here):
    """The comparison has something to bite on: a create's record holds its INSERT with values, the commit, the memory mirror,
    the broadcast and the rows left behind; a refusal is recorded with its words."""
    assert {"sql", "commit", "feature", "memory", "broadcast", "returned", "rows"} <= {e[0] for e in here["note_rest_create"]}
    inserts = [e for e in here["note_rest_create"] if e[0] == "sql" and e[1].startswith("INSERT INTO notes")]
    assert len(inserts) == 1 and "womble" in inserts[0][2] and "bring the womble" in inserts[0][2]
    made = [e for e in here["journal_tool_create"] if e[0] == "sql" and e[1].startswith("INSERT INTO journal_entries")]
    assert len(made) == 1 and "a day of wombles" in made[0][2] and '["a", "b"]' in made[0][2]
    assert [e for e in here["note_suggestion_accepted"] if e[0] == "pg" and e[1].startswith("INSERT INTO notes")]
    assert here["note_rest_edit_a_household_note_by_another_member"][-2] == ["raised", 403, "Not authorised to edit this note"]
    assert here["journal_rest_edit_another_members_entry"][-2] == ["raised", 404, "Entry not found"]
    assert any(r["title"] == "womble" for r in here["journal_rest_create"][-1][1]["journal_entries"])
