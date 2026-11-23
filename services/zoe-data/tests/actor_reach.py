"""Which ENTRY POINTS of the service can reach a sender, read from the code: the call graph by import resolution, walked back
from the senders to every route, websocket handler and spawned task it reaches. So "every authenticated entry point notes who
is acting" is checked against the code itself, not against a list of places someone remembered.

``Code(root)`` reads every module of the service once (``door_callers.modules``: tests, scripts and alembic are not the service).

* ``edges``: ``{(module, function): {(module, function) it names}}``. A function NAMES another when its body (nested functions
  included) refers to it by a name Python would resolve to it: a function of the same module (``fn``); one imported from another
  module (``from m import fn [as x]``, then ``fn`` / ``x``); a module's attribute (``import m [as x]`` anywhere in the module, or
  ``from pkg import m``, then ``m.fn`` / ``x.fn``); a method of the same class as ``self.method``. Called or handed on uncalled,
  either counts. A method is ``Class.method``.
* ``reaching(targets)``: every function from which one of ``targets`` can be reached (the targets included).
* ``routes()``: ``{(module, function): [resolved dependencies]}`` for every function decorated as an HTTP route or a websocket
  handler (``@<x>.get / post / put / patch / delete / api_route / websocket(...)``), with the functions its ``Depends(...)`` name
  (in the decorator's ``dependencies=[...]`` or a parameter default) where they resolve.
* ``spawned()``: ``{(module, function): {spawners}}`` for every function run as its own task or job: the first argument of
  ``asyncio.create_task`` / ``ensure_future`` / ``run_coroutine_threadsafe``, ``BackgroundTasks.add_task``, a scheduler's
  ``add_job`` (called or not).
* ``calls(key, names)``: does the function's own body (nested functions included) call something named one of ``names``.

It over-approximates where it cannot be sure (a same-named function of an imported module counts whatever object it is called
on), and it cannot see dispatch by a string looked up at run time (a tool registry): a path that only exists that way is not an
edge. Each caller states what it relies on.
"""
from __future__ import annotations

import ast
from pathlib import Path

import door_callers

ROUTE_ATTRS = {"get", "post", "put", "patch", "delete", "api_route", "websocket", "api_websocket_route"}
SPAWN_ATTRS = {"create_task", "ensure_future", "run_coroutine_threadsafe", "add_task", "add_job"}


class _Module:
    def __init__(self, rel: str, tree: ast.Module) -> None:
        self.rel = rel
        self.functions = dict(door_callers._top_functions(tree))
        self.mod_alias: dict = {}            # local name -> dotted module
        self.name_alias: dict = {}           # local name -> (dotted module, name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.mod_alias[a.asname] = a.name
                    else:
                        self.mod_alias[a.name] = a.name
                        self.mod_alias.setdefault(a.name.split(".")[0], a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                for a in node.names:
                    self.name_alias[a.asname or a.name] = (node.module, a.name)


class Code:
    def __init__(self, root: Path) -> None:
        self.mods: dict = {}
        for rel, path in door_callers.modules(root):
            try:
                self.mods[rel[:-3].replace("/", ".")] = _Module(rel, ast.parse(path.read_text("utf-8")))
            except SyntaxError:
                continue
        self.edges = {(m.rel, name): self._named(m, name, fn) for m in self.mods.values() for name, fn in m.functions.items()}

    def fn(self, key):
        return self.mods[key[0][:-3].replace("/", ".")].functions[key[1]]

    def resolve(self, m: _Module, expr) -> tuple | None:
        """The function an expression (``fn``, ``x.fn``, ``pkg.mod.fn``) names, as ``(module, function)``, or None."""
        if isinstance(expr, ast.Name):
            if expr.id in m.functions:
                return m.rel, expr.id
            if expr.id in m.name_alias:
                mod, name = m.name_alias[expr.id]
                target = self.mods.get(mod)
                if target is not None and name in target.functions:
                    return target.rel, name
            return None
        if isinstance(expr, ast.Attribute):
            base, mod = expr.value, None
            if isinstance(base, ast.Name):
                mod = m.mod_alias.get(base.id)
                if mod is None and base.id in m.name_alias:                 # ``from routers import music`` -> routers.music
                    pkg, name = m.name_alias[base.id]
                    mod = f"{pkg}.{name}"
            elif isinstance(base, ast.Attribute):
                mod = ast.unparse(base)
            target = self.mods.get(mod) if mod else None
            if target is not None and expr.attr in target.functions:
                return target.rel, expr.attr
        return None

    def _named(self, m: _Module, fname: str, fn) -> set:
        cls = fname.split(".")[0] if "." in fname else None
        named = set()
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self" and cls:
                if f"{cls}.{sub.attr}" in m.functions:
                    named.add((m.rel, f"{cls}.{sub.attr}"))
            elif isinstance(sub, (ast.Name, ast.Attribute)) and not (isinstance(sub, ast.Name) and not isinstance(sub.ctx, ast.Load)):
                key = self.resolve(m, sub)
                if key is not None and key != (m.rel, fname):
                    named.add(key)
        return named

    def reaching(self, targets) -> set:
        back: dict = {}
        for src, dsts in self.edges.items():
            for d in dsts:
                back.setdefault(d, set()).add(src)
        seen, todo = set(targets), list(targets)
        while todo:
            for src in back.get(todo.pop(), ()):
                if src not in seen:
                    seen.add(src)
                    todo.append(src)
        return seen

    def _module(self, key) -> _Module:
        return self.mods[key[0][:-3].replace("/", ".")]

    def routes(self) -> dict:
        found = {}
        for m in self.mods.values():
            for fname, fn in m.functions.items():
                decos = [d for d in fn.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                         and d.func.attr in ROUTE_ATTRS]
                if not decos:
                    continue
                deps = set()
                for node in (*decos, fn.args):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call) and getattr(sub.func, "id", getattr(sub.func, "attr", "")) == "Depends" and sub.args:
                            key = self.resolve(m, sub.args[0])
                            if key is not None:
                                deps.add(key)
                found[(m.rel, fname)] = sorted(deps)
        return found

    def dependencies(self, key) -> set:
        """A dependency's own ``Depends(...)`` parameters, any depth (FastAPI runs them all before the route)."""
        out, todo = set(), [key]
        while todo:
            k = todo.pop()
            m, fn = self._module(k), self.fn(k)
            for sub in ast.walk(fn.args):
                if isinstance(sub, ast.Call) and getattr(sub.func, "id", getattr(sub.func, "attr", "")) == "Depends" and sub.args:
                    dep = self.resolve(m, sub.args[0])
                    if dep is not None and dep not in out:
                        out.add(dep)
                        todo.append(dep)
        return out

    def spawned(self) -> dict:
        found: dict = {}
        for m in self.mods.values():
            for fname, fn in m.functions.items():
                for sub in ast.walk(fn):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in SPAWN_ATTRS and sub.args:
                        arg = sub.args[0]
                        key = self.resolve(m, arg.func if isinstance(arg, ast.Call) else arg)
                        if key is not None:
                            found.setdefault(key, set()).add((m.rel, fname))
        return found

    def calls(self, key, names) -> bool:
        return any(isinstance(sub, ast.Call) and (getattr(sub.func, "attr", None) in names or getattr(sub.func, "id", None) in names)
                   for sub in ast.walk(self.fn(key)))
