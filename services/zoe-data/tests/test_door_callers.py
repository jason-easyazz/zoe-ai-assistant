"""``tests/door_callers.py`` finds a door's callers however the call is spelled (every area's "every entry point is covered"
check stands on it).

Control run: with the ``from door import fn`` branch of ``references`` skipped, the from-import and aliased forms are missed and
``test_a_caller_is_found_however_it_names_the_door`` fails."""
from __future__ import annotations

import door_callers
import pytest

pytestmark = pytest.mark.ci_safe
DOOR = """
async def _row(db):
    await db.execute("UPDATE things SET a = 1")

async def change(db):
    return await _row(db)

async def read(db):
    return await db.execute("SELECT 1")

class Store:
    async def put(self, db):
        await change(db)
"""
CALLERS = """
import thing_service
import thing_service as ts
from thing_service import change as alter, read

async def by_module(db):
    await thing_service.change(db)

async def by_alias(db):
    await ts.change(db)

async def by_name(db):
    await alter(db)

async def handed_on(run, db):
    await run(ts.change, db)

async def lazily(db):
    from thing_service import change
    await change(db)

async def only_reads(db):
    await read(db)
    await thing_service.read(db)

class Screen:
    async def save(self, db):
        await ts.change(db)

LATER = ts.change
"""


def test_the_doors_writing_functions_are_followed_through_its_own_helpers():
    assert door_callers.door_functions(DOOR, {"_row"}) == {"_row", "change", "Store.put"}


def test_a_caller_is_found_however_it_names_the_door():
    found = door_callers.references(CALLERS, "thing_service", {"change", "_row"})
    assert found == {name: {"change"} for name in ("by_module", "by_alias", "by_name", "handed_on", "lazily", "Screen.save", "<module>")}


def test_a_listed_entry_point_the_code_no_longer_holds_and_one_not_listed_both_fail():
    found = {("a.py", "f"): ["change"]}
    door_callers.check_coverage(found, {("a.py", "f", "change"): ("s1",)}, {}, {"s1"})
    for recorded, not_recorded, names in (({}, {}, {"s1"}),                                            # in the code, not listed
                                          ({("a.py", "f", "change"): ("s1",), ("b.py", "g", "change"): ("s1",)}, {}, {"s1"}),
                                          ({("a.py", "f", "change"): ("s2",)}, {}, {"s1"}),             # a scenario not in the fixture
                                          ({}, {("a.py", "f", "change"): "why"}, {"s1"})):             # no real reason given
        with pytest.raises(AssertionError):
            door_callers.check_coverage(found, recorded, not_recorded, names)


def test_writes_are_counted_per_function_and_outside_any():
    import sql_scan

    src = DOOR + 'LATER = "DELETE FROM things WHERE a = 1"\n\nasync def two(db):\n    await db.execute("UPDATE things SET a = 2")\n    await db.execute("INSERT INTO things (a) VALUES (1)")\n'
    assert door_callers.sql_writes_by_function(src, sql_scan.write_pattern("things")) == {"_row": 1, "two": 2, "<module>": 1}
