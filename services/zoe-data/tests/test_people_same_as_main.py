"""Contacts have ONE door, and nothing about what any entry point does has changed against ``main``.

``tests/fixtures/people_as_on_main.json`` is the record of ``main`` (5533e3b08): for each entry point and action, every
statement with its values (reads too), every commit, hook, memory mirror, broadcast and receipt, the result or refusal, and the
``people`` and ``person_relationships`` rows afterwards. It was made by running ``tests/people_scenarios.py`` against an export
of that commit (``git archive origin/main services/zoe-data``), never by this branch's own code. The same scenarios run here and
must give the same record. This is also the "before the door" record the kernel-door tests compare flag-off behaviour with.

Covered: the screen's create, update and delete (their own, a household contact by another member, a private one by another
member) and its "add a relationship" (which can mint a stub); the brain's ``people_create`` / ``people_update`` /
``people_delete``; the spoken add and the same-person rename; Skybridge's "remember that <name> ..." (a new person, their own,
another member's); the panel's introduce; an accepted "add X as a contact" offer (new, a stub with and without a relation, a
full contact, a bad name, the feature off); a spoken correction (a pet, a name); the day review's approved relationship and its
undo. NOT covered here, and not moved: the shared mechanics (``contacts_store``, ``person_merge``) and the automatic makers.

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


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    assert set(ON_MAIN) == set(scenarios.scenarios())
    for prefix in ("screen_create", "screen_update", "screen_delete", "screen_relation", "tool_create", "tool_update", "tool_delete",
                   "said_add", "said_rename", "spoken_fact", "introduced", "offer_accepted", "corrected_to_a_pet", "corrected_name",
                   "reviewed_relation"):
        assert any(name.startswith(prefix) for name in ON_MAIN), prefix


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
