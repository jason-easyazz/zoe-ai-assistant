"""Only ``memory_service.py`` writes the memory rows (the palace collection), bar FOUR named bypass writes.

The rows are a collection, not SQL, so the scan is over calls: in every module of the service, a write call (``upsert``,
``update``, ``delete``, ``add``, ``modify``) on anything that came from the collection (``leased_drawers(...)``,
``._collection()``, ``get_drawers_collection(...)``, ``get_collection`` / ``get_or_create_collection``, through any chain of
assignments, a parameter such a value is passed to inside the module, or directly on the call). No file is exempt by name; the
four allowed writes are named by module and function WITH A COUNT, so a fifth fails.

The four (all AUTOMATIC, all on ``main``): ``memory_digest.py`` writes rows back from its night passes in three write calls
(``_upsert_rows``: one; ``_update_existing``: an update, or an upsert for a store with no update), and ``zoe_agent.py`` has a
one-off migration (``migrate_mempalace_legacy_records``). They are faults on main to route through ``MemoryService`` later.

Negative control run: a ``col.update`` planted in ``memory_digest._live_ids`` fails
``test_only_the_memory_service_writes_the_collection`` (and a write planted in a module that has none fails it too).
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
SOURCES = {"leased_drawers", "_collection", "_audit_collection", "get_drawers_collection", "get_collection", "get_or_create_collection"}
#: the bypass writes on main: (module, function) -> how many write calls
BYPASS = {("memory_digest.py", "_upsert_rows"): 1, ("memory_digest.py", "_update_existing"): 2,
          ("zoe_agent.py", "migrate_mempalace_legacy_records"): 1}


def _from_collection(node, names: set) -> bool:
    """Is this expression the collection: a call of one of SOURCES, or a name already known to hold it?"""
    while isinstance(node, ast.Await):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Call):
        fn = node.func
        return (fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")) in SOURCES
    return False


def _functions(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def collection_writes(source: str) -> Counter:
    """``{function name: write calls on the collection}`` for one module's source."""
    tree = ast.parse(source)
    fns = {f.name: f for f in _functions(tree)}
    # per function: the names that hold the collection. A parameter CALLED the collection is one (a write handed to an
    # executor through ``functools.partial`` reaches its helper with no call the scan could follow).
    holds: dict = {name: {a.arg for a in fn.args.args + fn.args.kwonlyargs if a.arg in COLLECTION_PARAMS} for name, fn in fns.items()}
    changed = True
    while changed:                                          # assignments, aliases, and parameters fed a collection by a caller
        changed = False
        for name, fn in fns.items():
            for node in ast.walk(fn):
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None \
                        and _from_collection(node.value, holds[name]):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if isinstance(t, ast.Name) and t.id not in holds[name]:
                            holds[name].add(t.id)
                            changed = True
                if isinstance(node, ast.Call):
                    callee = fns.get(node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", ""))
                    if callee is None:
                        continue
                    params = [a.arg for a in callee.args.args]
                    if params[:1] == ["self"]:
                        params = params[1:]
                    passed = [(params[i], a) for i, a in enumerate(node.args) if i < len(params)]
                    passed += [(k.arg, k.value) for k in node.keywords if k.arg]
                    for param, value in passed:
                        if _from_collection(value, holds[name]) and param not in holds[callee.name]:
                            holds[callee.name].add(param)
                            changed = True
    found: Counter = Counter()
    for name, fn in fns.items():
        inner = {n for sub in ast.walk(fn) if sub is not fn and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                 for n in ast.walk(sub)}
        for node in ast.walk(fn):
            if node in inner:
                continue
            # the write method named on a collection: called here, or handed on as a callable (``_store(col.update, ...)``)
            if isinstance(node, ast.Attribute) and node.attr in WRITES and _from_collection(node.value, holds[name]):
                found[name] += 1
    return found


def _modules():
    for path in sorted(SVC.rglob("*.py")):
        rel = path.relative_to(SVC).as_posix()
        if rel.startswith(("tests/", "scripts/")) or "/tests/" in rel or ".venv" in rel or "node_modules" in rel:
            continue
        yield rel, path


def test_only_the_memory_service_writes_the_collection():
    found = {}
    for rel, path in _modules():
        if rel == "memory_service.py":
            continue
        for fn, n in collection_writes(path.read_text("utf-8")).items():
            found[(rel, fn)] = n
    assert found == BYPASS
    assert sum(BYPASS.values()) == 4


def test_the_scan_sees_each_way_a_write_can_be_spelled():
    """The reader finds a write through a lease, an alias, a helper's parameter and a direct chain, and ignores reads and
    dict updates. Control run: with ``_from_collection`` answering False for names, the alias and parameter forms are missed."""
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


def test_the_memory_service_itself_is_seen_writing():
    """The scan is not blind to the one writer: it finds the service's own row writes."""
    found = collection_writes((SVC / "memory_service.py").read_text("utf-8"))
    assert found["_write_row"] == 1 and found["_update_row"] >= 1 and sum(found.values()) >= 15


# ---- every call of a mutating method, and who is acting there -------------------------------------------------------------------
def mutating_calls(source: str, rel: str) -> Counter:
    """``{(module, function, method): calls}`` of the mutating method names in one module (nested functions by dotted name)."""
    import memory_writers

    found: Counter = Counter()

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            inner = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = child.name if not fn else f"{fn}.{child.name}"
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in memory_writers.MUTATING:
                found[(rel, fn, child.func.attr)] += 1
            walk(child, inner)

    walk(ast.parse(source), "")
    return found


def test_every_call_of_a_mutating_method_is_in_the_table_with_who_is_acting():
    """``tests/memory_writers.py`` is the whole list: a new call site, or one more call in a listed function, fails until it is
    added WITH who is acting there. Control run: a second ``svc.ingest`` planted in ``routers/notes._store_note_memory`` fails."""
    import memory_writers

    found: Counter = Counter()
    for rel, path in _modules():
        found.update(mutating_calls(path.read_text("utf-8"), rel))
    service = {k: n for k, n in found.items() if k not in memory_writers.NOT_THE_SERVICE}
    assert service == {k: n for k, (_who, n) in memory_writers.SITES.items()}
    assert memory_writers.NOT_THE_SERVICE <= set(found)
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
