"""The list-add hook: every live list writer calls os_shadow.note_add AFTER its commit, and the live add is never changed by it.

Fake DB + the same in-process fake kernel as test_os_shadow.py. Companion pins: a static scan, PER WRITER FUNCTION (not per file),
that every function writing a list item either calls the hook itself or is named (with a reason) below; the detector reads SQL in
any case and spacing, f-strings, ``+``-assembled strings, schema-qualified tables and aliased helpers (each spelling has its own
negative control).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import os_shadow
from test_os_shadow import MEMBER, SVC, kernel, settle  # noqa: F401  (kernel is a fixture)

pytestmark = pytest.mark.ci_safe


# ---- the live writers: a shadow outcome never changes the live one ------------------------------------------------------------------------
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _Txn:
    """A transaction that RECORDS its state and can FAIL at exit (the commit), so a hook moved inside it, or run after a failed
    commit, is caught: ``depth`` > 0 while open, ``committed`` counts clean exits, ``fail_commit`` raises at the outermost exit."""

    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        self.owner.depth += 1
        return self

    async def __aexit__(self, exc_type, *a):
        self.owner.depth -= 1
        if exc_type is None and self.owner.depth == 0:
            if self.owner.fail_commit:
                raise RuntimeError("commit failed")
            self.owner.committed += 1
        return False


class _TxState:
    depth = 0
    committed = 0
    fail_commit = False

    def transaction(self):
        return _Txn(self)


def _spy_hook(monkeypatch, owner):
    """Record, for every hook call, whether a transaction was open and how many commits had landed."""
    seen = []
    real = os_shadow.note_add

    def spy(*a, **k):
        seen.append((owner.depth, owner.committed))
        return real(*a, **k)

    monkeypatch.setattr(os_shadow, "note_add", spy)
    return seen


class _DB(_TxState):
    def __init__(self, fail_commit=False):
        self.writes: list = []
        self.fail_commit = fail_commit

    async def execute(self, sql, params=()):
        if "pg_advisory_xact_lock" in sql:
            return _Cur([])
        if sql.lstrip().upper().startswith("SELECT"):
            return _Cur([{"id": "L1"}] if "FROM lists" in sql else [])
        self.writes.append((sql.split("(")[0].strip(), tuple(params)[1:]))
        return _Cur([])


async def _spoken_add(db=None):
    import list_service
    db = db or _DB()
    out = await list_service.add_items_to_list(db, text="oat milk and rice", user_id=MEMBER, list_type="shopping",
                                               list_name="Shopping", new_list_visibility="family")
    return db.writes, {k: out[k] for k in ("added", "already", "created_list", "deduped")}


@pytest.mark.asyncio
async def test_the_live_add_is_identical_with_the_shadow_off_dead_or_on(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    base_writes, base_out = await _spoken_add()
    assert [w[0] for w in base_writes] == ["INSERT INTO list_items"] * 2 and base_out["added"] == ["oat milk", "rice"]
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    for mode, code in (("ok", "match"), ("refuse", "kernel_refused_live_did"), ("close", "kernel_unavailable"),
                       ("malformed", "kernel_malformed")):
        os_shadow._reset_for_tests()                 # each mode on its own worker and circuit, and its own outcome checked
        assert os_shadow.revive()
        kernel.act_mode = mode
        writes, out = await _spoken_add()
        assert out == base_out and [w[1][1:] for w in writes] == [w[1][1:] for w in base_writes]   # the rows written are unchanged
        settle(2)
        assert os_shadow.counters() == {code: 2}, mode    # THIS mode was exercised, both rows
    os_shadow._reset_for_tests()
    assert os_shadow.revive()
    kernel.srv.close()
    Path(kernel.path).unlink()
    writes, out = await _spoken_add()
    assert out == base_out
    settle(2)
    assert os_shadow.counters() == {"kernel_unavailable": 2}


@pytest.mark.asyncio
async def test_the_spoken_add_hooks_only_after_its_transaction_committed_and_never_after_a_failed_commit(kernel, monkeypatch):
    db = _DB()
    seen = _spy_hook(monkeypatch, db)
    await _spoken_add(db)
    assert seen and all(depth == 0 and committed == 1 for depth, committed in seen), seen     # outside, after the one commit
    failed = _DB(fail_commit=True)
    seen_failed = _spy_hook(monkeypatch, failed)
    with pytest.raises(RuntimeError):
        await _spoken_add(failed)
    assert seen_failed == []                          # the commit failed: no proposal
    settle(2)
    assert os_shadow.counters() == {"match": 2}


@pytest.mark.asyncio
async def test_one_proposal_per_row_that_really_landed(kernel):
    await _spoken_add()                              # two items -> two rows -> two proposals
    settle(2)
    acts = [a["args"]["item"] for _, op, a in kernel.calls if op == "act"]
    assert sorted(acts) == ["oat milk", "rice"] and os_shadow.counters() == {"match": 2}


class _AuthDB:
    def __init__(self, row):
        self.sql, self.row = [], row

    async def execute(self, sql, params=()):
        self.sql.append(sql)
        return _Cur([self.row] if self.row else [])


@pytest.mark.asyncio
async def test_the_rest_add_mirrors_a_custom_list_by_its_name_from_the_row_it_already_read():
    from routers import lists as lists_router

    db = _AuthDB({"id": "L1", "user_id": MEMBER, "visibility": "family", "name": "Camping gear"})
    row = await lists_router._require_list_item_mutation_access(db, "L1", "shopping", {"user_id": MEMBER, "role": "member"}, "add")
    assert row["name"] == "Camping gear" and len(db.sql) == 1                     # the auth read IS the name read: no second query
    fn = next(n for n in ast.parse((SVC / "routers/lists.py").read_text("utf-8")).body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "add_item")
    shadow_calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "value", None) is not None
                    and getattr(c.func.value, "id", "") == "os_shadow"]
    assert [c.func.attr for c in shadow_calls] == ["note_add"]                      # one hook call = one flag read on this path
    name_arg = next(k.value for k in shadow_calls[0].keywords if k.arg == "list_name")
    assert not any(isinstance(n, (ast.Await, ast.Call)) and not (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "get")
                   for n in ast.walk(name_arg)), "the list name must come from data in hand, not a query on the turn"
    assert not (isinstance(name_arg, ast.Name) and name_arg.id == "list_type")      # the old bug: the type sent as the name


# ---- the accepted suggestion: a member's yes is a real add ----------------------------------------------------------------------------
class _Conn(_TxState):
    def __init__(self, boom=False, fail_commit=False):
        self.boom, self.writes, self.fail_commit = boom, [], fail_commit

    async def fetchrow(self, sql, *args):
        if "FROM pending_suggestions" in sql:
            return {"action_type": "list_add", "pre_filled_slots": '{"list_type": "shopping", "item": "oat milk"}'}
        return {"id": "L1"}

    async def execute(self, sql, *args):
        if self.boom and "UPDATE pending_suggestions" in sql:
            raise RuntimeError("commit failed")
        self.writes.append(sql.split("(")[0].strip())


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _A:
            async def __aenter__(s):
                return conn

            async def __aexit__(s, *a):
                return False
        return _A()


def _suggestion_list_name_contract(source):
    tree = ast.parse(source)
    assigned = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ln" for t in n.targets)
                   and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                   and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == "lt"
                   and n.value.func.attr == "capitalize" and not n.value.args and not n.value.keywords for n in ast.walk(tree))
    sql = any(isinstance(n, ast.Constant) and isinstance(n.value, str) and "name=$2" in n.value for n in ast.walk(tree))
    return assigned and sql


@pytest.mark.asyncio
async def test_an_accepted_suggestion_add_is_shadowed_after_its_commit_and_a_failed_one_is_not(kernel, monkeypatch):
    import pending_suggestions

    monkeypatch.setenv("ZOE_ANTICIPATION", "off")
    conn = _Conn()
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: _Pool(conn))
    out = await pending_suggestions.execute_suggestion("s1", MEMBER)
    assert out["ok"] and "INSERT INTO list_items" in conn.writes
    settle(1)
    assert [a["args"]["item"] for _, op, a in kernel.calls if op == "act"] == ["oat milk"] and os_shadow.counters() == {"match": 1}
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: _Pool(_Conn(boom=True)))
    assert not (await pending_suggestions.execute_suggestion("s2", MEMBER))["ok"]     # rolled back: nothing to shadow
    import time
    time.sleep(0.2)
    assert os_shadow.counters() == {"match": 1}


@pytest.mark.asyncio
async def test_the_suggestion_hook_runs_after_the_commit_and_never_after_a_failed_one(kernel, monkeypatch):
    import pending_suggestions

    monkeypatch.setenv("ZOE_ANTICIPATION", "off")
    conn = _Conn()
    seen = _spy_hook(monkeypatch, conn)
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: _Pool(conn))
    assert (await pending_suggestions.execute_suggestion("s1", MEMBER))["ok"]
    assert seen == [(0, 1)]                            # outside the transaction, after its commit
    failed = _Conn(fail_commit=True)                    # every statement succeeds; the COMMIT fails
    seen_failed = _spy_hook(monkeypatch, failed)
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: _Pool(failed))
    out = await pending_suggestions.execute_suggestion("s2", MEMBER)
    assert not out["ok"] and seen_failed == []
    settle(1)
    assert os_shadow.counters() == {"match": 1}


# ---- structure pins: every list-item WRITER FUNCTION calls the hook or is named -------------------------------------------------------
def _consts(tree) -> dict:
    """Names bound to a string constant anywhere in the module (``table = "list_items"``): an f-string or ``+`` that uses the name
    reads as its text."""
    out: dict = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            out.update({t.id: n.value.value for t in n.targets if isinstance(t, ast.Name)})
    return out


def _fold(n, consts: dict):
    """A string expression built from constants (and names bound to constants) with ``+`` or an f-string, or None. An f-string hole
    that is not a known constant reads as ``?``."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.Name) and n.id in consts:
        return consts[n.id]
    if isinstance(n, ast.JoinedStr):
        parts = []
        for v in n.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            else:
                inner = _fold(v.value, consts) if isinstance(v, ast.FormattedValue) else None
                parts.append(inner if inner is not None else "?")
        return "".join(parts)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        left, right = _fold(n.left, consts), _fold(n.right, consts)
        return None if left is None or right is None else left + right
    return None


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _own_nodes(fn):
    """The nodes of ``fn``'s own body, not descending into a nested function, lambda or class (each of those is its own unit)."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, _DEFS + (ast.ClassDef,)):
            stack.extend(ast.iter_child_nodes(n))


def _strings(node, consts: dict) -> list:
    """Every string a unit could hand to SQL in its OWN body: each constant, each f-string, each ``+``-fold."""
    return [t for t in (_fold(n, consts) for n in _own_nodes(node) if isinstance(n, (ast.Constant, ast.JoinedStr, ast.BinOp)))
            if t is not None]


def _refers(node, names) -> bool:
    """``node`` names one of ``names``: ``h``, ``m.h``, ``getattr(m, "h")`` (a literal), ``partial(h, ...)`` /
    ``functools.partial(h, ...)``."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Attribute):
        return node.attr in names
    if isinstance(node, ast.Call):
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            return node.args[1].value in names
        if fname == "partial" and node.args:
            return _refers(node.args[0], names)
    return False


