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


def _remove_by_tick(call) -> bool:
    """A ``set_item_checked`` call that ticks an item OFF with a constant (``completed=1`` / ``True``) is a REMOVE command (voice, the
    brain tool); a screen tick / untick passes a variable and is not. Judged on the call itself, never on the function's name, so a
    remove inside a generic dispatcher (``mcp_server._execute_tool``) is still caught."""
    for kw in call.keywords:
        if kw.arg == "completed":
            return isinstance(kw.value, ast.Constant) and bool(kw.value.value)
    return False


def unhooked_removes(source):
    offenders = []
    for function in (n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        calls = sorted((n for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)),
                       key=lambda n: (n.lineno, n.col_offset))
        for call in calls:
            if call.func.id == "remove_item_record" or (call.func.id == "set_item_checked" and _remove_by_tick(call)):
                if not any(n.func.id == "note_item_left" and n.lineno > call.lineno for n in calls):
                    offenders.append(function.name)
    return offenders


def test_remove_callers_observe_after_write():
    offenders = {}
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if {"tests", "alembic"} & set(relative.parts) or relative.as_posix() == "list_service.py":
            continue
        found = unhooked_removes(path.read_text())
        if found:
            offenders[relative.as_posix()] = found
    assert not offenders, offenders


def test_remove_hook_scan_negative_control():
    assert unhooked_removes("async def remove_thing():\n    await set_item_checked(completed=1)\n") == ["remove_thing"]
    # a remove inside a generic dispatcher whose name says nothing about removing (the MCP tool path) is caught too
    assert unhooked_removes("async def _execute_tool():\n    await set_item_checked(completed=1)\n") == ["_execute_tool"]
    # a screen tick / untick passes a variable: not a remove
    assert not unhooked_removes("async def toggle():\n    await set_item_checked(completed=completed_val)\n")
    assert unhooked_removes("async def delete_thing():\n    await remove_item_record()\n") == ["delete_thing"]
    assert not unhooked_removes("async def remove_thing():\n    await set_item_checked(completed=1)\n    await note_item_left()\n")
