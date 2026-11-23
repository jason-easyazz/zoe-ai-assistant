"""Every ENTRY POINT of the lists door does, with the lists flag off, exactly what it did on ``main``: each statement with its
values, each read with its arguments, every announcement, the reply, and the list and item rows its writes leave.

``tests/fixtures/lists_entry_points_as_on_main.json`` is the record of ``main`` (b2bbe84ca, the commit this work starts from),
made by running ``tests/lists_entry_scenarios.py`` against an export of that commit, never by this branch's code. The door
functions themselves are pinned against the code before the door asked the kernel by ``test_lists_kernel_door.py``; this pins what
each CALLER hands them (the REST routes, the brain's tools, the spoken executors, skybridge, the background makers), so an entry
point that changes a name, a visibility or the list it picks fails here.

The entry points are READ FROM THE CODE (``tests/door_callers.py``: every function outside ``list_service`` naming a function of
it that writes a list table), and each must be REACHED by the scenarios named for it (``tests/door_reach.py``), not only named.

Negative controls run (each broke the code, saw the named test fail, restored): the REST create's visibility forced to
``family`` -> ``rest_create_list``; an accepted suggestion's new list made ``family`` for every type ->
``suggestion_list_add_new_list``; skybridge's new list made ``family`` for every type -> ``skybridge_add_to_a_new_list``; the
packing list made ``family`` -> ``anticipation_packing``; the brain's add tool given a default quantity -> ``tool_add_item``;
the spoken add's replay window set to 0 -> ``spoken_add``; a planted ``async def sneak(db): await delete_list_record(...)`` in
``routers/lists.py`` -> the coverage test, naming it; ``spoken_rename`` pointed at the skybridge rename -> the reach test for
``intent_router.py:_execute_list_rename_direct``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import lists_entry_scenarios as scenarios

pytestmark = pytest.mark.ci_safe
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "lists_entry_points_as_on_main.json").read_text("utf-8"))
ROOT = Path(__file__).resolve().parents[1]

_R, _S = "routers/lists.py", "skybridge_service.py"
#: every (module, function, door function) the CODE holds -> the scenarios that drive it
RECORDED = {
    (_R, "create_list", "create_list_record"): ("rest_create_list",),
    (_R, "update_list", "update_list_record"): ("rest_rename_list",),
    (_R, "delete_list", "delete_list_record"): ("rest_delete_list",),
    (_R, "add_item", "create_item_record"): ("rest_add_item",),
    (_R, "update_item", "update_item_record"): ("rest_edit_item",),
    (_R, "delete_item", "remove_item_record"): ("rest_delete_item",),
    ("mcp_server.py", "_execute_tool", "add_items_to_list"): ("tool_add_item",),
    ("mcp_server.py", "_execute_tool", "set_item_checked"): ("tool_remove_item",),
    ("intent_router.py", "_execute_list_add_direct", "add_items_to_list"): ("spoken_add",),
    ("intent_router.py", "_execute_list_remove_direct", "set_item_checked"): ("spoken_remove",),
    ("intent_router.py", "_execute_list_rename_direct", "rename_item_on_list"): ("spoken_rename",),
    (_S, "_add_spoken_items", "create_item_record"): ("skybridge_add",),
    (_S, "_ensure_default_lists", "create_list_record"): ("skybridge_add",),
    (_S, "_find_or_create_list", "create_list_record"): ("skybridge_add_to_a_new_list",),
    (_S, "_resolve_list_remove_item", "remove_item_record"): ("skybridge_remove",),
    (_S, "_resolve_list_complete_item", "set_item_checked"): ("skybridge_tick",),
    (_S, "_rename_and_answer", "rename_item_on_list"): ("skybridge_rename",),
    (_S, "_resolve_list_create", "create_list_record"): ("skybridge_create_list",),
    ("list_sharing.py", "set_list_visibility", "set_list_visibility_record"): ("share_a_private_list", "skybridge_share"),
    ("anticipation.py", "_do", "create_list_record"): ("anticipation_packing",),
    ("anticipation.py", "_do", "create_item_record"): ("anticipation_packing",),
    ("pending_suggestions.py", "_execute_action", "create_item_record"): ("suggestion_list_add",),
    ("pending_suggestions.py", "_execute_action", "create_list_record"): ("suggestion_list_add_new_list",),
    ("omi_review_apply.py", "_list_add", "add_item_to_list"): ("pendant_list_item",),
}
#: ... or why it is not driven here
NOT_RECORDED: dict = {}


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


def test_the_fixture_covers_every_scenario_and_every_entry_point():
    """Control run: a planted ``async def sneak(db): await list_service.delete_list_record(db, user_id="u", list_id="l")`` in
    ``routers/lists.py`` fails this test naming it."""
    import door_callers
    import sql_scan

    assert set(ON_MAIN) == set(scenarios.scenarios())
    door = (ROOT / "list_service.py").read_text("utf-8")
    writers = door_callers.door_functions(door, door_callers.direct_sql_writers(door, sql_scan.write_pattern("lists", "list_items")))
    assert len(writers) >= 10
    door_callers.check_coverage(door_callers.entry_points(ROOT, "list_service", writers), RECORDED, NOT_RECORDED, set(ON_MAIN))


@pytest.mark.parametrize("key", sorted(RECORDED), ids=lambda k: f"{k[0]}:{k[1]}:{k[2]}")
def test_each_entry_point_is_driven_through_its_own_code(key):
    """Control run: ``spoken_rename`` pointed at the skybridge rename fails here for ``_execute_list_rename_direct``."""
    import door_reach

    reached = set()
    for name in RECORDED[key]:
        reached |= door_reach.reached(lambda: scenarios.run_one(name), ROOT, "list_service.py")
    assert key in reached, sorted(reached)


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_entry_point_does_exactly_what_main_did(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_holds_values_and_rows_and_each_scenario_writes():
    """More than SQL text: every write carries its values, and every scenario but the refused unshare changed a row."""
    start = scenarios.Store([]).record_rows()
    for name, rec in ON_MAIN.items():
        writes = [e for e in rec["log"] if e[0] == "sql" and e[2].split()[0] in ("INSERT", "UPDATE", "DELETE")]
        if name == "unshare_the_household_shopping_list":
            assert writes == [] and rec["rows"] == start, name
            continue
        assert writes and all(e[3] for e in writes), name
        assert rec["rows"] != start, name