def _bound(n, names) -> set:
    """The names (or attribute names) ``n`` binds to something that refers to one of ``names``."""
    pairs = []
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Tuple) and isinstance(n.value, ast.Tuple):          # a, b = h, g
                pairs += list(zip(t.elts, n.value.elts))
            else:
                pairs.append((t, n.value))
    elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)) and n.value is not None:      # w: Callable = h ; (w := h)
        pairs.append((n.target, n.value))
    elif isinstance(n, ast.arguments):                                                # def f(db, w=h)
        pos = n.posonlyargs + n.args
        pairs += list(zip(pos[len(pos) - len(n.defaults):], n.defaults))
        pairs += [(a, d) for a, d in zip(n.kwonlyargs, n.kw_defaults) if d is not None]
    out = set()
    for target, value in pairs:
        if _refers(value, names):
            name = getattr(target, "id", None) or getattr(target, "attr", None) or getattr(target, "arg", None)   # x / self.x / arg
            if name:
                out.add(name)
    return out


def _aliases(tree, names: frozenset) -> frozenset:
    """``names`` plus every alias of them, any scope: ``from m import h as x``; ``x = h`` / ``x = m.h``; ``x: T = h``; ``(x := h)``;
    ``self.x = h``; ``a, b = h, g``; a default argument ``def f(w=h)``; ``x = partial(h, ...)``; ``x = getattr(m, "h")``; and
    chains of those (``y = x``). Out of scope, said: a name built at run time (``getattr(m, name)``), a container lookup
    (``table["w"]()``), and a helper reached through another module's re-export under a new name."""
    got = set(names)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            got |= {a.asname for a in n.names if a.name in names and a.asname}
    changed = True
    while changed:                      # x = h; y = x
        changed = False
        for n in ast.walk(tree):
            new = _bound(n, got) - got
            if new:
                got |= new
                changed = True
    return frozenset(got)


