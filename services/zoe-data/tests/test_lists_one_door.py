"""All ordinary list writes go through list_service.

The scan reads f-strings and concatenations whole and admits ``ONLY`` and any schema (``tests/sql_scan.py``). Control run: with
``sql_scan.texts`` skipping ``+`` expressions the planted ``'UPDATE ' + 'lists SET ...'`` below escapes."""
import ast
from pathlib import Path

import pytest

import sql_scan

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]
# Erasure uses dynamically named tables, so has no literal SQL exemption.
ERASERS = {}
WRITE = sql_scan.write_pattern("lists", "list_items")


def writes(source):
    return sql_scan.writes(source, WRITE)


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
    # a schema-qualified table, quoted or not, is still a list write
    for sql in ('UPDATE public.lists SET name = ?', 'UPDATE "public".lists SET name = ?', 'DELETE FROM "public"."list_items" WHERE id = ?'):
        assert writes(f"async def f(db):\n    await db.execute('{sql}')\n"), sql
    for table in ("lists", "list_items"):
        for planted in sql_scan.planted_forms(table):
            assert writes(planted), planted
    assert writes("async def f(db, sets):\n    await db.execute(f\"UPDATE lists SET {', '.join(sets)} WHERE id = ?\")\n")
    assert not writes("async def f(db):\n    await db.execute('UPDATE listings SET x = ?')\n")


def _remove_by_tick(call) -> bool:
    """A ``set_item_checked`` call that ticks an item OFF with a constant (``completed=1`` / ``True``) is a REMOVE command (voice, the
    brain tool); a screen tick / untick passes a variable and is not. Judged on the call itself, never on the function's name, so a
    remove inside a generic dispatcher (``mcp_server._execute_tool``) is still caught."""
    for kw in call.keywords:
        if kw.arg == "completed":
            return isinstance(kw.value, ast.Constant) and bool(kw.value.value)
    return False


def _is_hook(stmt) -> bool:
    """A statement that IS the hook call (``await note_item_left(...)`` / ``note_item_left(...)``), not one that merely contains it in a branch."""
    value = stmt.value if isinstance(stmt, ast.Expr) else None
    value = value.value if isinstance(value, ast.Await) else value
    return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "note_item_left"


def _hooked_after(function, call) -> bool:
    """Review (PR #2679): the hook must FOLLOW the remove on the remove's own path - a later statement of the block holding the
    remove, or of a block enclosing it, up to the function. A hook in a sibling branch (another operation of a dispatcher) or
    nested in a later ``if`` does not count."""
    parents = {child: node for node in ast.walk(function) for child in ast.iter_child_nodes(node)}
    node = call
    while node is not function:
        parent = parents[node]
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if isinstance(block, list) and node in block:
                if any(_is_hook(stmt) for stmt in block[block.index(node) + 1:]):
                    return True
        node = parent
    return False


def unhooked_removes(source):
    offenders = []
    for function in (n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for call in (n for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
            if call.func.id == "remove_item_record" or (call.func.id == "set_item_checked" and _remove_by_tick(call)):
                if not _hooked_after(function, call):
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
    # the hook in ANOTHER branch of a dispatcher does not cover this remove
    assert unhooked_removes("async def _execute_tool(op):\n    if op == 'remove':\n        await set_item_checked(completed=1)\n"
                            "    elif op == 'other':\n        await note_item_left()\n") == ["_execute_tool"]
    # nor does a hook nested under a later condition, nor one BEFORE the remove
    assert unhooked_removes("async def f(x):\n    await set_item_checked(completed=1)\n    if x:\n        await note_item_left()\n") == ["f"]
    assert unhooked_removes("async def f():\n    await note_item_left()\n    await set_item_checked(completed=1)\n") == ["f"]
    # the hook after the remove's enclosing block (the remove sits in a branch, the hook follows it) covers it
    assert not unhooked_removes("async def f(x):\n    if x:\n        await remove_item_record()\n    await note_item_left()\n")
