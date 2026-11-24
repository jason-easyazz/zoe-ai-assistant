"""Only ``memory_service.py`` writes the memory rows (the palace collection), bar FOUR named bypass writes.

The rows are a collection, not SQL, so the scan is over calls, over the WHOLE service at once (``scan``): a write call
(``upsert``, ``update``, ``delete``, ``add``, ``modify``, also ``getattr(col, "delete")``; FETCHED is enough: ``erase = col.delete``) on anything that came from the
collection (``leased_drawers(...)``, ``._collection()``, ``get_drawers_collection(...)``, ``get_collection`` /
``get_or_create_collection`` / ``create_collection``, or any function that RETURNS one; through any chain of assignments, an
attribute it was stored in, a closure, a module-level or ``global`` handle, a parameter such a value is passed to in ANY module,
or directly on the call). No file is exempt by name; the four allowed writes are named by module and function WITH A COUNT, so a
fifth fails. Module- and class-level code is scanned too (as ``<module>``).

The four (all AUTOMATIC, all on ``main``): ``memory_digest.py`` writes rows back from its night passes in three write calls
(``_upsert_rows``: one; ``_update_existing``: an update, or an upsert for a store with no update), and ``zoe_agent.py`` has a
one-off migration (``migrate_mempalace_legacy_records``). They are faults on main to route through ``MemoryService`` later.

Functions are keyed by QUALIFIED name (enclosing classes and functions, dotted), so a later function of the same bare name
cannot replace an earlier one and hide its write.

Negative control runs: a ``col.update`` planted in ``memory_digest._live_ids`` fails
``test_only_the_memory_service_writes_the_collection`` (and a write planted in a module that has none fails it too); round 3,
each fails it too: a class in ``memory_digest`` holding ``self.col = svc._collection()`` and calling ``self.col.delete``; a
``memory_digest`` function handing ``svc._collection()`` to ``night_mind._finish`` (whose ``res.update`` becomes a write). And a
``svc.erase_rows`` handed to ``asyncio.to_thread`` in ``memory_digest`` fails the table test (a reference is a site).
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
SVC = Path(__file__).resolve().parents[1]
WRITES = {"upsert", "update", "delete", "add", "modify"}
COLLECTION_PARAMS = {"col", "coll", "collection", "drawers"}
SOURCES = {"leased_drawers", "_collection", "_audit_collection", "get_drawers_collection", "get_collection", "get_or_create_collection",
           "create_collection"}
#: the bypass writes on main: (module, function) -> how many write calls
BYPASS = {("memory_digest.py", "_upsert_rows"): 1, ("memory_digest.py", "_update_existing"): 2,
          ("zoe_agent.py", "migrate_mempalace_legacy_records"): 1}
MODULE = "<module>"                                          #: the unit for code at module and class level


def _qualified(tree):
    """``(qualified name, node)`` for every function AND class: its enclosing classes and functions joined by dots."""
    def walk(node, path):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{path}.{child.name}" if path else child.name
                yield qual, child
                yield from walk(child, qual)
            else:
                yield from walk(child, path)
    yield from walk(tree, "")


def _functions(tree):
    """``(qualified name, node)`` for every function, so a method ``A.save`` and a method ``B.save`` (or a nested
    ``outer.save``) are two entries, never one replacing the other."""
    return ((q, n) for q, n in _qualified(tree) if not isinstance(n, ast.ClassDef))


def _own_nodes(node):
    """Every node of a unit that runs IN it: a nested function's body is its own unit (the module's units are everything
    outside a function, class bodies included)."""
    todo = list(ast.iter_child_nodes(node))
    while todo:
        n = todo.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            todo.extend(ast.iter_child_nodes(n))


def _params(fn) -> list:
    ps = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    return ps[1:] if ps[:1] in (["self"], ["cls"]) else ps


def scan(modules: dict) -> Counter:
    """``{(module, qualified function or "<module>"): write calls on the collection}`` over a whole set of modules at once.

    A value is the collection when it is: a call of a SOURCE (or of any function, in any module, that RETURNS the collection);
    a name bound to it in the unit or an enclosing one (assignment, tuple unpacking, walrus, ``with ... as``, ``for``, a
    ``global``); a parameter a caller ANYWHERE in the set passes it to (callees found by bare name, so a helper in another
    module is followed through its import, a method on an object by its name anywhere); a parameter called the collection; an ATTRIBUTE some unit stored it in (``self.col = ...``, keyed by
    attribute name over the whole set); either branch of ``a if c else b`` / ``a or b``. A write is a WRITES method FETCHED off it
    (called there, stored, or handed on as a callable: the fetch is the site) or ``getattr(<it>, name)`` with a WRITES name or
    any name that is not a constant; the one ``getattr`` that is not a site is the argument of ``callable(...)`` (a probe that
    yields a bool, the method never leaves it)."""
    units, parent, globals_of = {}, {}, {}
    local, top, methods = {}, {}, {}        # (module, bare) -> units; (module, bare) -> top-level units; bare -> methods
    imported, modnames = {}, {}             # (module, local name) -> (module, bare); (module, local name) -> module
    for rel, src in modules.items():
        tree = ast.parse(src)
        units[(rel, MODULE)] = tree
        classes = {q for q, n in _qualified(tree) if isinstance(n, ast.ClassDef)}
        for qual, fn in _functions(tree):
            units[(rel, qual)] = fn
            up = qual.rsplit(".", 1)[0] if "." in qual else ""
            parent[(rel, qual)] = (rel, up) if (rel, up) in units and up else (rel, MODULE)
            local.setdefault((rel, fn.name), []).append((rel, qual))
            if "." not in qual:
                top.setdefault((rel, fn.name), []).append((rel, qual))
            if up in classes:
                methods.setdefault(fn.name, []).append((rel, qual))
        for node in ast.walk(tree):                         # imports anywhere in the module (lazy imports too)
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for al in node.names:
                    imported[(rel, al.asname or al.name)] = (node.module.replace(".", "/") + ".py", al.name)
                    modnames[(rel, al.asname or al.name)] = f"{node.module}.{al.name}".replace(".", "/") + ".py"
            elif isinstance(node, ast.Import):
                for al in node.names:
                    modnames[(rel, al.asname or al.name)] = al.name.replace(".", "/") + ".py"

    def callees(rel, func) -> list:
        """The functions a call may reach: same-module functions of the name; an imported function (``from m import f``,
        ``import m`` then ``m.f``); for a method call on an object, every method of that name in the set."""
        if isinstance(func, ast.Name):
            found = list(local.get((rel, func.id), ()))
            if (rel, func.id) in imported:
                found += top.get(imported[(rel, func.id)], [])
            return found
        if isinstance(func, ast.Attribute):
            found = list(local.get((rel, func.attr), ()))
            if isinstance(func.value, ast.Name) and (rel, func.value.id) in modnames:
                return found + top.get((modnames[(rel, func.value.id)], func.attr), [])
            return found + methods.get(func.attr, [])
        return []
    kinds = (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.withitem, ast.For, ast.AsyncFor, ast.Return, ast.Call, ast.Attribute,
             ast.Global)
    own = {u: [n for n in _own_nodes(node) if isinstance(n, kinds)] for u, node in units.items()}
    for u, nodes in own.items():
        globals_of[u] = {name for n in nodes if isinstance(n, ast.Global) for name in n.names}
    holds = {u: ({a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs if a.arg in COLLECTION_PARAMS}
                 if u[1] != MODULE else set()) for u, node in units.items()}
    attrs: set = set()
    sources = set(SOURCES)

    def names_of(u):
        seen = set(holds[u])
        while u[1] != MODULE:
            u = parent[u]
            seen |= holds[u]
        return seen

    def is_col(node, names) -> bool:
        while isinstance(node, (ast.Await, ast.NamedExpr)):
            node = node.value
        if isinstance(node, ast.Name):
            return node.id in names
        if isinstance(node, ast.Attribute):
            return node.attr in attrs
        if isinstance(node, ast.Call):
            fn = node.func
            return (fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")) in sources
        if isinstance(node, ast.IfExp):
            return is_col(node.body, names) or is_col(node.orelse, names)
        if isinstance(node, ast.BoolOp):
            return any(is_col(v, names) for v in node.values)
        return False

    def bind(u, target, value, names) -> bool:
        """Bind ``target`` (a name, attribute or tuple of them) when ``value`` is the collection; True when anything new."""
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts):
                return any([bind(u, t, v, names) for t, v in zip(target.elts, value.elts)])
            return False
        if value is None or not is_col(value, names):
            return False
        if isinstance(target, ast.Starred):
            target = target.value
        if isinstance(target, ast.Name):
            home = (u[0], MODULE) if target.id in globals_of[u] else u
            if target.id not in holds[home]:
                holds[home].add(target.id)
                return True
        elif isinstance(target, ast.Attribute) and target.attr not in attrs:
            attrs.add(target.attr)
            return True
        return False

    changed = True
    while changed:
        changed = False
        for u, nodes in own.items():
            names = names_of(u)
            for node in nodes:
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                    for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                        changed |= bind(u, t, node.value, names)
                elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                    changed |= bind(u, node.optional_vars, node.context_expr, names)
                elif isinstance(node, (ast.For, ast.AsyncFor)):
                    items = node.iter.elts if isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)) else [node.iter]
                    for item in items:
                        changed |= bind(u, node.target, item, names)
                elif isinstance(node, ast.Return) and u[1] != MODULE and node.value is not None and is_col(node.value, names):
                    bare = units[u].name
                    if bare not in sources:
                        sources.add(bare)
                        changed = True
                elif isinstance(node, ast.Call):
                    for callee in callees(u[0], node.func):
                        params = _params(units[callee])
                        passed = [(params[i], a) for i, a in enumerate(node.args) if i < len(params)]
                        passed += [(k.arg, k.value) for k in node.keywords if k.arg]
                        for param, value in passed:
                            if param not in holds[callee] and is_col(value, names):
                                holds[callee].add(param)
                                changed = True
                if changed:
                    names = names_of(u)
    probes = {id(n.args[0]) for nodes in own.values() for n in nodes                # ``callable(getattr(col, "update", None))``
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "callable" and len(n.args) == 1}
    found: Counter = Counter()
    for u, nodes in own.items():
        names = names_of(u)
        for node in nodes:
            # the write method named on a collection: called here, or FETCHED (``erase = col.delete``, ``self.erase = col.delete``,
            # ``run(col.update, ...)``): the fetch is the write site, wherever the bound method is called later
            if isinstance(node, ast.Attribute) and node.attr in WRITES and is_col(node.value, names):
                found[u] += 1
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr" and len(node.args) >= 2 \
                    and is_col(node.args[0], names) and id(node) not in probes \
                    and (not isinstance(node.args[1], ast.Constant) or node.args[1].value in WRITES):
                found[u] += 1       # ``getattr(col, "delete")`` called at once or later, or ``getattr(col, name)``: any name may write
    return found


def collection_writes(source: str) -> Counter:
    """``{qualified function name (or "<module>"): write calls on the collection}`` for one module's source."""
    return Counter({qual: n for (_rel, qual), n in scan({"module.py": source}).items()})


