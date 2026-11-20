"""Contacts have ONE door. Structural, over EVERY module of the service (tests and migrations aside).

``people`` is written by anything a person does only through ``people_service.py``; ``person_relationships`` only through
``people_graph.py``. Every other writer is named here by its PATH (never a file name), with why it is not at the door:

  * SHARED MECHANICS, used by automatic work and by a member's action alike: ``contacts_store.py`` (the address-book store) and
    ``person_merge.py`` (a merge re-points and folds rows). The member's action that reaches them asks at the door first.
  * AUTOMATIC, nobody acting: ``person_extractor.py`` and ``named_relations.py`` (extractors), ``person_health.py`` (a score).
  * THE ERASER and its undo: ``memory_forget_cascade.py``.
  * NOT YET BEHIND THE DOOR: the screen's two counters in ``routers/people.py`` (mark-read, log-activity), allowed by their
    exact text, and the side tables (contact fields, sources, links, merge reviews, synced cards, important dates, activities,
    gift ideas, bucket lists, custom fields): 55 statements in 6 modules, counted below so the number cannot drift unseen.

The scan reads an f-string whole (``x`` standing where a value is interpolated), allows any schema name and Postgres' ``ONLY``,
and is not fooled by ``people_field_values`` or ``source_contacts`` (other tables whose names start or end alike).

Negative controls run on the real tree (then restored): the raw ``INSERT INTO people`` put back in ``routers/voice_tts.py`` ->
``test_only_the_door_and_the_named_modules_write_people`` fails naming it; a third ``UPDATE people`` planted in
``routers/people.py`` -> the same test fails naming it; ``UPDATE person_relationships SET notes`` put back in
``routers/people.py`` -> ``test_only_the_graph_writes_relationships`` fails naming it. The planted cases keep the scan honest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sql_scan

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]

PEOPLE_DOOR, EDGE_DOOR = "people_service.py", "people_graph.py"
PEOPLE_NAMED = {
    "contacts_store.py": "shared mechanics: the address-book store (card import, voice learning, the day review, a voice tag)",
    "person_merge.py": "shared mechanics: a merge",
    "person_extractor.py": "automatic: an extractor mints stubs and bumps counters; nobody is acting",
    "named_relations.py": "automatic: an extractor mints the people a member's relations name; nobody is acting",
    "person_health.py": "automatic: a score",
    "memory_forget_cascade.py": "the eraser, and its undo",
}
EDGE_NAMED = {"person_merge.py": "shared mechanics: a merge re-points edges", "memory_forget_cascade.py": "the eraser, and its undo"}
#: the screen's two counters, by their exact text: not yet behind the door (a counter names no change a person would be refused)
NOT_YET = {"routers/people.py": {
    "UPDATE people SET notification_count = 0, updated_at = NOW() WHERE id = ? AND user_id = ?",
    "UPDATE people SET contact_count = contact_count + 1, last_contacted_at = ?, updated_at = NOW() WHERE id = ? AND user_id = ?"}}
SIDE_TABLES = ("contact_fields", "contact_sources", "contact_merge_reviews", "person_contact_links", "source_contacts",
               "person_important_dates", "person_activities", "person_gift_ideas", "person_bucket_list", "people_field_values",
               "people_field_definitions")
SIDE_STATEMENTS = {"contacts_store.py": 27, "correction_apply.py": 2, "database.py": 1, "person_extractor.py": 8, "person_merge.py": 6,
                   "routers/people.py": 11}


def _modules() -> dict:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return {p.relative_to(ROOT).as_posix(): p for p in sorted(ROOT.rglob("*.py")) if not skip.intersection(p.relative_to(ROOT).parts)}


def writes(source: str, table: str) -> set:
    """The distinct statement texts (whitespace folded; f-strings and concatenations whole, any schema, ONLY:
    ``tests/sql_scan.py``) in ``source`` that write ``table``."""
    return set(sql_scan.writes(source, sql_scan.write_pattern(table)))


def test_only_the_door_and_the_named_modules_write_people():
    rel = _modules()
    assert len(rel) > 100 and {"routers/people.py", "routers/voice_tts.py", "mcp_server.py", "intent_router.py", "skybridge_service.py",
                               "pending_suggestions.py", "correction_apply.py", "omi_review_apply.py", PEOPLE_DOOR, *PEOPLE_NAMED} <= set(rel)
    offenders = {}
    for name, path in rel.items():
        found = writes(path.read_text("utf-8"), "people") - NOT_YET.get(name, set())
        if found and name != PEOPLE_DOOR and name not in PEOPLE_NAMED:
            offenders[name] = sorted(found)
    assert not offenders, offenders
    assert len(writes(rel[PEOPLE_DOOR].read_text("utf-8"), "people")) >= 20            # the door itself holds the writes (the scan sees them)
    for name in PEOPLE_NAMED:                                                          # each named exception does write (none is stale)
        assert writes(rel[name].read_text("utf-8"), "people"), name
    for name, allowed in NOT_YET.items():                                              # ... and each allowed counter is still there
        assert allowed <= writes(rel[name].read_text("utf-8"), "people"), name


def test_only_the_graph_writes_relationships():
    rel = _modules()
    offenders = {name: sorted(found) for name, path in rel.items()
                 if name != EDGE_DOOR and name not in EDGE_NAMED and (found := writes(path.read_text("utf-8"), "person_relationships"))}
    assert not offenders, offenders
    assert len(writes(rel[EDGE_DOOR].read_text("utf-8"), "person_relationships")) >= 3
    for name in EDGE_NAMED:
        assert writes(rel[name].read_text("utf-8"), "person_relationships"), name


def test_what_is_not_yet_behind_the_door_is_counted():
    """The side tables are written where they always were: 55 statements in 6 modules. A new one changes the count and fails
    here, so "not yet" cannot quietly grow."""
    rel = _modules()
    counted = {}
    for name, path in rel.items():
        source = path.read_text("utf-8")
        n = sum(len(writes(source, table)) for table in SIDE_TABLES)
        if n:
            counted[name] = n
    assert counted == SIDE_STATEMENTS and sum(counted.values()) == 55


def test_a_planted_write_is_caught():
    for table in ("people", "person_relationships"):
        for sql in (f"UPDATE {table} SET name = ?", f"INSERT INTO {table} (id) VALUES (?)", f"DELETE FROM {table} WHERE id = ?",
                    f'UPDATE "public"."{table}" SET x = ?', f"UPDATE public1.{table} SET x = ?", f"DELETE FROM ONLY {table} WHERE id = ?",
                    f"insert or replace into {table} (id) values (1)"):
            assert writes(f"async def f(db):\n    await db.execute('{sql}')\n", table), sql
        for sql in (f'f"UPDATE {{schema}}.{table} SET x = ?"', f"f\"UPDATE {table} SET {{', '.join(sets)}} WHERE id = ?\""):
            assert writes(f"async def f(db, schema, sets):\n    await db.execute({sql})\n", table), sql
    for table in ("people", "person_relationships"):          # a write split by ``+``, and Postgres' ONLY
        for planted in sql_scan.planted_forms(table):
            assert writes(planted, table), planted
    assert not writes("async def f(db):\n    await db.execute('INSERT INTO people_field_values (id) VALUES (?)')\n"
                      "    await db.execute('UPDATE source_people SET x = 1')\n    await db.execute('SELECT * FROM people')\n"
                      "    await db.execute('UPDATE people_archive SET x = 1')\n", "people")
    # an allowed counter is allowed by its exact text only: a changed one is a new statement
    changed = "async def f(db):\n    await db.execute('UPDATE people SET notification_count = 5 WHERE id = ?')\n"
    assert writes(changed, "people") - NOT_YET["routers/people.py"]