def _calls_to(node, names: frozenset) -> bool:
    """``node``'s OWN body calls one of ``names`` (a call inside a nested unit belongs to that unit), calls ``getattr(m, "h")(...)``
    or ``partial(h, ...)(...)``, or HANDS one on as an argument (``retry(h, db)``, ``partial(h, db)``, ``run_in_executor(None, h)``)."""
    for n in _own_nodes(node):
        if not isinstance(n, ast.Call):
            continue
        if _refers(n.func, names) or any(_refers(a, names) for a in n.args) or any(_refers(k.value, names) for k in n.keywords):
            return True
    return False


def _units(node, prefix: str):
    """Every function at every nesting level, each its own unit: ``f``, ``C.m``, ``f.inner``, ``f.<lambda:12>``, ``f.Local.m``."""
    for n in _own_nodes(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{n.name}"
        elif isinstance(n, ast.Lambda):
            name = f"{prefix}<lambda:{n.lineno}>"
        elif isinstance(n, ast.ClassDef):
            yield from _units(n, f"{prefix}{n.name}.")
            continue
        else:
            continue
        yield name, n
        yield from _units(n, name + ".")


def _functions(tree):
    """Every unit of ``tree`` (see ``_units``) plus ``<module>``, the top-level statements. A write is attributed to the INNERMOST unit
    whose own body holds it, never to the function that encloses it."""
    yield from _units(tree, "")
    yield "<module>", ast.Module(body=[n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))],
                                 type_ignores=[])


