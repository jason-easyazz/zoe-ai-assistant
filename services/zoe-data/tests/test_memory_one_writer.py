"""Only ``memory_service.py`` writes the memory rows (the palace collection), bar FOUR named bypass writes.

The rows are a collection, not SQL, so the scan is over calls, over the WHOLE service at once (``scan``): a write call
(``upsert``, ``update``, ``delete``, ``add``, ``modify``, also ``getattr(col, "delete")(...)``) on anything that came from the
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
    attribute name over the whole set); either branch of ``a if c else b`` / ``a or b``. A write is a WRITES method named on it
    (called, or handed on as a callable) or ``getattr(<it>, "<write>")``."""
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
    found: Counter = Counter()
    for u, nodes in own.items():
        names = names_of(u)
        for node in nodes:
            # the write method named on a collection: called here, or handed on as a callable (``_store(col.update, ...)``)
            if isinstance(node, ast.Attribute) and node.attr in WRITES and is_col(node.value, names):
                found[u] += 1
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Call) and getattr(node.func.func, "id", "") == "getattr" \
                    and len(node.func.args) >= 2 and isinstance(node.func.args[1], ast.Constant) \
                    and node.func.args[1].value in WRITES and is_col(node.func.args[0], names):
                found[u] += 1                               # ``getattr(col, "delete")(...)``; a bare ``getattr`` probe is a read
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


