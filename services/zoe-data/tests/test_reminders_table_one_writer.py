"""Every write of the ``reminders`` table is in ``reminder_service`` (the reminders ONE DOOR).

Structural, over EVERY module of the service (tests and migrations aside), so a writer in a new file is caught too. Erasure
(a member's delete) names its tables dynamically and has no literal SQL here.

Negative control (run, then restored): ``UPDATE reminders SET deleted = 1`` planted in ``routers/reminders.py`` -> ``test_one_door``
fails naming that file. The planted cases below keep the scan itself honest; control run for the f-string case: with
``_texts`` reading constants only, the interpolated-schema forms are not caught and ``test_planted_write_is_caught`` fails."""
import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]
DOOR = "reminder_service.py"
WRITE = re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+(?:[\"`\[]?[a-z_]+[\"`\]]?\.)?[\"`\[]?reminders\b", re.I)


def _texts(tree):
    """Every SQL-bearing string of a module: plain constants, and each f-string put back together with ``x`` standing where a
    value is interpolated (so ``f"UPDATE {schema}.reminders SET ..."`` reads ``UPDATE x.reminders SET ...``; its pieces alone
    would each look harmless)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield "".join(part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "x" for part in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def writes(source: str) -> list:
    """Every string (f-strings whole) that writes the table."""
    return [text for text in _texts(ast.parse(source)) if WRITE.search(text)]


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
                "UPDATE public.reminders SET x = ?"):
        assert writes(f"async def f(db):\n    await db.execute('{sql}')\n"), sql
    assert writes("async def f(db, sets):\n    await db.execute(f\"UPDATE reminders SET {', '.join(sets)} WHERE id = ?\")\n")
    # a write split across the parts of an f-string (review of #2686): an interpolated schema, quoted or not, and a split keyword
    for sql in ('f"UPDATE {schema}.reminders SET x = ?"', 'f"DELETE FROM {schema}.reminders WHERE id = ?"',
                "f'INSERT INTO \"{schema}\".reminders (id) VALUES (?)'", 'f"UPDATE {schema}.reminders SET {col} = ?"'):
        assert writes(f"async def f(db, schema, col):\n    await db.execute({sql})\n"), sql
    assert not writes('async def f(db, t):\n    await db.execute(f"SELECT * FROM {t}.reminders WHERE id = ?")\n')
    assert not writes("async def f(db):\n    await db.execute('UPDATE reminders_archive SET x = ?')\n"
                      "    await db.execute('SELECT * FROM reminders WHERE id = ?')\n")
