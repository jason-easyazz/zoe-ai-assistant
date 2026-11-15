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


class _DB:
    def __init__(self):
        self.writes: list = []

    async def execute(self, sql, params=()):
        if "pg_advisory_xact_lock" in sql:
            return _Cur([])
        if sql.lstrip().upper().startswith("SELECT"):
            return _Cur([{"id": "L1"}] if "FROM lists" in sql else [])
        self.writes.append((sql.split("(")[0].strip(), tuple(params)[1:]))
        return _Cur([])

    def transaction(self):
        class _T:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False
        return _T()


async def _spoken_add():
    import list_service
    db = _DB()
    out = await list_service.add_items_to_list(db, text="oat milk and rice", user_id=MEMBER, list_type="shopping",
                                               list_name="Shopping", new_list_visibility="family")
    return db.writes, {k: out[k] for k in ("added", "already", "created_list", "deduped")}


@pytest.mark.asyncio
async def test_the_live_add_is_identical_with_the_shadow_off_dead_or_on(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    base_writes, base_out = await _spoken_add()
    assert [w[0] for w in base_writes] == ["INSERT INTO list_items"] * 2 and base_out["added"] == ["oat milk", "rice"]
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    for mode in ("ok", "refuse", "close", "malformed"):
        kernel.act_mode = mode
        writes, out = await _spoken_add()
        assert out == base_out and [w[1][1:] for w in writes] == [w[1][1:] for w in base_writes]   # the rows written are unchanged
    kernel.srv.close()
    Path(kernel.path).unlink()
    writes, out = await _spoken_add()
    assert out == base_out
    settle(1)


@pytest.mark.asyncio
async def test_one_proposal_per_row_that_really_landed(kernel):
    await _spoken_add()                              # two items -> two rows -> two proposals
    settle(2)
    acts = [a["args"]["item"] for _, op, a in kernel.calls if op == "act"]
    assert sorted(acts) == ["oat milk", "rice"] and os_shadow.counters() == {"match": 2}


class _NameDB:
    def __init__(self, row=None, boom=False):
        self.sql, self.row, self.boom = [], row, boom

    async def execute(self, sql, params=()):
        self.sql.append(sql)
        if self.boom:
            raise RuntimeError("db down")
        return _Cur([self.row] if self.row else [])


@pytest.mark.asyncio
async def test_the_rest_add_mirrors_a_custom_list_by_its_name_not_its_type(kernel, monkeypatch):
    from routers import lists as lists_router

    assert await lists_router._shadow_list_name(_NameDB({"name": "Camping gear"}), "L1") == "Camping gear"
    assert await lists_router._shadow_list_name(_NameDB(None), "L1") is None
    assert await lists_router._shadow_list_name(_NameDB(boom=True), "L1") is None          # never raises into the add
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    off = _NameDB({"name": "Camping gear"})
    assert await lists_router._shadow_list_name(off, "L1") is None and off.sql == []       # off: no query at all
    call = next(n for n in ast.walk(ast.parse((SVC / "routers/lists.py").read_text("utf-8")))
                if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "note_add")
    name_arg = next(k.value for k in call.keywords if k.arg == "list_name")
    assert not (isinstance(name_arg, ast.Name) and name_arg.id == "list_type")             # the old bug: the type sent as the name


# ---- structure pins: every list-item WRITER FUNCTION calls the hook or is named -------------------------------------------------------
def _fold(n):
    """A string expression built from constants with ``+`` (f-string holes as ``?``), or None."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "?" for v in n.values)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        left, right = _fold(n.left), _fold(n.right)
        return None if left is None or right is None else left + right
    return None


def _strings(node) -> list:
    """Every string a function could hand to SQL: each constant, each f-string, each ``+``-fold of constants."""
    return [t for t in (_fold(n) for n in ast.walk(node) if isinstance(n, (ast.Constant, ast.JoinedStr, ast.BinOp))) if t is not None]


def _aliases(tree, names: frozenset) -> frozenset:
    """``names`` plus every local alias they were imported as (``from list_service import create_item_record as cir``)."""
    got = set(names)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            got |= {a.asname for a in n.names if a.name in names and a.asname}
    return frozenset(got)


def _calls_to(node, names: frozenset) -> bool:
    return any(isinstance(n, ast.Call) and (getattr(n.func, "id", None) in names or getattr(n.func, "attr", None) in names)
               for n in ast.walk(node))


def _functions(tree):
    """Top-level functions and class methods (a nested function belongs to the function it is in)."""
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n.name, n
        elif isinstance(n, ast.ClassDef):
            yield from ((f"{n.name}.{m.name}", m) for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)))
    yield "<module>", ast.Module(body=[n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))],
                                 type_ignores=[])


def writer_sites(src: str, sql: "re.Pattern", helpers: frozenset = frozenset()) -> list:
    """The names of the functions in ``src`` that write: a string matching ``sql``, or a call to a writer helper (any alias)."""
    tree = ast.parse(src)
    names = _aliases(tree, helpers)
    return [name for name, fn in _functions(tree) if any(sql.search(t) for t in _strings(fn)) or (names and _calls_to(fn, names))]


def unhooked(src: str, sql: "re.Pattern", helpers: frozenset, hooks: frozenset) -> list:
    """Writer functions that do not call ``os_shadow.<hook>`` themselves (anywhere inside them, nested functions included)."""
    tree = ast.parse(src)
    fns = dict(_functions(tree))

    def hooked(fn) -> bool:
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in hooks
                   and getattr(n.func.value, "id", "") == "os_shadow" for n in ast.walk(fn))
    return [name for name in writer_sites(src, sql, helpers) if not hooked(fns[name])]


LIST_ADD_SQL = re.compile(r"(?is)\binsert\s+(?:or\s+\w+\s+)?into\s+[\"`]?(?:\w+[\"`]?\.[\"`]?)?list_items\b")
LIST_ADD_HELPERS = frozenset({"create_item_record"})

#: "file::function" -> why that list-item writer is NOT shadowed. A new writer function must call the hook or join this table.
NOT_SHADOWED = {
    "list_service.py::create_item_record": "the row helper itself: every caller is a writer (it calls create_item_record) and is scanned",
    "pending_suggestions.py::_execute_action": "OUT OF SCOPE: Zoe's own offer, accepted by the member, not a member's add; shadowing it "
                                               "compares the kernel with an offer, a later, separate decision",
    "anticipation.py::_do": "OUT OF SCOPE: the packing list Zoe starts for a trip, many items from one offer; no member add to compare",
}


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


def test_every_list_item_writer_function_is_shadowed_or_named_with_a_reason():
    sites, bare = scan(LIST_ADD_SQL, LIST_ADD_HELPERS, frozenset({"note_add"}), "list_items")
    assert bare == set(NOT_SHADOWED), ("a list-item writer function neither calls os_shadow.note_add nor is named in NOT_SHADOWED "
                                       f"(out of scope must be SAID): {sorted(bare ^ set(NOT_SHADOWED))}")
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
    assert unhooked("async def a(db):\n    await db.execute('INSERT INTO list_items (id)')\n    def inner():\n        os_shadow.note_add(1)\n",
                    LIST_ADD_SQL, LIST_ADD_HELPERS, frozenset({"note_add"})) == []
