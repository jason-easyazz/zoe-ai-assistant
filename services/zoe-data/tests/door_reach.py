"""Which entry points a run REACHES its door through: the door functions called, each with the function that called it.

``reached(run, root, door_file)`` runs ``run()`` (one scenario) under a profiler and returns every ``(module, function, door
function)`` where a function of ``door_file`` was entered from a function of another module of the service. The caller is named
as ``tests/door_callers.py`` names it (the outermost top-level function or ``Class.method`` holding the call), so a coverage
table keyed the door_callers way can be checked against what each named scenario actually ran, not only against its name.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _first(node) -> int:
    """A decorated function's code starts at its first decorator."""
    return min([node.lineno] + [d.lineno for d in node.decorator_list])


def _outer(path: Path) -> list:
    """``[(first line, last line, qualname)]`` of a module's top-level functions and methods."""
    tree = ast.parse(path.read_text("utf-8"))
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((_first(node), node.end_lineno, node.name))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((_first(sub), sub.end_lineno, f"{node.name}.{sub.name}"))
    return out


def reached(run, root: Path, door_file: str) -> set:
    root = Path(root).resolve()
    door = (root / door_file).resolve()
    door_names = {first: name for first, _end, name in _outer(door)}
    spans: dict = {}
    hits: set = set()

    def where(filename: str, line: int):
        path = Path(filename)
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            return None
        if rel.startswith("tests/"):
            return None
        if rel not in spans:
            spans[rel] = _outer(path)
        for first, last, name in spans[rel]:
            if first <= line <= last:
                return rel, name
        return rel, "<module>"

    def profile(frame, event, _arg):
        if event != "call" or frame.f_code.co_filename != str(door):
            return
        name = door_names.get(frame.f_code.co_firstlineno)
        caller = frame.f_back
        if name is None or caller is None or caller.f_code.co_filename == str(door):
            return
        found = where(caller.f_code.co_filename, caller.f_code.co_firstlineno)
        if found:
            hits.add((*found, name))

    old = sys.getprofile()
    sys.setprofile(profile)
    try:
        run()
    finally:
        sys.setprofile(old)
    return hits