def writer_sites(src: str, sql: "re.Pattern", helpers: frozenset = frozenset()) -> list:
    """The units of ``src`` (every nesting level) whose OWN body writes: a string matching ``sql``, or a call to a writer helper (any
    alias)."""
    tree = ast.parse(src)
    names, consts = _aliases(tree, helpers), _consts(tree)
    return [name for name, fn in _functions(tree)
            if any(sql.search(t) for t in _strings(fn, consts)) or (names and _calls_to(fn, names))]


def hooked(fn, hooks: frozenset) -> bool:
    """``fn`` calls ``os_shadow.<hook>`` in its own body, or in a nested function its body (transitively) USES by name (calls it or
    hands it on); a nested function nothing refers to does not count.

    The rule looks DOWN only, never up: a nested WRITER is hooked by its own body (or what it uses), never because the function around
    it holds a hook, since the scan cannot tell whether the parent runs it before, after or instead of the hooked path. A nested writer
    whose hook really is its parent's names that parent in ``HOOKED_BY_CALLER`` (pinned: the parent is its only user and is hooked)."""
    nested = {n.name: n for n in ast.walk(fn) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn}
    todo, seen = [fn], set()
    while todo:
        f = todo.pop()
        for n in _own_nodes(f):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in hooks
                    and getattr(n.func.value, "id", "") == "os_shadow"):
                return True
            if isinstance(n, ast.Name) and n.id in nested and n.id not in seen:
                seen.add(n.id)
                todo.append(nested[n.id])
    return False


