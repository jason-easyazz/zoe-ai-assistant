"""Member B never reads member A's private rows: every GET route that reads an owned table is scoped (audit row B3, 2026-11-16).

The charter rule (ZOE_DESIGN_PRINCIPLES.md section 3): "Personal scope of user A is never visible to user B". Until now it was pinned
per feature only (lists, voice packets, biometrics), so a NEW read route could serve another member's notes and nothing failed.

WHY STATIC. The stores live in Postgres (``database.py``: asyncpg) and a route needs the live pool, so seeding two members and calling
every GET in-process cannot run offline in the slim ``ci_safe`` lane (tests/AGENTS.md: a test needing Postgres stays unmarked), and
building ``main.app`` imports the whole service. So this is the strongest static form: like ``test_mutating_routes_declare_auth.py``
it reads the routers with ``ast`` and imports nothing.

THE RULE. For every ``@router.get`` handler in ``routers/*.py``, follow the functions it calls (same module and imported zoe-data
modules, a few levels deep). Every reached function whose SQL reads an owned table (``FROM``/``JOIN``; the tables are read from
``VISIBILITY_MATRIX.md``, so a table added there is covered here) must scope THAT query in the same function: a SQL predicate on
``*user_id = / IN`` or ``visibility = / IN`` in THAT query (its expression plus the variables it names), or a visibility helper. The handler must also declare a principal
dependency, or there is no member to scope by. ``UNSCOPED_BASELINE`` is the reviewed ratchet of exceptions (one today).

NOT COVERED (stated, not hidden): memories (MemPalace, not SQL), and whether the id bound to the predicate is the CALLER's - the
per-feature behaviour tests own that.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[1]

# SQL-fragment helpers that apply the visibility rule inside the query that uses them.
FRAGMENT_HELPERS = frozenset({"_visibility_filter_sql", "_read_filter_sql", "_visibility_sql", "_owner_filter_sql"})
# child table -> the parent it inherits visibility from (VISIBILITY_MATRIX.md "Inherits list").
INHERITS = {"list_items": "lists"}
# Helpers that GATE the whole function (404/403 unless the caller may see the parent row) rather than return a SQL fragment.
GATE_HELPERS = frozenset({"_list_visible_to"})
# Dependencies that establish who is calling (a superset of the GET-relevant ones in test_mutating_routes_declare_auth.py).
PRINCIPAL_DEPS = frozenset({"get_current_user", "require_admin", "require_member", "require_member_or_panel", "require_signed_in",
                            "resolve_acting_user", "_require_voice_auth", "_require_member", "_require_member_session",
                            "_require_caller", "require_member_panel_or_jukebox", "require_internal_token", "_require_owner",
                            "_require_device", "_member", "_require_card"})
SCOPE = re.compile(r"\b\w*user_id\s*(=|IN\b)|\bvisibility\s*(=|IN\b)", re.I)
MAX_DEPTH = 4

# (router module, handler) -> why it reads an owned table without a scoped query. A RATCHET: shrink it, never grow it unreviewed.
UNSCOPED_BASELINE: dict[tuple[str, str], str] = {
    ("routers.lists", "list_lists"): "_collapse_duplicate_lists: COUNT(*) of list_items for family lists the caller already "
                                     "sees (the rows passed in were visibility-filtered); no item text is returned",
}


def owned_tables(matrix: str) -> list[str]:
    """Tables in VISIBILITY_MATRIX.md whose default visibility is ``family``, ``personal`` or inherited (``users`` is N/A)."""
    return re.findall(r"^\|\s*\*\*(\w+)\*\*\s*\|\s*(?:`family`|`personal`|Inherits)", matrix, re.M)


def _imports(tree: ast.AST) -> dict[str, tuple[str, str | None]]:
    out: dict[str, tuple[str, str | None]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                out[a.asname or a.name] = (n.module, a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name] = (a.name, None)
    return out


def _call_name(c: ast.Call) -> str:
    return c.func.id if isinstance(c.func, ast.Name) else c.func.attr if isinstance(c.func, ast.Attribute) else ""


def unscoped_in(fn: ast.AST, read: re.Pattern) -> list[str]:
    """Owned tables ``fn`` reads in a query that is not itself scoped. A query is the whole expression its SQL literal sits in,
    plus every value assigned to a variable that expression names (``sql += " AND owner_user_id = ?"``, ``where = f"{helper()}"``):
    scoped when that group has a predicate or a fragment helper call. A GATE helper anywhere in the function clears it too."""
    if {_call_name(c) for c in ast.walk(fn) if isinstance(c, ast.Call)} & GATE_HELPERS:
        return []
    parent = {c: p for p in ast.walk(fn) for c in ast.iter_child_nodes(p)}
    assigned: dict[str, list[ast.AST]] = {}
    for n in ast.walk(fn):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign)) else []
        for t in targets:
            if isinstance(t, ast.Name) and n.value is not None:
                assigned.setdefault(t.id, []).append(n.value)

    def group(root: ast.AST, seen: set) -> list[ast.AST]:
        out = [root]
        for n in ast.walk(root):
            if isinstance(n, ast.Name) and n.id in assigned and n.id not in seen:
                seen.add(n.id)
                out += [x for v in assigned[n.id] for x in group(v, seen)]
        return out

    bad: set[str] = set()
    scoped: set[str] = set()
    for lit in ast.walk(fn):
        if not (isinstance(lit, ast.Constant) and isinstance(lit.value, str) and read.search(lit.value)):
            continue
        root = lit
        while isinstance(parent.get(root), ast.expr):
            root = parent[root]
        if isinstance(parent.get(root), (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            tgt = parent[root]
            for t in (tgt.targets if isinstance(tgt, ast.Assign) else [tgt.target]):
                if isinstance(t, ast.Name):
                    root = ast.Tuple(elts=[root, ast.Name(id=t.id)])
        nodes = [x for r in group(root, set()) for x in ast.walk(r)]
        strs = [x.value for x in nodes if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        calls = {_call_name(x) for x in nodes if isinstance(x, ast.Call)}
        found = {t.lower() for t in read.findall(lit.value)}
        if (calls & FRAGMENT_HELPERS) or any(SCOPE.search(x) for x in strs):
            scoped |= found
        else:
            bad |= found
    # a child table whose matrix row "inherits" its parent is gated by a scoped read of that parent in the same function
    return sorted(t for t in bad if INHERITS.get(t) not in scoped)


class Sweep:
    def __init__(self, sources: dict[str, str], tables: list[str]):
        trees = {m: ast.parse(s) for m, s in sources.items()}
        self.funcs = {m: {n.name: n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                      for m, t in trees.items()}
        self.imps = {m: _imports(t) for m, t in trees.items()}
        self.trees = trees
        self.read = re.compile(r"\b(?:FROM|JOIN)\s+(" + "|".join(tables) + r")\b", re.I)

    def _resolve(self, mod: str, call: ast.Call):
        f = call.func
        if isinstance(f, ast.Name):
            if f.id in self.funcs[mod]:
                return mod, f.id
            m2, attr = self.imps[mod].get(f.id, ("", None))
            return (m2, attr) if attr and attr in self.funcs.get(m2, {}) else None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in self.imps[mod]:
            m2, attr = self.imps[mod][f.value.id]
            m2 = m2 if attr is None else f"{m2}.{attr}"
            return (m2, f.attr) if f.attr in self.funcs.get(m2, {}) else None
        return None

    def reach(self, mod: str, name: str, depth: int = 0, seen=None) -> list[tuple[str, str]]:
        seen = set() if seen is None else seen
        if (mod, name) in seen or depth > MAX_DEPTH:
            return []
        seen.add((mod, name))
        out = [(mod, name)]
        for c in ast.walk(self.funcs[mod][name]):
            if isinstance(c, ast.Call) and (target := self._resolve(mod, c)):
                out += self.reach(*target, depth + 1, seen)
        return out

    def unscoped_reads(self, mod: str, name: str) -> list[str]:
        """``"<module>.<function>: <tables>"`` for every reached function with an owned-table read whose OWN query is unscoped."""
        bad = []
        for m, fn in self.reach(mod, name):
            tables = unscoped_in(self.funcs[m][fn], self.read)
            if tables:
                bad.append(f"{m}.{fn}: {','.join(tables)}")
        return bad

    def get_routes(self, mod: str):
        """``(handler, declares a principal)`` for each ``@<router>.get`` handler in ``mod``."""
        router_deps = any(_call_name(c) in ("Depends", "Security") and c.args
                          and getattr(c.args[0], "id", getattr(c.args[0], "attr", "")) in PRINCIPAL_DEPS
                          for n in ast.walk(self.trees[mod]) if isinstance(n, ast.Call) and _call_name(n) == "APIRouter"
                          for c in ast.walk(n) if isinstance(c, ast.Call))
        for fn in self.funcs[mod].values():
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "get"
                       for d in fn.decorator_list):
                continue
            deps = {getattr(c.args[0], "id", getattr(c.args[0], "attr", "")) for c in ast.walk(fn)
                    if isinstance(c, ast.Call) and _call_name(c) in ("Depends", "Security") and c.args}
            yield fn.name, router_deps or bool(deps & PRINCIPAL_DEPS)


def _service_sources() -> dict[str, str]:
    files = list(ROOT.glob("*.py")) + list((ROOT / "routers").glob("*.py"))
    return {(p.stem if p.parent == ROOT else f"routers.{p.stem}"): p.read_text(encoding="utf-8") for p in files}


def sweep_findings(sweep: Sweep) -> tuple[dict[tuple[str, str], str], int, int]:
    findings, n_get, n_owned = {}, 0, 0
    for mod in sorted(m for m in sweep.funcs if m.startswith("routers.")):
        for handler, has_principal in sweep.get_routes(mod):
            n_get += 1
            reached = [(m, f) for m, f in sweep.reach(mod, handler)]
            reads = any(sweep.read.search(n.value) for m, f in reached for n in ast.walk(sweep.funcs[m][f])
                        if isinstance(n, ast.Constant) and isinstance(n.value, str))
            if not reads:
                continue
            n_owned += 1
            problems = sweep.unscoped_reads(mod, handler) + ([] if has_principal else ["declares no principal dependency"])
            if problems:
                findings[(mod, handler)] = "; ".join(problems)
    return findings, n_get, n_owned


@pytest.fixture(scope="module")
def real_sweep():
    tables = owned_tables((ROOT / "VISIBILITY_MATRIX.md").read_text(encoding="utf-8"))
    return tables, sweep_findings(Sweep(_service_sources(), tables))


def test_matrix_names_the_owned_tables(real_sweep):
    tables, _ = real_sweep
    assert {"events", "lists", "list_items", "people", "reminders", "notes", "journal_entries", "transactions"} <= set(tables)


def test_every_get_route_reading_an_owned_table_is_scoped(real_sweep):
    _, (findings, n_get, n_owned) = real_sweep
    # The scan must stay meaningful: if the router layout changes so it sees nothing, it fails instead of passing vacuously.
    assert n_get >= 150 and n_owned >= 30, (n_get, n_owned)
    new = {k: v for k, v in findings.items() if k not in UNSCOPED_BASELINE}
    assert not new, "GET route(s) read an owned table without a visibility/owner predicate (or declare no principal):\n  " + \
        "\n  ".join(f"{m}.{h}: {why}" for (m, h), why in sorted(new.items()))
    fixed = sorted(set(UNSCOPED_BASELINE) - set(findings))
    assert not fixed, f"now scoped - drop from UNSCOPED_BASELINE: {fixed}"


# -- negative controls: the same scanner over synthetic modules ---------------------------------------------------------------
_HELPER = '''
async def fetch_note(db, note_id):
    return await db.execute("SELECT * FROM notes WHERE id = ?", [note_id])
'''
_ROUTER = '''
from fastapi import APIRouter, Depends
from auth import get_current_user
import note_helper
router = APIRouter(prefix="/api/x")

@router.get("/a/{i}")
async def unscoped(i: str, user: dict = Depends(get_current_user), db=None):
    return await db.execute("SELECT title FROM notes WHERE id = ?", [i])

@router.get("/b/{i}")
async def via_helper(i: str, user: dict = Depends(get_current_user), db=None):
    return await note_helper.fetch_note(db, i)

@router.get("/c")
async def scoped(user: dict = Depends(get_current_user), db=None):
    return await db.execute("SELECT * FROM notes WHERE " + _visibility_filter_sql(), [user["user_id"]])

@router.get("/d")
async def owner_only(user: dict = Depends(get_current_user), db=None):
    return await db.execute("SELECT * FROM journal_entries WHERE user_id = ? AND deleted = 0", [user["user_id"]])

@router.get("/e")
async def no_principal(db=None):
    return await db.execute("SELECT * FROM reminders WHERE user_id = ?", ["x"])

@router.get("/g")
async def mixed(user: dict = Depends(get_current_user), db=None):
    await db.execute("SELECT COUNT(*) FROM notes WHERE user_id = ?", [user["user_id"]])
    return await db.execute("SELECT * FROM notes")

@router.get("/h/{i}")
async def gated_child(i: str, user: dict = Depends(get_current_user), db=None):
    await db.execute("SELECT id FROM lists WHERE id = ? AND (visibility = 'family' OR user_id = ?)", [i, user["user_id"]])
    return await db.execute("SELECT * FROM list_items WHERE list_id = ?", [i])

@router.get("/i/{i}")
async def ungated_child(i: str, user: dict = Depends(get_current_user), db=None):
    return await db.execute("SELECT * FROM list_items WHERE list_id = ?", [i])

@router.get("/f")
async def not_owned(user: dict = Depends(get_current_user), db=None):
    return await db.execute("SELECT * FROM weather_cache")

def _visibility_filter_sql():
    return "(visibility = 'family' OR user_id = ?) AND deleted = 0"
'''


def test_negative_control_unscoped_reads_and_missing_principal_are_caught():
    findings, n_get, n_owned = sweep_findings(Sweep({"routers.x": _ROUTER, "note_helper": _HELPER}, ["notes", "journal_entries",
                                                                                                     "reminders", "lists", "list_items"]))
    assert (n_get, n_owned) == (9, 8)
    assert set(findings) == {("routers.x", "unscoped"), ("routers.x", "via_helper"), ("routers.x", "no_principal"),
                             ("routers.x", "mixed"), ("routers.x", "ungated_child")}     # a scoped query beside it does not clear the unscoped one
    assert findings[("routers.x", "via_helper")] == "note_helper.fetch_note: notes"     # followed across modules
    assert findings[("routers.x", "no_principal")] == "declares no principal dependency"


def test_negative_control_a_new_unscoped_route_on_the_real_tree_fails(real_sweep):
    tables, _ = real_sweep
    sources = _service_sources()
    sources["routers.notes"] += ('\n\n@router.get("/leak/{note_id}")\nasync def leak(note_id: str, user: dict = '
                                 'Depends(get_current_user), db=Depends(get_db)):\n'
                                 '    return await db.execute("SELECT * FROM notes WHERE id = ?", [note_id])\n')
    findings, _, _ = sweep_findings(Sweep(sources, tables))
    assert ("routers.notes", "leak") in findings