def _modules():
    for path in sorted(SVC.rglob("*.py")):
        rel = path.relative_to(SVC).as_posix()
        if rel.startswith(("tests/", "scripts/")) or "/tests/" in rel or ".venv" in rel or "node_modules" in rel:
            continue
        yield rel, path


def test_only_the_memory_service_writes_the_collection():
    found = {key: n for key, n in scan({rel: path.read_text("utf-8") for rel, path in _modules()}).items()
             if key[0] != "memory_service.py"}
    assert found == BYPASS
    assert sum(BYPASS.values()) == 4


def test_the_scan_sees_each_way_a_write_can_be_spelled():
    """The reader finds a write through a lease, an alias, a helper's parameter and a direct chain, and ignores reads and
    dict updates. Control run: with ``is_col`` answering False for names, the alias and parameter forms are missed."""
    src = '''
def lease(svc):
    col = leased_drawers(svc)
    col.update(ids=[1], metadatas=[{}])
def alias(svc):
    a = svc._collection()
    b = a
    b.delete(ids=[1])
def helper(col, rows):
    col.upsert(ids=rows)
def caller(svc):
    helper(leased_drawers(svc), [])
def chain(svc):
    get_drawers_collection("x").add(ids=[1])
def handed_on(svc):
    col = leased_drawers(svc)
    run(col.update, ids=[1])
def by_name(col, ids):
    col.upsert(ids=ids)
def reads(svc):
    col = leased_drawers(svc)
    col.get(ids=[1])
    meta = {}
    meta.update(a=1)
'''
    assert collection_writes(src) == Counter({"lease": 1, "alias": 1, "helper": 1, "chain": 1, "handed_on": 1, "by_name": 1})