def unhooked(src: str, sql: "re.Pattern", helpers: frozenset, hooks: frozenset) -> list:
    """Writer functions that do not reach an ``os_shadow.<hook>`` call (see ``hooked``)."""
    fns = dict(_functions(ast.parse(src)))
    return [name for name in writer_sites(src, sql, helpers) if not hooked(fns[name], hooks)]


def callers_of(src: str, name: str) -> set:
    """The units of ``src`` whose own body uses ``name`` (the last part of a qualified unit name): calls it, or for a nested unit, also
    hands it on by name."""
    short = name.rsplit(".", 1)[-1]
    nested = "." in name
    out = set()
    for fname, fn in _functions(ast.parse(src)):
        if fname == name:
            continue
        if _calls_to(fn, frozenset({short})) or (nested and any(isinstance(n, ast.Name) and n.id == short for n in _own_nodes(fn))):
            out.add(fname)
    return out


LIST_ADD_SQL = re.compile(r"(?is)\binsert\s+(?:or\s+\w+\s+)?into\s+[\"`]?(?:\w+[\"`]?\.[\"`]?)?list_items\b")
LIST_ADD_HELPERS = frozenset({"create_item_record"})

#: "file::function" -> why that list-item writer is NOT shadowed. A new writer function must call the hook or join this table.
NOT_SHADOWED = {
    "list_service.py::create_item_record": "the row helper itself: every caller is a writer (it calls create_item_record) and is scanned",
    "anticipation.py::_do": "OUT OF SCOPE: the packing list Zoe starts for a trip, many items from one offer; no member add to compare",
}
#: "file::writer" -> the ONE function in that file that calls it and holds the hook (the write runs inside the caller's transaction,
#: so the hook belongs after the caller's commit). Pinned: the caller is the writer's only caller and reaches the hook. A NESTED
#: writer (``outer.inner``) whose hook is its parent's goes here too, with the parent as the caller, under the same pin.
HOOKED_BY_CALLER = {"pending_suggestions.py::_execute_action": "execute_suggestion",
                    # the new-list path: the parent runs it inside its transaction, then hooks after the commit (single add) or hands
                    # the receipts to add_items_to_list, which hooks after ITS commit (that caller is scanned as a writer itself)
                    "list_service.py::add_item_to_list._write_new_list_and_item": "add_item_to_list"}


def _service_files():
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return [p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts)]


def scan(sql, helpers, hooks, table: str) -> tuple[set, set]:
    """``(every writer site, the unhooked ones)`` across zoe-data, as ``file::function``. A file that names neither ``table`` (any
    case) nor a helper cannot match ``sql`` and is not parsed."""
    sites, bare = set(), set()
    for p in _service_files():
        src = p.read_text("utf-8")
        if table.lower() not in src.lower() and not any(h in src for h in helpers):
            continue
        rel = p.relative_to(SVC).as_posix()
        sites |= {f"{rel}::{n}" for n in writer_sites(src, sql, helpers)}
        bare |= {f"{rel}::{n}" for n in unhooked(src, sql, helpers, hooks)}
    return sites, bare


def assert_hooked_by_caller(table: dict, hooks: frozenset) -> None:
    for site, caller in table.items():
        rel, writer = site.split("::")
        src = (SVC / rel).read_text("utf-8")
        assert callers_of(src, writer) == {caller}, f"{site}: its callers are {callers_of(src, writer)}, the hook is only in {caller}"
        assert hooked(dict(_functions(ast.parse(src)))[caller], hooks), f"{rel}::{caller} does not reach os_shadow.{sorted(hooks)}"


def test_every_list_item_writer_function_is_shadowed_or_named_with_a_reason():
    sites, bare = scan(LIST_ADD_SQL, LIST_ADD_HELPERS, frozenset({"note_add"}), "list_items")
    named = set(NOT_SHADOWED) | set(HOOKED_BY_CALLER)
    assert bare == named, ("a list-item writer function neither reaches os_shadow.note_add nor is named in NOT_SHADOWED / HOOKED_BY_CALLER "
                           f"(out of scope must be SAID): {sorted(bare ^ named)}")
    assert_hooked_by_caller(HOOKED_BY_CALLER, frozenset({"note_add"}))
    assert {"list_service.py::add_item_to_list", "skybridge_service.py::_add_spoken_items", "routers/lists.py::add_item"} <= sites - bare


