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


_TOOLS, _ROUTER, _OFFER = ("mcp_server.py", "_execute_tool"), "intent_router.py", ("pending_suggestions.py", "_execute_action")
#: every (module, function, door function) the CODE holds, for ``note_service`` and for ``journal_service`` -> its scenarios
NOTES_RECORDED = {
    ("routers/notes.py", "create_note", "create_note_record"): ("note_rest_create", "note_rest_create_for_the_house"),
    ("routers/notes.py", "update_note", "update_note_record"): ("note_rest_edit", "note_rest_patch",
                                                               "note_rest_edit_a_household_note_by_another_member"),
    ("routers/notes.py", "delete_note", "delete_note_record"): ("note_rest_delete", "note_rest_delete_a_household_note_by_another_member"),
    (*_TOOLS, "said_note_record"): ("note_tool_create",),
    (*_TOOLS, "tool_update_note_record"): ("note_tool_update", "note_tool_update_another_members_household_note"),
    (*_TOOLS, "tool_delete_note_record"): ("note_tool_delete", "note_tool_delete_another_members_note"),
    (_ROUTER, "_execute_note_create_direct", "said_note_record"): ("note_spoken_create",),
    (*_OFFER, "suggested_note_record"): ("note_suggestion_accepted", "note_suggestion_accepted_with_a_description_only"),
}
JOURNAL_RECORDED = {
    ("routers/journal.py", "create_entry", "create_entry_record"): ("journal_rest_create",),
    ("routers/journal.py", "update_entry", "update_entry_record"): ("journal_rest_edit", "journal_rest_edit_another_members_entry"),
    ("routers/journal.py", "delete_entry", "delete_entry_record"): ("journal_rest_delete", "journal_rest_delete_another_members_entry"),
    (*_TOOLS, "tool_entry_record"): ("journal_tool_create",),
    (_ROUTER, "_execute_journal_create_direct", "said_entry_record"): ("journal_spoken_create",),
}


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    """The entry points are READ FROM THE CODE (``tests/door_callers.py``): every function of the service that names a writing
    function of ``note_service`` or of ``journal_service``. Each is tied to named scenarios; nothing is left unrecorded (the
    two empty dicts below). A new caller of either door fails here until it has a scenario. (The hand-picked name prefixes this
    replaces could not see one.)
    Control run: ``async def sneak(db): await note_service.delete_note_record(db, {}, "n")`` added to ``routers/notes.py`` fails
    this naming it; the same for ``journal_service.delete_entry_record`` in ``routers/journal.py``."""
    import door_callers
    import sql_scan

    assert set(ON_MAIN) == set(scenarios.scenarios())
    root = Path(__file__).resolve().parents[1]
    for module, table, recorded, at_least in (("note_service", "notes", NOTES_RECORDED, 7),
                                               ("journal_service", "journal_entries", JOURNAL_RECORDED, 5)):
        door = (root / f"{module}.py").read_text("utf-8")
        writers = door_callers.door_functions(door, door_callers.direct_sql_writers(door, sql_scan.write_pattern(table)))
        assert len(writers) >= at_least, module
        door_callers.check_coverage(door_callers.entry_points(root, module, writers), recorded, {}, set(ON_MAIN))


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
