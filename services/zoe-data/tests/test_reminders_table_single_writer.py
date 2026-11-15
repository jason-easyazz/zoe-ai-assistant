"""Structural: no module outside ``reminder_service`` writes the ``reminders`` table with its own SQL (synthetic fixtures only).

Every change to a reminder (create, edit, snooze, done, cancel) goes through the store, which normalises the time, refuses a past
one, cancels and re-makes the scheduled job and broadcasts. A second writer skips all of that. SQL is found in the string
constants of each module (``ast``, so a multi-line or f-string statement is seen), not by a line regex."""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {"tests", "alembic", "__pycache__", ".venv", "venv", "node_modules"}
# INSERT INTO / UPDATE / DELETE FROM the table `reminders` as a whole word (not proactive_scheduled, reminder_claims, ...).
_WRITE = re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?\"?reminders\b(?!\w)", re.I)

# file -> why it may write `reminders` directly. Each is an ERASER or a system sweep that must not run the edit rules.
ALLOWED = {
    "reminder_service.py": "the store itself",
    "member_owned_forget.py": "member delete: erases the member's own rows (a past or nonsense row must still be removable)",
    "forget_cascade.py": "forget: scrubs reminders naming a forgotten thing; an eraser, not an edit",
}


def writers(root: Path = ROOT) -> list[tuple[str, int]]:
    found = set()  # an f-string's constant parts are nodes of their own: one hit per statement
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if _SKIP_DIRS.intersection(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):  # an f-string: its constant parts, the expressions as a placeholder
                text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            else:
                continue
            if _WRITE.search(text):
                found.add((str(rel), node.lineno))
    return sorted(found)


def test_no_module_outside_the_store_writes_reminders():
    stray = [(f, n) for f, n in writers() if f not in ALLOWED]
    assert not stray, "write the reminders table through reminder_service, or add an eraser to ALLOWED with a reason: " + \
        ", ".join(f"{f}:{n}" for f, n in stray)


def test_the_scan_catches_a_planted_writer(tmp_path):
    (tmp_path / "planted.py").write_text(
        'async def f(db):\n    await db.execute(\n        """UPDATE reminders\n           SET title = ?"""\n    )\n')
    (tmp_path / "planted_f.py").write_text('async def g(db, t):\n    await db.execute(f"DELETE FROM reminders WHERE id = {t}")\n')
    (tmp_path / "innocent.py").write_text(
        'X = "UPDATE proactive_scheduled SET fired = 1"\nY = "SELECT * FROM reminders"\nZ = "INSERT INTO reminder_claims (id)"\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text('S = "DELETE FROM reminders"\n')
    assert writers(tmp_path) == [("planted.py", 3), ("planted_f.py", 2)]