@pytest.mark.parametrize("sql", [
    "INSERT INTO list_items (id) VALUES (1)", "insert into list_items (id) values (1)", "Insert\n   Into\tlist_items(id)",
    'INSERT INTO "list_items" (id)', "INSERT INTO public.list_items (id)", "INSERT OR IGNORE INTO list_items (id)"])
def test_the_detector_reads_every_spelling_of_the_sql(sql):
    assert writer_sites(f"async def f(db):\n    await db.execute({sql!r})", LIST_ADD_SQL) == ["f"]


def test_the_detector_reads_assembled_and_f_string_sql():
    assert writer_sites("async def f(db, t):\n    await db.execute('INSERT INTO ' + 'list_items' + ' (id) VALUES (1)')", LIST_ADD_SQL) == ["f"]
    assert writer_sites("async def f(db, c):\n    await db.execute(f'INSERT INTO list_items ({c}) VALUES (1)')", LIST_ADD_SQL) == ["f"]


def test_the_detector_reads_the_helper_by_any_name():
    for src in ("async def f(db):\n    await create_item_record(db, list_id='x', text='y')",
                "async def f(db):\n    await list_service.create_item_record(db, list_id='x', text='y')",
                "from list_service import create_item_record as cir\nasync def f(db):\n    await cir(db, list_id='x', text='y')"):
        assert writer_sites(src, LIST_ADD_SQL, LIST_ADD_HELPERS) == ["f"], src
    assert writer_sites("def f():\n    return 'SELECT * FROM list_items'", LIST_ADD_SQL, LIST_ADD_HELPERS) == []


def test_one_hooked_writer_does_not_hide_an_unhooked_one_in_the_same_file():
    src = ("async def a(db):\n    await db.execute('INSERT INTO list_items (id) VALUES (1)')\n    os_shadow.note_add('u', item='x')\n"
           "async def b(db):\n    await db.execute('insert into list_items (id) values (2)')\n")
    assert unhooked(src, LIST_ADD_SQL, LIST_ADD_HELPERS, frozenset({"note_add"})) == ["b"]
    hook = frozenset({"note_add"})
    called = ("async def a(db):\n    await db.execute('INSERT INTO list_items (id)')\n    def inner():\n        os_shadow.note_add(1)\n"
              "    inner()\n")
    assert unhooked(called, LIST_ADD_SQL, LIST_ADD_HELPERS, hook) == []
    handed_on = ("async def a(db):\n    async def w():\n        os_shadow.note_add(1)\n"
                 "    await db.execute('INSERT INTO list_items (id)')\n    await retry(w)\n")
    assert unhooked(handed_on, LIST_ADD_SQL, LIST_ADD_HELPERS, hook) == []


def test_a_nested_hook_nothing_calls_does_not_count():
    never = "async def a(db):\n    await db.execute('INSERT INTO list_items (id)')\n    def inner():\n        os_shadow.note_add(1)\n"
    assert unhooked(never, LIST_ADD_SQL, LIST_ADD_HELPERS, frozenset({"note_add"})) == ["a"]
    in_lambda = "async def a(db):\n    await db.execute('INSERT INTO list_items (id)')\n    f = lambda: os_shadow.note_add(1)\n"
    assert unhooked(in_lambda, LIST_ADD_SQL, LIST_ADD_HELPERS, frozenset({"note_add"})) == ["a"]


def test_the_detector_reads_an_assigned_alias_of_the_helper():
    for src in ("async def f(db):\n    writer = create_item_record\n    await writer(db, list_id='x', text='y')",
                "import list_service\nWRITE = list_service.create_item_record\nasync def f(db):\n    await WRITE(db, list_id='x')",
                "from list_service import create_item_record as c\nw = c\nasync def f(db):\n    await w(db)"):
        assert writer_sites(src, LIST_ADD_SQL, LIST_ADD_HELPERS) == ["f"], src


