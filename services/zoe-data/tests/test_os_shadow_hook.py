"""The list-add hook: every live list writer calls os_shadow.note_add AFTER its commit, and the live add is never changed by it.

Fake DB + the same in-process fake kernel as test_os_shadow.py. Companion pins: a static scan that a NEW list-item writer must either
call the hook or be named (with a reason) below.
"""
from __future__ import annotations

import ast
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


# ---- structure pins -----------------------------------------------------------------------------------------------------------------
def _writers(src: str) -> bool:
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "INSERT INTO list_items" in n.value:
            return True
        if isinstance(n, ast.Call) and (getattr(n.func, "id", "") == "create_item_record" or getattr(n.func, "attr", "") == "create_item_record"):
            return True
    return False


def _hooks(src: str) -> bool:
    return any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "note_add" and getattr(n.func.value, "id", "") == "os_shadow"
               for n in ast.walk(ast.parse(src)))


#: modules that write a list item and are NOT shadowed, each with the reason; a new writer must join one list or the other
NOT_SHADOWED = {
    "pending_suggestions.py": "Zoe's own offer, accepted by the member: not a spoken add; shadowing it is a later, separate decision",
    "anticipation.py": "the packing list Zoe starts for a trip: many items from one offer, no spoken add to compare",
}
SHADOWED = {"list_service.py", "skybridge_service.py", "routers/lists.py"}


def _service_files():
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return [p for p in SVC.rglob("*.py") if not skip.intersection(p.relative_to(SVC).parts)]


def test_every_list_item_writer_is_shadowed_or_named_with_a_reason():
    found = {p.relative_to(SVC).as_posix() for p in _service_files() if _writers(p.read_text("utf-8"))}
    assert found == SHADOWED | set(NOT_SHADOWED), f"a list-item writer is neither shadowed nor excused: {found ^ (SHADOWED | set(NOT_SHADOWED))}"
    for rel in SHADOWED:
        assert _hooks((SVC / rel).read_text("utf-8")), f"{rel} writes list items but never calls os_shadow.note_add"


def test_the_writer_scan_catches_a_new_writer():
    assert _writers("async def f(db):\n    await db.execute('INSERT INTO list_items (id) VALUES (1)')")
    assert _writers("async def f(db):\n    await list_service.create_item_record(db, list_id='x', text='y')")
    assert not _writers("def f():\n    return 1")
    assert not _hooks("def f():\n    return 1") and _hooks("def f():\n    os_shadow.note_add(1)")


