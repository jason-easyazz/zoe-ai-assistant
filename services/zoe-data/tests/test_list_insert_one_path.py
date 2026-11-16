"""WP-20: existing caller contracts plus a guard against a second item writer.

The caller tests use only pre-WP-20 APIs and expectations taken from the
original INSERTs/checks; they can be copied onto the parent revision unchanged.
"""
import ast
import logging
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import list_service
from test_list_service import _FakeDB, _writes

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]


def _function(path, name, **namespace):
    """Load the actual caller without booting unrelated service dependencies."""
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    namespace.update(uuid=uuid, Any=object)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / path), "exec"), namespace)
    return namespace[name]


class NativeDB:
    def __init__(self, *, recent=None, opened=(), list_row=None):
        self.recent, self.opened, self.list_row = recent, list(opened), list_row
        self.writes = []

    async def fetch(self, sql, *args):
        return list(self.opened)

    async def fetchrow(self, sql, *args):
        return self.recent if "FROM list_items" in sql else self.list_row

    async def execute(self, sql, *args):
        self.writes.append((sql, args))


def _row(write):
    sql, values = write
    columns = re.search(r"list_items\s*\((.*?)\)", sql, re.S).group(1).split(",")
    return dict(zip((c.strip() for c in columns), values))


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["new", "recent", "open"])
async def test_spoken_add_original_contract(monkeypatch, state):
    receipts, audit, recent = [], [], []
    monkeypatch.setattr(list_service, "note_recent_add", lambda *a: recent.append(a))
    db = NativeDB(recent={"id": "I0"} if state == "recent" else None,
                  opened=[{"id": "I0", "text": "milk"}] if state == "open" else [])
    commit = AsyncMock()
    call = _function("skybridge_service.py", "_add_spoken_items",
                     _maybe_commit=commit, _audit_list_item=lambda *a: audit.append(a),
                     write_receipts=SimpleNamespace(record=lambda *a: receipts.append(a)),
                     panel_principal=SimpleNamespace(HOUSEHOLD_VIEWER="__household__"),
                     logger=logging.getLogger("wp20"))
    added, already, ids, item_id = await call(db, "u1", "L1", "shopping", "Shopping", "milk")
    if state == "new":
        assert _row(db.writes[0]) == dict(id=item_id, list_id="L1", text="milk", priority="normal",
                                         category="", quantity="", parent_id=None, assigned_to=None)
    else:
        assert db.writes == []
        assert item_id == "I0"
    assert (added, already, ids) == (([], ["milk"], []) if state == "open" else (["milk"], [], [item_id]))
    assert receipts == [("u1", "list", item_id, "skybridge:add_item")]
    assert audit == ([] if state == "open" else [("add", "u1", "L1", item_id)])
    assert recent == ([] if state == "open" else [("u1", "L1", item_id)])
    assert commit.await_count == (0 if state == "open" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["new", "recent", "open"])
async def test_service_add_original_contract(monkeypatch, state):
    receipts = []
    monkeypatch.setattr(list_service.write_receipts, "record", lambda *a: receipts.append(a))
    monkeypatch.setattr(list_service, "note_recent_add", lambda *a: None)
    selects = [[{"id": "I0"}]] if state == "recent" else [[], [{"id": "I0", "text": "milk"}] if state == "open" else []]
    db = _FakeDB(select_results=selects)
    out = await list_service.add_item_to_list(db, user_id="u1", list_id="L1", list_type="shopping",
                                            list_name="Shopping", text="milk", quantity="2",
                                            new_list_visibility="family", dedup_window_s=10)
    assert out["deduped"] == (state != "new")
    assert out["created_list"] is False
    assert out.get("already", False) == (state == "open")
    writes = _writes(db, "list_items")
    if state == "new":
        assert writes[0][1] == (out["item_id"], "L1", "milk", "normal", None, "2", None, None)
    else:
        assert writes == [] and out["item_id"] == "I0"
    suffix = {"new": "", "recent": ":dedup", "open": ":already"}[state]
    assert receipts == [("u1", "list", out["item_id"], "add_item_to_list" + suffix)]


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [True, False])
async def test_suggestion_original_contract(monkeypatch, existing):
    receipts = []
    monkeypatch.setattr(list_service.write_receipts, "record", lambda *a: receipts.append(a))
    call = _function("pending_suggestions.py", "_execute_action")
    db = NativeDB(list_row={"id": "L1"} if existing else None)
    # Deliberately includes whitespace/punctuation: suggestions do NOT normalize or dedupe.
    text = "  milk!!  "
    for _ in range(2):
        out = await call(db, "list_add", {"item": text}, "u1")
        assert out == {"item_id": out["item_id"], "list_id": out["list_id"], "text": text}
        assert _row(db.writes[-1]) == dict(id=out["item_id"], list_id=out["list_id"], text=text)
    assert len([w for w in db.writes if "INSERT INTO list_items" in w[0]]) == 2
    assert receipts == []