def test_the_detector_reads_a_table_name_held_in_a_variable():
    for src in ("TABLE = 'list_items'\nasync def f(db, c):\n    await db.execute(f'INSERT INTO {TABLE} ({c}) VALUES (1)')",
                "async def f(db):\n    t = 'list_items'\n    await db.execute('INSERT INTO ' + t + ' (id) VALUES (1)')"):
        assert writer_sites(src, LIST_ADD_SQL) == ["f"], src
    assert writer_sites("async def f(db, t):\n    await db.execute(f'INSERT INTO {t} (id)')", LIST_ADD_SQL) == []   # unknowable: not claimed


def test_a_nested_writer_is_its_own_unit_and_a_hooked_parent_does_not_cover_it():
    """Every function at every nesting level is scanned on its own: a write is the innermost unit's, and the hook rule looks down
    only, so a hooked parent does not make an unhooked nested writer (an inner coroutine fired off, a closure) hooked."""
    hook = frozenset({"note_add"})
    fired = ("async def a(db):\n    async def w():\n        await db.execute('INSERT INTO list_items (id)')\n"
             "    asyncio.create_task(w())\n    await db.execute('INSERT INTO list_items (id)')\n    os_shadow.note_add('u', item='x')\n")
    assert writer_sites(fired, LIST_ADD_SQL, LIST_ADD_HELPERS) == ["a", "a.w"]
    assert unhooked(fired, LIST_ADD_SQL, LIST_ADD_HELPERS, hook) == ["a.w"]
    closure = ("def a(db):\n    os_shadow.note_add('u', item='x')\n    def w():\n        create_item_record(db, list_id='x', text='y')\n"
               "    return w\n")
    assert unhooked(closure, LIST_ADD_SQL, LIST_ADD_HELPERS, hook) == ["a.w"]
    own = ("async def a(db):\n    async def w():\n        await db.execute('INSERT INTO list_items (id)')\n"
           "        os_shadow.note_add('u', item='x')\n    await w()\n")
    assert writer_sites(own, LIST_ADD_SQL, LIST_ADD_HELPERS) == ["a.w"] and unhooked(own, LIST_ADD_SQL, LIST_ADD_HELPERS, hook) == []
    in_lambda = "def a(db):\n    os_shadow.note_add('u', item='x')\n    f = lambda: db.execute('INSERT INTO list_items (id)')\n"
    assert unhooked(in_lambda, LIST_ADD_SQL, LIST_ADD_HELPERS, hook) == ["a.<lambda:3>"]
    deep = ("class C:\n    def m(self, db):\n        def inner():\n            async def deeper():\n"
            "                await db.execute('INSERT INTO list_items (id)')\n            return deeper\n        return inner\n")
    assert unhooked(deep, LIST_ADD_SQL, LIST_ADD_HELPERS, hook) == ["C.m.inner.deeper"]
    assert callers_of(fired, "a.w") == {"a"}                       # what HOOKED_BY_CALLER pins for a nested writer named there



@pytest.mark.parametrize("src", [
    "async def f(db):\n    writer: Callable = create_item_record\n    await writer(db)",                          # annotated alias
    "async def f(db):\n    if (w := create_item_record):\n        await w(db)",                                   # walrus
    "class C:\n    def __init__(self):\n        self.w = create_item_record\n    async def f(self, db):\n        await self.w(db)",
    "async def f(db):\n    a, b = create_item_record, print\n    await a(db)",                                    # tuple unpacking
    "async def f(db, w=create_item_record):\n    await w(db)",                                                    # default argument
    "async def f(db, *, w=create_item_record):\n    await w(db)",                                                 # keyword-only default
    "import functools\nasync def f(db):\n    w = functools.partial(create_item_record, db)\n    await w()",        # partial alias
    "from functools import partial\nasync def f(db):\n    await partial(create_item_record, db)()",                # partial called
    "import list_service\nasync def f(db):\n    w = getattr(list_service, 'create_item_record')\n    await w(db)",  # getattr literal
    "import list_service\nasync def f(db):\n    await getattr(list_service, 'create_item_record')(db)",
    "async def f(db):\n    await retry(create_item_record, db)",                                                  # handed on
    "import list_service as ls\nasync def f(db):\n    await ls.create_item_record(db)",                              # import-as module
])
def test_the_detector_reads_every_handled_alias_form(src):
    sites = writer_sites(src, LIST_ADD_SQL, LIST_ADD_HELPERS)
    assert sites and all(s.endswith("f") for s in sites), (src, sites)


