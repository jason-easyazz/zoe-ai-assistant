"""Which functions of the service reach an area's ONE DOOR, read from the code (so a pin's "every entry point is covered"
check cannot be satisfied by a hand-picked list that a new caller is missing from).

``door_functions(source, direct)``: the door module's functions that write, directly (``direct``: names found by the area's
own write scan) or through another function of the module, any depth.
``entry_points(root, door_module, names)``: every ``(module, function)`` OUTSIDE the door that names one of those functions
(``door.fn(...)``, ``from door import fn`` then ``fn(...)``, an alias of either, or the function handed on uncalled), mapped to
the door functions it names. The function is the outermost one holding the reference (``Class.method`` for a method;
``<module>`` at module level).

Negative controls are run by each area's own coverage test (a planted caller in another module must be reported).
"""
from __future__ import annotations

import ast
from pathlib import Path

SKIP = {"tests", "alembic", "node_modules", "__pycache__", ".venv", "venv", "scripts"}


def modules(root: Path):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if not SKIP & set(rel.parts):
            yield rel.as_posix(), path


def _top_functions(tree):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{sub.name}", sub


def door_functions(source: str, direct) -> set:
    """Every top-level function (or method) of the door module that writes: named in ``direct``, or naming one that does."""
    fns = dict(_top_functions(ast.parse(source)))
    short = {name.rsplit(".", 1)[-1]: name for name in fns}
    found = {short.get(d, d) for d in direct if short.get(d, d) in fns}
    changed = True
    while changed:
        changed = False
        for name, fn in fns.items():
            if name in found:
                continue
            named = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
            if any(f.rsplit(".", 1)[-1] in named for f in found):
                found.add(name)
                changed = True
    return found


def references(source: str, door_module: str, names) -> dict:
    """``{function: {door functions it names}}`` for one module's source."""
    tree = ast.parse(source)
    leaf = door_module.rsplit(".", 1)[-1]
    module_aliases, direct = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == door_module or a.name.endswith("." + leaf):
                    if a.asname or "." not in a.name:       # ``import pkg.door`` is read at its use: ``pkg.door.fn``
                        module_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").rsplit(".", 1)[-1] == leaf:
                for a in node.names:
                    if a.name in names:
                        direct[a.asname or a.name] = a.name
            else:
                for a in node.names:
                    if a.name == leaf:
                        module_aliases.add(a.asname or a.name)
    out: dict = {}

    def scan(owner: str, node) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr in names:
                base = sub.value
                if (isinstance(base, ast.Name) and base.id in module_aliases) or (isinstance(base, ast.Attribute) and base.attr == leaf):
                    out.setdefault(owner, set()).add(sub.attr)
            elif isinstance(sub, ast.Name) and sub.id in direct and isinstance(sub.ctx, ast.Load):
                out.setdefault(owner, set()).add(direct[sub.id])

    owned = set()
    for name, fn in _top_functions(tree):
        scan(name, fn)
        owned.update(id(n) for n in ast.walk(fn))
    rest = ast.Module(body=[n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))], type_ignores=[])
    scan("<module>", rest)
    for node in tree.body:                                  # class bodies outside their methods
        if isinstance(node, ast.ClassDef):
            scan(f"{node.name}.<class>", ast.Module(body=[n for n in node.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))], type_ignores=[]))
    return out


def entry_points(root: Path, door_module: str, names, door_file: str | None = None) -> dict:
    """``{(module, function): sorted door functions named}`` over every module of the service but the door itself."""
    door_file = door_file or door_module.replace(".", "/") + ".py"
    found = {}
    for rel, path in modules(root):
        if rel == door_file:
            continue
        for fn, used in references(path.read_text("utf-8"), door_module, set(names)).items():
            found[(rel, fn)] = sorted(used)
    return found


def direct_sql_writers(source: str, pattern) -> set:
    """The door module's top-level functions whose own text holds a write of the area's tables (``sql_scan`` pattern)."""
    import sql_scan

    return {name for name, fn in _top_functions(ast.parse(source)) if sql_scan.writes(ast.get_source_segment(source, fn), pattern)}


def check_coverage(found: dict, recorded: dict, not_recorded: dict, fixture_names) -> None:
    """Every ``(module, function, door function)`` the code holds is either RECORDED (named scenarios, each in the fixture) or
    NOT RECORDED for a stated reason; nothing is listed that the code no longer holds."""
    pairs = {(module, fn, door) for (module, fn), doors in found.items() for door in doors}
    listed = set(recorded) | set(not_recorded)
    assert pairs == listed, {"in the code, not listed": sorted(pairs - listed), "listed, not in the code": sorted(listed - pairs)}
    assert not set(recorded) & set(not_recorded)
    for key, names in recorded.items():
        assert names and all(name in fixture_names for name in names), key
    for key, why in not_recorded.items():
        assert isinstance(why, str) and len(why) > 20, key