def test_the_scan_follows_attributes_helpers_elsewhere_wrappers_closures_and_module_level():
    """Round 3: the forms the call-and-name scan missed, each its own planted write. An attribute handle
    (``self.col = svc._collection()`` then ``self.col.delete``, also used from another method and another module); a helper
    in ANOTHER module handed the collection (``import b`` then ``b.erase(col)``; ``from b import wipe_out as wo``; a method
    ``obj.run(col)`` of any class; a same-named helper nobody hands the collection, ``unrelated``, stays clean); a wrapper that RETURNS
    it; a closure over it; a module-level handle and a module-level write; a ``global``; ``with ... as``; tuple unpacking;
    ``a or b``; ``getattr(col, "delete")``; ``create_collection``. Controls run (each fails this test): ``bind`` ignoring
    attribute targets (the attribute forms are missed); callees looked up in the CALLER's module only (the old per-module
    scan: ``b.erase`` is missed); ``Return`` not making a source (``wrapped`` is missed)."""
    a = '''
import b
from b import wipe_out as wo
class Keeper:
    def __init__(self, svc):
        self.col = svc._collection()
    def wipe(self):
        self.col.delete(ids=[1])
def hand_off(svc):
    b.erase(svc._collection())
    wo(leased_drawers(svc), why="x")
    svc.eraser.run(svc._collection())
def _drawers(svc):
    return leased_drawers(svc)
def wrapped(svc):
    _drawers(svc).upsert(ids=[1])
def closure(svc):
    col = leased_drawers(svc)
    def inner():
        col.update(ids=[1])
    inner()
_COL = get_collection("x")
def uses_module_handle():
    _COL.add(ids=[1])
get_collection("y").delete(ids=[1])
def sets_global(svc):
    global _LATER
    _LATER = svc._collection()
def uses_global():
    _LATER.upsert(ids=[1])
def with_as(svc):
    with leased_drawers(svc) as handle:
        handle.delete(ids=[1])
def unpacked(svc):
    one, two = leased_drawers(svc), 3
    one.update(ids=[1])
def either(svc, maybe):
    h = maybe or svc._collection()
    h.delete(ids=[1])
def by_getattr(svc):
    getattr(svc._collection(), "delete")(ids=[1])
def created(client):
    client.create_collection("z").add(ids=[1])
def reads(svc):
    col = leased_drawers(svc)
    col.get(ids=[1])
    getattr(col, "get")(ids=[1])
'''
    b = '''
def erase(handle):
    handle.delete(ids=[1])
def wipe_out(handle, why=None):
    handle.delete(ids=[1])
class Eraser:
    def run(self, h):
        h.update(ids=[1])
def unrelated(handle):
    handle.delete(ids=[1])
def elsewhere(keeper):
    keeper.col.modify(name="w")
'''
    assert scan({"a.py": a, "b.py": b}) == Counter({
        ("a.py", "Keeper.wipe"): 1, ("b.py", "erase"): 1, ("b.py", "wipe_out"): 1, ("b.py", "Eraser.run"): 1,
        ("b.py", "elsewhere"): 1, ("a.py", "wrapped"): 1,
        ("a.py", "closure.inner"): 1, ("a.py", "uses_module_handle"): 1, ("a.py", "<module>"): 1, ("a.py", "uses_global"): 1,
        ("a.py", "with_as"): 1, ("a.py", "unpacked"): 1, ("a.py", "either"): 1, ("a.py", "by_getattr"): 1,
        ("a.py", "created"): 1})