def test_out_of_scope_alias_forms_are_not_claimed():
    assert writer_sites("import list_service\nasync def f(db, n):\n    await getattr(list_service, n)(db)", LIST_ADD_SQL,
                        LIST_ADD_HELPERS) == []          # a name built at run time: unknowable statically, not claimed


# ---- the shadow is told the list the row really lands on, not the words -----------------------------------------------------------
class _GroceriesDB(_DB):
    """The household shopping list is called "Groceries": a "Shopping" add lands there (the household rule picks the fullest)."""

    async def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT") and "FROM lists" in sql:
            return _Cur([{"id": "L1", "name": "Groceries", "created_at": "2030-01-01", "n": 3}])
        return await super().execute(sql, params)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["spoken", "single"])
async def test_an_add_is_proposed_against_the_list_it_was_written_to(kernel, path):
    import list_service

    db = _GroceriesDB()
    if path == "spoken":
        await list_service.add_items_to_list(db, text="oat milk", user_id=MEMBER, list_type="shopping", list_name="Shopping",
                                             new_list_visibility="family")
    else:
        await list_service.add_item_to_list(db, user_id=MEMBER, list_type="shopping", list_name="Shopping", text="oat milk",
                                            new_list_visibility="family")
    settle(1)
    (act,) = [a for _, op, a in kernel.calls if op == "act"]
    assert act["args"]["list"] == "Groceries"                         # the written list, never the requested "Shopping"


@pytest.mark.asyncio
async def test_a_list_the_add_creates_is_proposed_under_the_name_it_was_created_with(kernel):
    import list_service

    class _NoListDB(_DB):
        async def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith("SELECT") and "FROM lists" in sql:
                return _Cur([])
            return await super().execute(sql, params)

    out = await list_service.add_item_to_list(_NoListDB(), user_id=MEMBER, list_type="shopping", list_name="Camping gear",
                                              text="tent pegs", new_list_visibility="family")
    settle(1)
    assert out["created_list"] and [a["args"]["list"] for _, op, a in kernel.calls if op == "act"] == ["Camping gear"]


def test_find_list_id_is_unchanged_and_find_list_adds_the_name():
    import asyncio

    import list_service

    db = _GroceriesDB()
    assert asyncio.run(list_service.find_list_id(db, user_id=MEMBER, list_type="shopping", name="Shopping")) == "L1"
    assert asyncio.run(list_service.find_list(db, user_id=MEMBER, list_type="shopping", name="Shopping")) == ("L1", "Groceries")



@pytest.mark.asyncio
async def test_an_accepted_suggestion_is_proposed_against_the_list_row_it_wrote_to(kernel, monkeypatch):
    """The suggestion path (``_execute_action``) finds its list by the EXACT name ``list_type.capitalize()`` (``name=$2``) or creates
    it with that name, so the row's name IS that value: the hook passes the same expression (the train review's third finding is
    not a mismatch; this pins the invariant it relies on)."""
    import inspect

    import pending_suggestions

    monkeypatch.setenv("ZOE_ANTICIPATION", "off")
    conn = _Conn()
    monkeypatch.setattr(pending_suggestions, "get_pool", lambda: _Pool(conn))
    out = await pending_suggestions.execute_suggestion("s1", MEMBER)
    settle(1)
    assert out["ok"] and [a["args"]["list"] for _, op, a in kernel.calls if op == "act"] == ["Shopping"]
    src = inspect.getsource(pending_suggestions._execute_action)
    assert _suggestion_list_name_contract(src)
    # Remove the assignment, leaving a comment and a string naming the old call.
    assignment = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "ln" for t in n.targets))
    lines = src.splitlines(keepends=True)
    for replacement in ("# ln = lt.capitalize()\n        pass", repr("ln = lt.capitalize()")):
        changed = lines[:assignment.lineno - 1] + ["        " + replacement + "\n"] + lines[assignment.end_lineno:]
        assert not _suggestion_list_name_contract("".join(changed))
