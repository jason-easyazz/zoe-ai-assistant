"""All ordinary list writes go through list_service."""
import ast
from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]
# Erasure uses dynamically named tables, so has no literal SQL exemption.
ERASERS = {}
WRITE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:[a-z_]+\.)?[\"`\[]?(?:lists|list_items)\b", re.I)


def writes(source):
    tree = ast.parse(source)
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and WRITE.search(node.value)]


def test_one_door():
    offenders = {}
    seen = set()
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if {"tests", "alembic"} & set(relative.parts) or relative.as_posix() == "list_service.py":
            continue
        found = writes(path.read_text())
        if found:
            name = relative.as_posix()
            if name in ERASERS:
                seen.add(name)
            else:
                offenders[name] = found
    assert seen == set(ERASERS), "stale eraser exemption"
    assert not offenders, offenders


def test_planted_write_is_caught():
    assert writes("async def f(db):\n    await db.execute('UPDATE list_items SET text = ?')\n")