def test_a_write_method_fetched_first_and_called_later_is_a_write():
    """Round 4: the bound write method fetched off the collection and called later, in every form: by attribute into a name,
    into an attribute, handed to a helper; by ``getattr`` with a constant name into a name, with a NON-constant name (kept, and
    called at once), and the old immediate ``getattr(col, "delete")(...)``. Each is one write, at the FETCH. A
    ``callable(getattr(col, "update", None))`` probe and ``getattr(col, "get")`` are reads. Controls run (each fails this
    test): the ``getattr`` branch put back to the old rule (only a ``getattr`` whose result is called at once: ``by_getattr_later``
    and ``by_name_kept`` missed); the non-constant case dropped (``by_name_kept`` and ``by_name_called`` missed); the probe
    exemption dropped (``probe`` counted)."""
    src = '''
def by_attr_later(svc, ids):
    erase = svc._collection().delete
    erase(ids=ids)
class Holder:
    def __init__(self, svc):
        self.erase = svc._collection().delete
    def go(self, ids):
        self.erase(ids=ids)
def handed(svc, ids):
    run_later(leased_drawers(svc).update, ids)
def by_getattr_later(svc, ids):
    erase = getattr(svc._collection(), "delete")
    erase(ids=ids)
def by_name_kept(svc, ids, verb):
    fn = getattr(leased_drawers(svc), verb)
    fn(ids=ids)
def by_name_called(svc, ids, verb):
    getattr(svc._collection(), verb)(ids=ids)
def by_getattr_now(svc, ids):
    getattr(svc._collection(), "delete")(ids=ids)
def probe(svc):
    col = svc._collection()
    return callable(getattr(col, "update", None))
def reads(svc):
    return getattr(svc._collection(), "get")(ids=[1])
'''
    assert collection_writes(src) == Counter({"by_attr_later": 1, "Holder.__init__": 1, "handed": 1, "by_getattr_later": 1,
                                              "by_name_kept": 1, "by_name_called": 1, "by_getattr_now": 1})