# ---- every call of a mutating method, and who is acting there -------------------------------------------------------------------
def mutating_calls(source: str, rel: str) -> Counter:
    """``{(module, function, method): calls}`` of the mutating method names in one module (nested functions by dotted name).

    Every REFERENCE counts, not only a call: ``svc.ingest(...)``, but also the bound method handed on
    (``asyncio.to_thread(svc.erase_rows, ...)``, ``partial(svc.review, ...)``, ``f = svc.ingest``) and
    ``getattr(svc, "review")``, so a write run through an executor or an alias is still a site in the table."""
    import memory_writers

    found: Counter = Counter()

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            inner = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = child.name if not fn else f"{fn}.{child.name}"
            if isinstance(child, ast.Attribute) and child.attr in memory_writers.MUTATING:
                found[(rel, fn, child.attr)] += 1
            elif isinstance(child, ast.Call) and getattr(child.func, "id", "") == "getattr" and len(child.args) >= 2 \
                    and isinstance(child.args[1], ast.Constant) and child.args[1].value in memory_writers.MUTATING:
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
    added WITH who is acting there. Control run: a second ``svc.ingest`` planted in ``routers/notes._store_note_memory`` fails."""
    import memory_writers

    found: Counter = Counter()
    for rel, path in _modules():
        found.update(mutating_calls(path.read_text("utf-8"), rel))
    assert service_calls(found, memory_writers.NOT_THE_SERVICE) == {k: n for k, (_who, n) in memory_writers.SITES.items()}
    assert {who for who, _n in memory_writers.SITES.values()} == {"member", "automatic", "passes_on"}
    assert len(memory_writers.MUTATING) == 13


# ---- every call site says WHO IS ACTING (memory_actor) --------------------------------------------------------------------------
PASS_ON_HELPERS = {"_ingest_or_supersede", "retire_twins"}       # told who is acting by their own callers


def _first_await(fn) -> int:
    return min((n.lineno for n in ast.walk(fn) if isinstance(n, ast.Await)), default=10 ** 9)


def _member_built_first(fn) -> bool:
    """Does this entry point build the acting member before anything is awaited? ``acting = memory_actor.member(...)``, or
    ``memory_actor.Member(id, stamps)`` over stamps ``kernel_door.stamps_now(..., "memory")`` took before the first await."""
    first = _first_await(fn)
    stamped = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        text = ast.unparse(node.value)
        if text.startswith("kernel_door.stamps_now(") and "'memory'" in text and node.lineno < first:
            stamped.add(node.targets[0].id)
        if node.targets[0].id != "acting":
            continue
        if text.startswith("memory_actor.member(") and node.lineno < first:
            return True
        if text.startswith("memory_actor.Member(") and isinstance(node.value, ast.Call) and len(node.value.args) == 2 \
                and isinstance(node.value.args[1], ast.Name) and node.value.args[1].id in stamped:
            return True
    return False


def acting_faults(source: str, rel: str) -> list:
    """Every mutating call (and every call of a pass-on helper) that does not say who is acting the way its row of
    ``tests/memory_writers.py`` says it must."""
    import memory_writers

    faults: list = []

    def walk(node, fn, outer):
        for child in ast.iter_child_nodes(node):
            inner, top = fn, outer
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = child.name if not fn else f"{fn}.{child.name}"
                top = outer or child
            if isinstance(child, ast.Call):
                name = child.func.attr if isinstance(child.func, ast.Attribute) else getattr(child.func, "id", "")
                site = (rel, fn, name)
                given = next((ast.unparse(k.value) for k in child.keywords if k.arg == "acting"), None)
                if name in PASS_ON_HELPERS and given is None:
                    faults.append((rel, fn, name, "a pass-on helper called without acting="))
                if site in memory_writers.SITES:
                    who = memory_writers.SITES[site][0]
                    params = {a.arg for f in ast.walk(outer) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                              for a in f.args.args + f.args.kwonlyargs} if outer is not None else set()
                    if given is None:
                        faults.append((*site, "no acting="))
                    elif who == "automatic" and given != "memory_actor.AUTOMATIC":
                        faults.append((*site, "an automatic writer must pass memory_actor.AUTOMATIC"))
                    elif who == "passes_on" and not (given == "acting" and "acting" in params):
                        faults.append((*site, "must hand on its own acting parameter"))
                    elif who == "member" and not (given == "acting" and outer is not None and _member_built_first(outer)):
                        faults.append((*site, "a member site must pass the member built before the first await"))
            walk(child, inner, top)

    walk(ast.parse(source), "", None)
    return faults


def test_every_call_site_says_who_is_acting_and_a_member_is_built_before_anything_is_awaited():
    """Control runs: ``acting=acting`` dropped from ``memory_disputes.resolve``'s review; a digest ingest passing a member;
    ``memory_space.forget_row`` building ``memory_actor.Member(user_id)`` itself (no stamps of the moment); ``retire_twins``
    called without ``acting=``: each fails this."""
    faults = []
    for rel, path in _modules():
        faults += acting_faults(path.read_text("utf-8"), rel)
    assert faults == []


def test_the_scan_sees_each_way_a_site_can_fail_to_say_who_is_acting(monkeypatch):
    import memory_writers

    monkeypatch.setattr(memory_writers, "SITES", {("m.py", "ask", "ingest"): ("member", 1), ("m.py", "late", "ingest"): ("member", 1),
                                                  ("m.py", "night", "ingest"): ("automatic", 1), ("m.py", "helper", "review"): ("passes_on", 1),
                                                  ("m.py", "silent", "ingest"): ("member", 1), ("m.py", "tool", "ingest"): ("member", 1)})
    src = '''
async def ask(svc, user_id):
    acting = memory_actor.member(user_id)
    await svc.ingest("x", user_id=user_id, source="s", acting=acting)
async def late(svc, user_id):
    await other()
    acting = memory_actor.member(user_id)
    await svc.ingest("x", user_id=user_id, source="s", acting=acting)
async def night(svc, user_id):
    await svc.ingest("x", user_id=user_id, source="s", acting=memory_actor.member(user_id))
async def helper(svc, row, *, acting):
    await svc.review(row, decision="edit", actor="a", acting=acting)
async def silent(svc, user_id):
    await svc.ingest("x", user_id=user_id, source="s")
async def tool(svc, db):
    stamps_at = kernel_door.stamps_now("lists", "memory")
    user_id = await who(db)
    acting = memory_actor.Member(user_id, stamps_at)
    await svc.ingest("x", user_id=user_id, source="s", acting=acting)
async def caller(svc, tw):
    await retire_twins("u", tw, svc)
'''
    assert sorted(f[1:3] for f in acting_faults(src, "m.py")) == [("caller", "retire_twins"), ("late", "ingest"), ("night", "ingest"),
                                                                 ("silent", "ingest")]


def test_a_mutating_method_refuses_a_call_that_does_not_say_who_is_acting():
    """No default in production: the real method (``unwrapped``: tests get AUTOMATIC from ``conftest``) raises ``TypeError``
    with no ``acting``, and with anything that is neither a member nor AUTOMATIC. Control runs: a default on a mutating method fails; ``memory_actor.check``
    accepting anything fails."""
    import asyncio
    import inspect

    import memory_actor
    import memory_service
    import memory_writers

    svc = memory_service.MemoryService(data_dir="/nonexistent/zoe-memory-acting")
    for name in memory_writers.MUTATING:
        real = getattr(memory_service.MemoryService, name).unwrapped
        param = inspect.signature(real).parameters["acting"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty, name
    with pytest.raises(TypeError):
        asyncio.run(memory_service.MemoryService.review.unwrapped(svc, "row0001", decision="edit", actor="a"))
    with pytest.raises(TypeError):
        asyncio.run(memory_service.MemoryService.review.unwrapped(svc, "row0001", decision="edit", actor="a", acting="member-ann"))
    assert memory_actor.check(memory_actor.AUTOMATIC) is memory_actor.AUTOMATIC
    assert memory_actor.check(memory_actor.Member("member-ann")).user_id == "member-ann"


# ---- the tracked Python OUTSIDE the service that calls it (maintenance / performance scripts, labs) --------------------------------
REPO = SVC.parents[1]
#: how a caller outside the service names the automatic actor (a script imports from the zoe-data path at run time)
AUTOMATIC_SPELLINGS = {"memory_actor.AUTOMATIC", "__import__('memory_actor').AUTOMATIC"}
#: the same method names on something that is NOT the memory service, by exact site WITH A COUNT: (file, function, method) -> calls
SCRIPTS_NOT_THE_SERVICE = {
    ("scripts/perf/zmb/arms/zma.py", "ingest", "ingest"): 2,          # the Z0 arm's own ingest (self.z0.ingest)
    ("scripts/perf/zmb/bakeoff_measure.py", "start", "ingest"): 1,    # a benchmark arm's ingest (self.arm.ingest)
    ("scripts/perf/zmb/bakeoff_measure.py", "finish", "ingest"): 1,
    ("scripts/perf/zmb/bakeoff_measure.py", "phase_latency", "ingest"): 1,
}
#: the files outside the service whose calls are checked (the scan must not go quiet: a file that stops being found fails)
SERVICE_CALLERS_OUTSIDE = {
    "labs/memory-claims-lab/run_baseline.py", "scripts/maintenance/forget_command_rows_audit.py",
    "scripts/maintenance/guest_memory_report.py", "scripts/maintenance/identity_memory_audit.py",
    "scripts/maintenance/memory_prompt_packet_measure.py", "scripts/maintenance/mempalace_baseline.py",
    "scripts/maintenance/restore_memories_from_export.py", "scripts/perf/recall_gate_replay.py", "scripts/perf/zmb/arms/z0.py",
}


def _tracked_python_outside_the_service():
    """``(repo-relative path, source)`` of every tracked Python file outside ``services/zoe-data`` that names the memory service
    (tests aside: their calls default to automatic in the test configuration). ``git ls-files`` when there is a checkout, else
    every ``.py`` under ``scripts/``, ``labs/`` and ``tools/``."""
    import re
    import subprocess

    try:
        out = subprocess.run(["git", "ls-files", "-z", "--", "*.py"], cwd=REPO, capture_output=True, check=True, timeout=30)
        files = [f for f in out.stdout.decode("utf-8").split("\0") if f]
    except Exception:  # noqa: BLE001 - no git (a sold box): the same directories, read from disk
        files = [p.relative_to(REPO).as_posix() for top in ("scripts", "labs", "tools") for p in sorted((REPO / top).rglob("*.py"))]
    for rel in sorted(files):
        parts = rel.split("/")
        if rel.startswith("services/zoe-data/") or "tests" in parts or parts[-1].startswith("test_") or parts[-1] == "conftest.py" \
                or ".venv" in parts or "node_modules" in parts:
            continue
        path = REPO / rel
        if not path.is_file():
            continue
        text = path.read_text("utf-8", errors="replace")
        if re.search(r"\bmemory_service\b|get_memory_service|MemoryService", text):
            yield rel, text


def script_acting_faults(source: str, rel: str) -> Counter:
    """``{(file, function, method): calls}`` of the mutating calls in a caller outside the service that do not say they are
    AUTOMATIC (no ``acting=``, or anything else passed: nothing a script does is a member asking)."""
    import memory_writers

    faults: Counter = Counter()

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            inner = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = child.name if not fn else f"{fn}.{child.name}"
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in memory_writers.MUTATING:
                given = next((ast.unparse(k.value) for k in child.keywords if k.arg == "acting"), None)
                if given not in AUTOMATIC_SPELLINGS:
                    faults[(rel, fn, child.func.attr)] += 1
            walk(child, inner)

    walk(ast.parse(source), "")
    return faults


def test_every_call_outside_the_service_says_it_is_automatic():
    """The maintenance and performance scripts and the labs that call the memory service pass ``memory_actor.AUTOMATIC`` on
    every mutating call (the service refuses a call that does not say; a script passing a member would be a script acting as
    a person). Other-store calls of the same names are exempt by exact site and count. Control runs: ``acting=`` dropped from
    ``scripts/maintenance/guest_memory_report.py``'s ``erase_rows`` fails this; the same call passing
    ``memory_actor.member(owner)`` fails it; ``acting=`` dropped from the lab's ``ingest`` (``labs/memory-claims-lab``) fails it;
    ``SERVICE_CALLERS_OUTSIDE`` losing a file (the scan gone quiet) fails it."""
    faults: Counter = Counter()
    callers = set()
    for rel, text in _tracked_python_outside_the_service():
        import memory_writers

        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in memory_writers.MUTATING
               for n in ast.walk(ast.parse(text))):
            callers.add(rel)
        faults.update(script_acting_faults(text, rel))
    for key, n in SCRIPTS_NOT_THE_SERVICE.items():
        assert faults.get(key, 0) >= n, f"{key}: {faults.get(key, 0)} calls, the exemption names {n}"
        faults[key] -= n
    assert {k: n for k, n in faults.items() if n} == {}
    assert callers - {k[0] for k in SCRIPTS_NOT_THE_SERVICE} == SERVICE_CALLERS_OUTSIDE


def test_the_outside_scan_sees_a_missing_or_wrong_actor():
    src = '''
async def ok(svc):
    await svc.erase_rows("u", ["a"], actor="x", acting=__import__("memory_actor").AUTOMATIC)
    await svc.ingest("t", user_id="u", acting=memory_actor.AUTOMATIC)
async def missing(svc):
    await svc.erase_rows("u", ["a"], actor="x")
async def member(svc):
    await svc.review("r", decision="archive", actor="x", acting=memory_actor.member("u"))
'''
    assert script_acting_faults(src, "s.py") == Counter({("s.py", "missing", "erase_rows"): 1, ("s.py", "member", "review"): 1})
