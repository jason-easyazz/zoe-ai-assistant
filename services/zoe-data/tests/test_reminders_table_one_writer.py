"""Every write of the ``reminders`` table is in ``reminder_service`` (the reminders ONE DOOR).

Structural, over EVERY module of the service (tests and migrations aside), so a writer in a new file is caught too. Erasure
(a member's delete) names its tables dynamically and has no literal SQL here.

Negative control (run, then restored): ``UPDATE reminders SET deleted = 1`` planted in ``routers/reminders.py`` -> ``test_one_door``
fails naming that file. The planted cases below keep the scan itself honest."""
import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]
DOOR = "reminder_service.py"
WRITE = re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+(?:[\"`\[]?[a-z_]+[\"`\]]?\.)?[\"`\[]?reminders\b", re.I)


def writes(source: str) -> list:
    """Every string constant (f-string pieces included) that writes the table."""
    return [node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and WRITE.search(node.value)]


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
    assert not writes("async def f(db):\n    await db.execute('UPDATE reminders_archive SET x = ?')\n"
                      "    await db.execute('SELECT * FROM reminders WHERE id = ?')\n")