def test_a_later_function_of_the_same_name_does_not_hide_an_earlier_write():
    """Two methods called ``save`` in two classes, and a nested ``save``: each is its own entry, so the earlier one's write
    is still counted. Control run: with ``collection_writes`` keying functions by bare name (the old ``{f.name: f}``), the
    first ``A.save`` write vanishes (the later write-free ``B.save`` replaces it) and this test fails."""
    src = '''
class A:
    def save(self):
        get_drawers_collection("x").delete(ids=[1])
class B:
    def save(self):
        return None
def outer():
    def save():
        get_drawers_collection("x").upsert(ids=[1])
    save()
def save():
    return None
'''
    assert collection_writes(src) == Counter({"A.save": 1, "outer.save": 1})


def test_the_memory_service_itself_is_seen_writing():
    """The scan is not blind to the one writer: it finds the service's own row writes."""
    found = collection_writes((SVC / "memory_service.py").read_text("utf-8"))
    assert found["MemoryService._write_row"] == 1 and found["MemoryService._update_row"] >= 1 and sum(found.values()) >= 15


# ---- which service methods change the rows: DERIVED from the service's code -------------------------------------------------------
SERVICE_CLASS = "MemoryService"
#: the access tick a read schedules for the rows it returned (``access_count``, ``last_accessed``): see ``changing_methods``
READ_TICK = "_schedule_tick"