@pytest.mark.asyncio
async def test_rest_original_contract(monkeypatch):
    from models import ListItemCreate
    from test_router_authz_regressions import ListDB

    receipts = []
    monkeypatch.setattr(list_service.write_receipts, "record", lambda *a: receipts.append(a))
    broadcast, audit = AsyncMock(), []
    call = _function("routers/lists.py", "add_item", ListItemCreate=ListItemCreate,
                     Depends=lambda x: None, get_current_user=None, get_db=None, Request=object,
                     router=SimpleNamespace(post=lambda *a: lambda f: f),
                     _item_actor=AsyncMock(return_value=(False, "u1")),
                     _require_item_feature=AsyncMock(), _require_list_item_mutation_access=AsyncMock(),
                     VALID_LIST_TYPES={"shopping"}, _normalize_list_type=lambda x: x,
                     create_item_record=list_service.create_item_record, _row_to_dict=dict,
                     _audit_item=lambda *a: audit.append(a), broadcaster=SimpleNamespace(broadcast=broadcast))
    db = ListDB("u1")
    for _ in range(2):
        out = await call("shopping", "list-1", ListItemCreate(text="milk", quantity="2"), {"user_id": "u1"}, db)
        assert out["text"] == "milk" and out["list_id"] == "list-1"
        assert audit[-1] == ("add", "u1", "list-1", out["id"])
        assert broadcast.await_args.args == ("lists", "list_updated",
            {"action": "item_added", "list_id": "list-1", "item": out, "actor": "u1"})
    writes = [(s, p) for s, p in db.calls if "INSERT INTO list_items" in s]
    assert len(writes) == 2  # REST does not dedupe
    assert writes[0][1][1:] == ["list-1", "milk", "normal", "", "2", None, None]
    assert db.commits == 2 and receipts == []


def _sql_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _sql_text(node.left) + _sql_text(node.right)
    if isinstance(node, ast.JoinedStr):
        return "".join(_sql_text(v) if isinstance(v, ast.Constant) else " dynamic " for v in node.values)
    return ""


def _insert_sites(root):
    found = set()
    pattern = re.compile(r'\binsert\s+(?:or\s+\w+\s+)?into\s+(?:"?public"?\s*\.\s*)?"?list_items"?\b', re.I)
    for path in root.rglob("*.py"):
        if "tests" in path.relative_to(root).parts:
            continue  # fixture seed SQL is not a production writer
        tree = ast.parse(path.read_text())

        def visit(node, owner=None):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = node.name
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)) and pattern.search(_sql_text(node)):
                found.add((path.relative_to(root).as_posix(), owner))
            for child in ast.iter_child_nodes(node):
                visit(child, owner)
        visit(tree)
    return found


def test_only_one_item_insert_function():
    assert _insert_sites(ROOT) == {("list_service.py", "create_item_record")}


@pytest.mark.parametrize("sql", [
    '"INSERT INTO list_items (id) VALUES ($1)"',
    '"insert\\ninto list_items (id) values ($1)"',
    'f"INSERT INTO list_items ({columns}) VALUES ({values})"',
    '("INSERT " + "INTO list_items (id) VALUES ($1)")',
    '"INSERT INTO public.list_items (id) VALUES ($1)"',
])
@pytest.mark.parametrize("method", ["execute", "executemany"])
def test_scan_negative_control(tmp_path, sql, method):
    (tmp_path / "planted.py").write_text(f"async def rogue(db):\n    await db.{method}({sql}, [])\n")
    assert _insert_sites(tmp_path) == {("planted.py", "rogue")}
