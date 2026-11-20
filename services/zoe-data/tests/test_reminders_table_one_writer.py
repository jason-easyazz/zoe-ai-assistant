"""Every write of the ``reminders`` table is in ``reminder_service`` (the reminders ONE DOOR).

Structural, over EVERY module of the service (tests and migrations aside), so a writer in a new file is caught too. Erasure
(a member's delete) names its tables dynamically and has no literal SQL here.

Negative control (run, then restored): ``UPDATE reminders SET deleted = 1`` planted in ``routers/reminders.py`` -> ``test_one_door``
fails naming that file. The planted cases below keep the scan itself honest; control run for the f-string case: with
``_texts`` reading constants only, the interpolated-schema forms are not caught and ``test_planted_write_is_caught`` fails;
control run for the schema: with the unquoted schema pattern back to letters and underscores, ``public1.reminders`` escapes and
the same test fails; control run for a concatenation: with ``sql_scan.texts`` skipping ``+`` expressions, the planted
``'UPDATE ' + 'reminders SET ...'`` escapes and the same test fails; control run for ONLY: with the optional ``ONLY`` dropped from
``sql_scan.write_pattern`` the planted ``UPDATE ONLY reminders`` escapes."""
import ast  # noqa: F401 (kept for readers of the scan: the helper below walks the tree)
from pathlib import Path

import pytest

import sql_scan

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]
DOOR = "reminder_service.py"
WRITE = sql_scan.write_pattern("reminders")


def writes(source: str) -> list:
    """Every text (f-strings and concatenations whole; ``tests/sql_scan.py``) that writes the table."""
    return sql_scan.writes(source, WRITE)


def test_one_door():
    modules = [p for p in ROOT.rglob("*.py") if not {"tests", "alembic", "node_modules", "__pycache__"} & set(p.relative_to(ROOT).parts)]
    assert len(modules) > 100 and ROOT / "routers" / "reminders.py" in modules and ROOT / "mcp_server.py" in modules
    offenders = {p.relative_to(ROOT).as_posix(): found for p in modules
                 if p.relative_to(ROOT).as_posix() != DOOR and (found := writes(p.read_text("utf-8")))}
    assert not offenders, offenders
    assert len(writes((ROOT / DOOR).read_text("utf-8"))) >= 5      # the door itself still holds the writes (the scan sees them)


def test_planted_write_is_caught():
    for sql in ("UPDATE reminders SET title = ?", "INSERT INTO reminders (id) VALUES (?)", "DELETE FROM reminders WHERE id = ?",
                'UPDATE "public"."reminders" SET x = ?', "insert or replace into reminders (id) values (1)",
                "UPDATE public.reminders SET x = ?", "UPDATE public1.reminders SET x = ?", "DELETE FROM app_2$x.reminders WHERE id = ?",
                'INSERT INTO "My Schema 1".reminders (id) VALUES (?)', "UPDATE public . reminders SET x = ?"):
        assert writes(f"async def f(db):\n    await db.execute('{sql}')\n"), sql
    assert writes("async def f(db, sets):\n    await db.execute(f\"UPDATE reminders SET {', '.join(sets)} WHERE id = ?\")\n")
    # a write split across the parts of an f-string (review of #2686): an interpolated schema, quoted or not, and a split keyword
    for sql in ('f"UPDATE {schema}.reminders SET x = ?"', 'f"DELETE FROM {schema}.reminders WHERE id = ?"',
                "f'INSERT INTO \"{schema}\".reminders (id) VALUES (?)'", 'f"UPDATE {schema}.reminders SET {col} = ?"'):
        assert writes(f"async def f(db, schema, col):\n    await db.execute({sql})\n"), sql
    # a write split by ``+``, and Postgres' ONLY (review of #2686: a scan that reads whole constants misses both)
    for planted in sql_scan.planted_forms("reminders"):
        assert writes(planted), planted
    assert not writes("async def f(db):\n    n = 1 + 2\n    await db.execute('SELECT * FROM ' + 'reminders')\n")
    assert not writes('async def f(db, t):\n    await db.execute(f"SELECT * FROM {t}.reminders WHERE id = ?")\n')
    assert not writes("async def f(db):\n    await db.execute('UPDATE reminders_archive SET x = ?')\n"
                      "    await db.execute('SELECT * FROM reminders WHERE id = ?')\n")
