"""Notes and the journal each have ONE door. Structural, over EVERY module of the service (tests and migrations aside).

``notes`` is written only by ``note_service.py`` and ``journal_entries`` only by ``journal_service.py``. There is no named
exception: nothing makes a note or a journal entry automatically, and nothing erases one row at a time (the scan would name any
module that started to).

The scan reads an f-string whole (``x`` standing where a value is interpolated), allows any schema name and Postgres' ``ONLY``,
and is not fooled by ``conversation_notes`` / ``night_notes`` (other tables whose names END in ``notes``) or by a column called
``notes``.

Negative controls run on the real tree (then restored): the raw ``INSERT INTO notes`` put back in ``pending_suggestions.py`` ->
``test_only_the_doors_write_notes_and_the_journal`` fails naming it; ``UPDATE journal_entries SET title = ?`` planted in
``routers/journal.py`` -> the same test fails naming it. The planted cases below keep the scan itself honest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sql_scan

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]

DOORS = {"notes": "note_service.py", "journal_entries": "journal_service.py"}     # table -> its door, by RELATIVE PATH


def _modules() -> list:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return sorted(p for p in ROOT.rglob("*.py") if not skip.intersection(p.relative_to(ROOT).parts))


def writes(source: str, table: str) -> list:
    """f-strings and concatenations whole, any schema, ONLY (``tests/sql_scan.py``)."""
    return [text[:80] for text in sql_scan.writes(source, sql_scan.write_pattern(table))]


def test_only_the_doors_write_notes_and_the_journal():
    rel = {p.relative_to(ROOT).as_posix(): p for p in _modules()}
    assert len(rel) > 100 and {"routers/notes.py", "routers/journal.py", "mcp_server.py", "intent_router.py", "pending_suggestions.py",
                               *DOORS.values()} <= set(rel)
    for table, door in DOORS.items():
        offenders = {name: found for name, p in rel.items() if name != door and (found := writes(p.read_text("utf-8"), table))}
        assert not offenders, (table, offenders)
        assert len(writes(rel[door].read_text("utf-8"), table)) >= 5, table        # the door itself holds the writes (the scan sees them)


def test_a_planted_write_is_caught():
    for table in DOORS:
        for sql in (f"UPDATE {table} SET title = ?", f"INSERT INTO {table} (id) VALUES (?)", f"DELETE FROM {table} WHERE id = ?",
                    f'UPDATE "public"."{table}" SET x = ?', f"UPDATE public1.{table} SET x = ?", f"DELETE FROM ONLY {table} WHERE id = ?",
                    f"insert or replace into {table} (id) values (1)"):
            assert writes(f"async def f(db):\n    await db.execute('{sql}')\n", table), sql
        for sql in (f'f"UPDATE {{schema}}.{table} SET x = ?"', f"f\"UPDATE {table} SET {{', '.join(sets)}} WHERE id = ?\""):
            assert writes(f"async def f(db, schema, sets):\n    await db.execute({sql})\n", table), sql
        for planted in sql_scan.planted_forms(table):         # a write split by ``+``, and Postgres' ONLY
            assert writes(planted, table), planted
    # other tables whose names end in ``notes``, a column called notes, a longer name, and a read are not writes of ``notes``
    assert not writes("async def f(db):\n    await db.execute('INSERT INTO conversation_notes (id) VALUES (?)')\n"
                      "    await db.execute('UPDATE people SET notes = ? WHERE id = ?')\n    await db.execute('SELECT * FROM notes')\n"
                      "    await db.execute('UPDATE notes_archive SET x = 1')\n", "notes")
    assert not writes("async def f(db):\n    await db.execute('SELECT * FROM journal_entries')\n"
                      "    await db.execute('UPDATE journal_entries_old SET x = 1')\n", "journal_entries")
