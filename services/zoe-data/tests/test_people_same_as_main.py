"""Contacts have ONE door, and nothing about what any entry point does has changed against ``main``.

``tests/fixtures/people_as_on_main.json`` is the record of ``main`` (5533e3b08): for each entry point and action, every
statement with its values (reads too), every commit, hook, memory mirror, broadcast and receipt, the result or refusal, and the
``people`` and ``person_relationships`` rows afterwards. It was made by running ``tests/people_scenarios.py`` against an export
of that commit (``git archive origin/main services/zoe-data``), never by this branch's own code. The same scenarios run here and
must give the same record. (Re-recorded in review from main at b2bbe84ca with EVERY column of a contact in the rows; the
statements, hooks and results of the 38 scenarios it already held came out identical.) This is also the "before the door" record the kernel-door tests compare flag-off behaviour with.

Covered: the screen's create, update and delete (their own, a household contact by another member, a private one by another
member) and its "add a relationship" (which can mint a stub); the brain's ``people_create`` / ``people_update`` /
``people_delete``; the spoken add and the same-person rename; Skybridge's "remember that <name> ..." (a new person, their own,
another member's); the panel's introduce; an accepted "add X as a contact" offer (new, a stub with and without a relation, a
full contact, a bad name, the feature off); a spoken correction (a pet, on the pooled handle and on a raw connection; a name); the day review's approved relationship and its
undo; an accepted offer that renames a one-name stub; the screen's edit of a link's notes, its retype and its removal; a
contact made with a phone and an email. NOT covered here, and not moved: the shared mechanics (``contacts_store``, ``person_merge``) and the automatic makers.

Negative controls run (each broke the code, saw the named scenario fail, restored): ``updated_at = NOW()`` dropped from
``screen_delete`` -> ``screen_delete_own``; ``AND user_id=?`` dropped from ``tool_delete`` -> ``tool_delete``;
``is_partial = 0`` dropped from ``said_rename`` -> ``said_rename``; ``is_partial=0`` made ``1`` in ``offer_promote`` ->
``offer_accepted_for_a_stub``; ``'family'`` moved one place in ``spoken_create``'s values (the caller's tuple) ->
``spoken_fact_about_a_new_person``; ``write_contact_either`` answering False -> ``corrected_to_a_pet``;
``relationship IS NULL`` dropped from ``reviewed_relation`` -> ``reviewed_relation``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import people_scenarios as scenarios

pytestmark = pytest.mark.ci_safe
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "people_as_on_main.json").read_text("utf-8"))


ROOT = Path(__file__).resolve().parents[1]
#: what the scenarios actually REACHED at the two doors while ``here`` ran them: ``people_service`` entries, ``people_graph`` writers
USED = {"entries": set(), "edges": set()}


def edge_writers() -> set:
    """The functions of ``people_graph`` that write ``person_relationships``, read from its code."""
    import door_callers
    import sql_scan

    door = (ROOT / "people_graph.py").read_text("utf-8")
    return door_callers.door_functions(door, door_callers.direct_sql_writers(door, sql_scan.write_pattern("person_relationships")))


@pytest.fixture(scope="module")
def here():
    """The scenarios' record, made with a spy at each door that only NOTES what was reached (the calls pass through untouched,
    so the record is the same with or without it: the comparison with main below would fail otherwise)."""
    import functools

    import people_graph
    import people_service

    kept = {(people_service, "write_contact"): people_service.write_contact}

    async def write_contact(db, entry, *a, **k):
        # noted only once the handle has RUN the statement (review of #2698: counting it when its SQL was merely built let
        # ``corrected_pet_pg`` count while the pooled handle refused the raw call and the other form ran instead)
        out = await kept[(people_service, "write_contact")](db, entry, *a, **k)
        USED["entries"].add(entry)
        return out

    people_service.write_contact = write_contact
    for name in edge_writers():
        real = kept[(people_graph, name)] = getattr(people_graph, name)

        def spy(*a, _real=real, _name=name, **k):
            USED["edges"].add(_name)
            return _real(*a, **k)

        setattr(people_graph, name, functools.wraps(real)(spy))
    try:
        return scenarios.run_all()
    finally:
        for (module, name), real in kept.items():
            setattr(module, name, real)


_SCREEN = "routers/people.py"
#: every (module, function, writer of ``people_graph``) the CODE holds -> the scenarios that record it, or why none does
EDGES_RECORDED = {
    (_SCREEN, "add_relationship", "insert_edge"): ("screen_relation_with_a_new_stub", "screen_relation_notes_edited"),
    (_SCREEN, "update_relationship", "set_edge_notes"): ("screen_relation_notes_edited",),
    (_SCREEN, "update_relationship", "replace_current_edge"): ("screen_relation_retyped",),
    (_SCREEN, "delete_relationship", "close_edge"): ("screen_relation_removed",),
    ("correction_apply.py", "_retype_edge_as_pet", "close_edge"): ("corrected_to_a_pet",),
    ("correction_apply.py", "_retype_edge_as_pet", "insert_edge"): ("corrected_to_a_pet",),
}
_AUTO = "automatic or shared mechanics, nobody acting at the door (named with its reason in test_people_one_door.py): not moved, not recorded"
EDGES_NOT_RECORDED = {
    ("contacts_store.py", "write_relation", "insert_edge"): _AUTO,
    ("person_extractor.py", "_write_relationship", "replace_current_edge"): _AUTO,
    ("person_extractor.py", "apply_edge_dispute", "replace_current_edge"): _AUTO,
    ("person_merge.py", "_repoint_relationships", "close_edge"): _AUTO,
}


def entries_named_by_callers() -> dict:
    """``{entry: [module, ...]}``: every literal entry name a module hands to ``people_service.write_contact`` /
    ``write_contact_either``, read from the code. A call whose entry is not a literal is reported under ``<not a literal>``."""
    import ast

    import door_callers

    named: dict = {}
    for rel, path in door_callers.modules(ROOT):
        if rel == "people_service.py":
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("write_contact", "write_contact_either"):
                names = node.args[1:3] if getattr(node.func, "attr", getattr(node.func, "id", "")) == "write_contact_either" else node.args[1:2]
                for arg in names:
                    key = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else "<not a literal>"
                    named.setdefault(key, []).append(rel)
    return named


def test_the_fixture_covers_every_scenario_and_every_entry_point(here):
    """Coverage is built from the DOORS, not from name prefixes (review of #2698: ``offer_upgrade`` and the edge-notes edit had
    no scenario and the prefix list could not tell).

    * ``people``: the door's entry points are the keys of ``people_service.SQL`` and ``_BUILT``. Every one must have been REACHED
      by a scenario while the record was made (the spy in ``here``), every one is named by a caller in the code, and no caller
      names an entry that is not there.
    * ``person_relationships``: the writers of ``people_graph`` are read from its code; every one was reached, and every
      (module, function, writer) the code holds is tied to scenarios or listed with its reason.

    Controls run: an entry ``"sneak"`` added to ``people_service.SQL`` -> this fails naming it (no scenario reaches it, no
    caller names it); a new writing function added to ``people_graph`` -> it fails naming it; a new
    ``async def sneak(db): await pg.close_edge(db, "u", "e", reason="x")`` in ``routers/people.py`` -> it fails naming it."""
    import door_callers
    import people_service

    assert set(ON_MAIN) == set(scenarios.scenarios())
    entries = set(people_service.SQL) | set(people_service._BUILT)
    assert len(entries) >= 22 and sorted(entries - USED["entries"]) == []            # every entry point was reached by a scenario
    named = entries_named_by_callers()
    assert "<not a literal>" not in named and set(named) == entries, sorted(set(named) ^ entries)
    writers = edge_writers()
    assert len(writers) >= 4 and sorted(writers - USED["edges"]) == []
    door_callers.check_coverage(door_callers.entry_points(ROOT, "people_graph", writers), EDGES_RECORDED, EDGES_NOT_RECORDED, set(ON_MAIN))


def test_a_correction_on_a_raw_connection_runs_the_dollar_statement(here):
    """``corrected_pet_pg`` is REACHED only where it runs: on the raw asyncpg connection its UPDATE is executed and the row it
    leaves is in the record; on the pooled handle the raw call is refused and the ``?`` form runs instead (review of #2698:
    the spy counted the entry when its SQL was built, so the only scenario never ran it).
    Controls run: ``WHERE id=$3 AND user_id=$4`` made ``WHERE user_id=$4`` in ``corrected_pet_pg`` (every contact of the
    member retyped) -> ``corrected_to_a_pet_on_a_raw_connection`` fails; ``relationship=$1`` made a fixed ``'pet'`` -> it
    fails; the raw scenario run on the pooled handle instead (the hole: the statement is built, refused, never run) -> the
    coverage test fails naming ``corrected_pet_pg``."""
    raw = here["corrected_to_a_pet_on_a_raw_connection"]
    runs = [e for e in raw if e[0] == "pg" and e[1].startswith("UPDATE people SET relationship=$1")]
    assert len(runs) == 1 and runs[0][2][0] == "pet dog" and runs[0][2][2:] == ["p-ann-family", scenarios.ANN]
    rows = {r["id"]: r for r in raw[-1][1]["people"]}
    assert rows["p-ann-family"]["relationship"] == "pet dog"
    assert [r["relationship"] for k, r in rows.items() if k != "p-ann-family" and "relationship" in r] == ["cousin", "boss"]
    pooled = here["corrected_to_a_pet"]
    assert not [e for e in pooled if e[0] == "pg"] and [e for e in pooled if e[0] == "sql" and e[1].startswith("UPDATE people SET relationship=?")]


def test_the_record_holds_every_column_of_a_contact(here):
    """A contact's phone, email and preferences are in the rows the record keeps (review of #2698: they were left out, so a
    write that changed one passed). Control run: ``email`` dropped from the ``tool_create`` statement's values (a ``None`` in
    its place) -> ``tool_create_with_contact_details`` fails; it passed with the columns left out of the rows."""
    made = [r for r in here["tool_create_with_contact_details"][-1][1]["people"] if r["name"] == scenarios.WORD]
    assert len(made) == 1 and made[0]["phone"] == "555-0100" and made[0]["email"] == "womble@example.invalid"
    for name, record in here.items():
        for row in record[-1][1]["people"]:
            assert {"health_score", "notification_count", "contact_count", "circle", "context", "is_partial"} <= set(row), name
    upgraded = [r for r in here["offer_accepted_upgrades_a_one_name_stub"][-1][1]["people"] if r["id"] == "p-ann-stub"]
    assert upgraded[0]["name"] == "Plover Zed" and upgraded[0]["is_partial"] == 0
    noted = here["screen_relation_notes_edited"][-1][1]["person_relationships"]
    assert [e["notes"] for e in noted] == ["met at the womble fair"]


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_contacts_entry_point_does_exactly_what_main_did(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_shows_writes_hooks_and_the_store_not_only_replies(here):
    """The comparison has something to bite on: a create's record holds its INSERT with values, the commit, the memory mirror,
    the health recalculation, the broadcast and the rows left behind; both handle shapes are recorded; a refusal has its words."""
    assert {"sql", "commit", "feature", "memory", "health", "broadcast", "returned", "rows"} <= {e[0] for e in here["screen_create"]}
    inserts = [e for e in here["screen_create"] if e[0] == "sql" and e[1].startswith("INSERT INTO people")]
    assert len(inserts) == 1 and "Womble" in inserts[0][2] and "likes wombles" in inserts[0][2]
    assert [e for e in here["offer_accepted"] if e[0] == "pg" and e[1].startswith("INSERT INTO people")]
    fact = [e for e in here["spoken_fact_about_their_own_contact"] if e[0] == "pg" and e[1].startswith("UPDATE people SET")]
    assert len(fact) == 1 and fact[0][1].endswith("WHERE id = $3 AND user_id = $4") and fact[0][2][-2:] == ["p-ann-private", "ann-0001"]
    assert here["screen_update_a_private_contact_by_another_member"][-2] == ["raised", 404, "Person not found"]
    assert here["screen_relation_with_a_new_stub"][-1][1]["person_relationships"]              # the edge landed, through people_graph
    assert here["reviewed_relation"][-1][1]["people"][1]["relationship"] == "neighbour"