def changing_methods(source: str, *, through_tick: bool = True) -> set:
    """The names of the ``MemoryService`` methods that CHANGE the memory rows, read from the service module's source.

    The rule: a method changes the rows when its own body (nested functions included), or any function of the module it NAMES,
    transitively, makes a write the collection scan (``scan``) finds: a write method on the rows collection or on the audit
    collection. A method is named as ``self.X`` / ``cls.X`` / ``MemoryService.X`` and a module-level function by its bare name;
    named means called OR handed on (``self._run_sync(self._tick_access_sync, ...)``,
    ``self._track_background_task(self._tick_access(...))``), so a write run through an executor or a background task counts.

    ``through_tick=False`` cuts the one edge into ``READ_TICK``: the methods that then stop changing anything are the reads whose
    ONLY write is the access tick of the rows they returned (``test_the_reads_that_tick_change_nothing_else`` names them)."""
    tree = ast.parse(source)
    module_fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == SERVICE_CLASS)
    methods = {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    units = {("m", k): v for k, v in methods.items()} | {("f", k): v for k, v in module_fns.items()}

    def owner(qual: str):
        parts = qual.split(".")
        if parts[0] == SERVICE_CLASS:
            return ("m", parts[1]) if len(parts) > 1 else None
        return ("f", parts[0]) if parts[0] in module_fns else None

    writes = {owner(q) for (_r, q), n in scan({"memory_service.py": source}).items() if n} - {None}
    edges = {}
    for key, fn in units.items():
        out = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id in ("self", "cls", SERVICE_CLASS) and node.attr in methods:
                out.add(("m", node.attr))
            elif isinstance(node, ast.Name) and node.id in module_fns:
                out.add(("f", node.id))
        if not through_tick:
            out.discard(("m", READ_TICK))
        edges[key] = out - {key}
    changing = set(writes)
    grew = True
    while grew:
        grew = False
        for key, out in edges.items():
            if key not in changing and out & changing:
                changing.add(key)
                grew = True
    return {name for kind, name in changing if kind == "m"}


def test_the_mutating_methods_are_derived_from_the_service():
    """``memory_writers`` lists every method the rule (``changing_methods``) finds, no more: the 13 that take who is acting
    (``MUTATING``) and the rest (``WITHOUT_ACTING``: counters on rows, a superseded row put back, compaction, the residue scrub); every
    private method that changes the rows is named by nobody outside the service (else it is a way round the table); the reads
    whose only write is their access tick are exactly ``READS_THAT_TICK``. Controls run (each fails this): ``tick_access``
    removed from ``WITHOUT_ACTING`` (the derivation still finds it); a new public method planted in the real service,
    ``womble_fix``, writing only through ``self._run_sync(self._tick_access_sync, ...)``."""
    import memory_writers

    found = changing_methods((SVC / "memory_service.py").read_text("utf-8"))
    no_tick = changing_methods((SVC / "memory_service.py").read_text("utf-8"), through_tick=False)
    public = {name for name in found if not name.startswith("_")}
    assert set(memory_writers.MUTATING).isdisjoint(memory_writers.WITHOUT_ACTING)
    assert public - set(memory_writers.READS_THAT_TICK) == set(memory_writers.MUTATING) | set(memory_writers.WITHOUT_ACTING)
    assert set(memory_writers.READS_THAT_TICK) == public - no_tick
    private = {name for name in found if name.startswith("_")}
    outside = Counter()
    for rel, path in _modules():
        if rel != "memory_service.py":
            outside.update((rel, n.attr) for n in ast.walk(ast.parse(path.read_text("utf-8")))
                           if isinstance(n, ast.Attribute) and n.attr in private)
    assert dict(outside) == {}


def test_the_derivation_finds_a_new_mutating_method():
    """A method planted in the service that writes through a private helper run in an executor (``self._run_sync(self._x)``),
    one that writes through a module function, one that writes the audit collection, and a read that only ticks: the first
    three are found, the read only with the tick edge, the plain read never. Controls run (each fails this): the edge to a
    module-level function dropped (``planted_module`` missed); ``through_tick`` ignored (``a_read`` still found without the
    tick)."""
    src = '''
def _module_writer(col):
    col.delete(ids=[1])
class MemoryService:
    def _collection(self):
        return get_drawers_collection("x")
    def _audit_collection(self):
        return None
    def _x_sync(self, ids):
        self._collection().update(ids=ids, metadatas=[{}])
    async def planted_executor(self, ids):
        await self._run_sync(self._x_sync, ids)
    def planted_module(self):
        _module_writer(self._collection())
    def planted_audit(self):
        self._audit_collection().add(ids=[1])
    def _schedule_tick(self, ids):
        self._track_background_task(self.planted_executor(ids))
    async def a_read(self, ids):
        rows = self._collection().get(ids=ids)
        self._schedule_tick(ids)
        return rows
    async def plain_read(self, ids):
        return self._collection().get(ids=ids)
'''
    assert changing_methods(src) == {"_x_sync", "planted_executor", "planted_module", "planted_audit", "_schedule_tick", "a_read"}
    assert changing_methods(src, through_tick=False) == {"_x_sync", "planted_executor", "planted_module", "planted_audit",
                                                         "_schedule_tick"}


# ---- every call of a mutating method, and who is acting there -------------------------------------------------------------------
def mutating_calls(source: str, rel: str) -> Counter:
    """``{(module, function, method): calls}`` of the mutating method names in one module (nested functions by dotted name).

    Every REFERENCE counts, not only a call: ``svc.ingest(...)``, but also the bound method handed on
    (``asyncio.to_thread(svc.erase_rows, ...)``, ``partial(svc.review, ...)``, ``f = svc.ingest``) and
    ``getattr(svc, "review")``, so a write run through an executor or an alias is still a site in the table."""
    import memory_writers

    found: Counter = Counter()
    changing = set(memory_writers.MUTATING) | set(memory_writers.WITHOUT_ACTING)

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            inner = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = child.name if not fn else f"{fn}.{child.name}"
            if isinstance(child, ast.Attribute) and child.attr in changing:
                found[(rel, fn, child.attr)] += 1
            elif isinstance(child, ast.Call) and getattr(child.func, "id", "") == "getattr" and len(child.args) >= 2 \
                    and isinstance(child.args[1], ast.Constant) and child.args[1].value in changing:
                found[(rel, fn, child.args[1].value)] += 1
            walk(child, inner)

    walk(ast.parse(source), "")
    return found


def service_calls(found: Counter, not_the_service: dict) -> dict:
    """The calls left once each exempt site's EXACT count of other-store calls is taken off (never the whole site). An exempt
    site with fewer calls than its count fails: the table must say what is there."""
    for key, n in not_the_service.items():
        assert found.get(key, 0) >= n, f"{key}: {found.get(key, 0)} calls, the exemption names {n}"
    left = {k: n - not_the_service.get(k, 0) for k, n in found.items()}
    return {k: n for k, n in left.items() if n}


def test_an_exempt_site_does_not_hide_one_more_call():
    """A ``MemoryService.delete_user()`` planted inside ``memory_service.delete_user`` (which also calls the struggle
    detector's own ``delete_user``) is left over and must be in the table. Control run: with ``service_calls`` dropping the
    whole exempt key (the old ``k not in NOT_THE_SERVICE``), the planted call vanishes and this test fails."""
    src = '''
class MemoryService:
    async def delete_user(self, user_id, *, actor):
        await struggle_detector.delete_user(user_id)
        await get_memory_service().delete_user(user_id, actor=actor)
'''
    found = mutating_calls(src, "memory_service.py")
    assert service_calls(found, {("memory_service.py", "delete_user", "delete_user"): 1}) == {
        ("memory_service.py", "delete_user", "delete_user"): 1}
    assert service_calls(found, {("memory_service.py", "delete_user", "delete_user"): 2}) == {}
    with pytest.raises(AssertionError):                     # an exemption naming more calls than are there fails too
        service_calls(found, {("memory_service.py", "delete_user", "delete_user"): 3})


def test_a_handed_on_or_aliased_mutating_method_is_a_site():
    """Round 3: a bound method handed to an executor, aliased, or fetched by ``getattr`` is a site like a call. Control run:
    with ``mutating_calls`` counting only ``Call`` nodes (the old rule) this test fails (all three are missed)."""
    src = '''
async def by_thread(svc, ids):
    await asyncio.to_thread(svc.erase_rows, ids)
def aliased(svc, row):
    keep = svc.ingest
    keep(row)
def fetched(svc):
    getattr(svc, "review")(1)
def a_read(svc):
    return svc.recall(1)
'''
    assert mutating_calls(src, "m.py") == Counter({("m.py", "by_thread", "erase_rows"): 1, ("m.py", "aliased", "ingest"): 1,
                                                   ("m.py", "fetched", "review"): 1})


def test_every_call_of_a_mutating_method_is_in_the_table_with_who_is_acting():
    """``tests/memory_writers.py`` is the whole list: a new call site, or one more call in a listed function, fails until it is
    added WITH who is acting there. The methods are the DERIVED set (``test_the_mutating_methods_are_derived_from_the_service``):
    the calls of those that take ``acting`` are ``SITES``, of the rest ``WITHOUT_ACTING_SITES``. Control runs: a second
    ``svc.ingest`` planted in ``routers/notes._store_note_memory`` fails; round 4, a second ``svc.tick_access`` planted in
    ``memory_digest._rem_reinforce_pass._write`` fails (``tick_access`` was in no list before)."""
    import memory_writers

    found: Counter = Counter()
    for rel, path in _modules():
        found.update(mutating_calls(path.read_text("utf-8"), rel))
    calls = service_calls(found, memory_writers.NOT_THE_SERVICE)
    assert {k: n for k, n in calls.items() if k[2] in memory_writers.MUTATING} == {
        k: n for k, (_who, n) in memory_writers.SITES.items()}
    assert {k: n for k, n in calls.items() if k[2] in memory_writers.WITHOUT_ACTING} == {
        k: n for k, (_who, n) in memory_writers.WITHOUT_ACTING_SITES.items()}
    assert {who for who, _n in memory_writers.WITHOUT_ACTING_SITES.values()} <= {"member", "automatic"}
    assert {who for who, _n in memory_writers.SITES.values()} == {"member", "automatic", "passes_on"}
    assert all(k[0] == "memory_service.py" for k, (who, _n) in memory_writers.SITES.items() if who == "passes_on")
    assert len(memory_writers.MUTATING) == 13
