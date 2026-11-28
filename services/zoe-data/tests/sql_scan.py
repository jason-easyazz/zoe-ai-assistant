"""What every area's one-writer scan reads, in ONE place: the SQL a module could send, and what counts as a write of a table.

A statement can be hidden from a scan that reads only whole string constants. This reads each of these as ONE text:
  * a plain string constant (Python has already joined adjacent literals);
  * an f-string, with ``x`` standing where a value is interpolated (``f"UPDATE {schema}.reminders ..."`` -> ``UPDATE x.reminders ...``);
  * a CONCATENATION, however it is nested: ``"UPDATE " + "reminders SET ..."`` -> ``UPDATE reminders SET ...``; a part that is
    not a string (a name, a call) reads ``x``.

A write is ``INSERT [OR ...] INTO`` / ``UPDATE`` / ``DELETE FROM``, an optional Postgres ``ONLY``, an optional schema (unquoted or
quoted), then the table's exact name.

Controls are run by each area's own scan test (a planted concatenation and a planted ``UPDATE ONLY`` in ``planted_forms``).
"""
from __future__ import annotations

import ast
import re

#: an optional schema before the table: an unquoted identifier (letters, digits, ``_``, ``$``; not starting with a digit) or a quoted one
SCHEMA = r"(?:(?:[A-Za-z_][A-Za-z0-9_$]*|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])\s*\.\s*)?"


def write_pattern(*tables: str):
    """A compiled pattern matching a write of any of ``tables`` (exact names: ``reminders`` is not ``reminders_archive``)."""
    return re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?" + SCHEMA
                      + r"[\"`\[]?(?:" + "|".join(re.escape(t) for t in tables) + r")\b", re.I)


def _fold(node) -> str:
    """The text of a string-building expression: constants as they are, an f-string whole, ``+`` joined, anything else ``x``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_fold(part) if isinstance(part, ast.Constant) else "x" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _fold(node.left) + _fold(node.right)
    return "x"


def texts(tree):
    """Every SQL-bearing text of a module (see the module docstring)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.JoinedStr, ast.BinOp)) or (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Add):
                continue
            yield _fold(node)


def writes(source: str, pattern) -> list:
    """The distinct texts in ``source`` that ``pattern`` calls a write, whitespace folded, in order of first appearance."""
    seen: dict = {}
    for text in texts(ast.parse(source)):
        if pattern.search(text):
            seen.setdefault(" ".join(text.split()), None)
    return list(seen)


def planted_forms(table: str) -> list:
    """Source snippets that each write ``table`` in a form a naive scan misses; every area's scan test asserts each is caught."""
    return [
        f"async def f(db):\n    await db.execute('UPDATE ' + '{table} SET x = ?')\n",
        f"async def f(db):\n    await db.execute('DELETE ' + 'FROM ' + '{table}' + ' WHERE id = ?')\n",
        f"async def f(db, where):\n    await db.execute('UPDATE {table} SET x = 1 WHERE ' + where)\n",
        f"async def f(db, schema):\n    await db.execute('INSERT INTO ' + schema + '.{table} (id) VALUES (?)')\n",
        f"async def f(db, cols):\n    sql = 'UPDATE ' + f'{table} SET {{cols}}'\n    await db.execute(sql)\n",
        f"async def f(db):\n    await db.execute('UPDATE ONLY {table} SET x = ?')\n",
        f"async def f(db):\n    await db.execute('DELETE FROM ONLY public.{table} WHERE id = ?')\n",
        f"async def f(db):\n    await db.execute('update only ' 'app_2$x.{table} set x = ?')\n",
    ]
