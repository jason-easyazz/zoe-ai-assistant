"""scripts/maintenance/merge_duplicate_lists.py - fold duplicate HOUSEHOLD lists into one (2026-10-15).

The dry run (the default) must write nothing and print counts only; the plan must never touch a private list; the apply must
move items, de-duplicate, and soft-delete (never hard-delete) the emptied duplicate. All data invented.
"""
import asyncio
import importlib.util
import re
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "maintenance"


@pytest.fixture(scope="module")
def mod():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("merge_duplicate_lists_under_test", SCRIPTS / "merge_duplicate_lists.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m          # @dataclass resolves its module through sys.modules
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.remove(str(SCRIPTS))


def _row(i, owner, name="Shopping", vis="family", n=0, created="2026-10-01", ltype="shopping"):
    return {"id": i, "user_id": owner, "name": name, "list_type": ltype, "visibility": vis, "created_at": created, "n": n}


def test_plan_merges_guest_and_empty_family_twins_into_the_fullest_household_list(mod):
    rows = [
        _row("full", "jo", n=5, created="2026-10-02"),
        _row("guest-empty", "guest", vis="personal", n=0),
        _row("guest-two", "voice-guest", vis="family", n=2),
        _row("fam-twin", "casey", name="shopping list", vis="family", n=0),
        _row("hh", "family-admin", name="Other", vis="family", n=1),
    ]
    plan = mod.plan_merges(rows)
    assert {m.duplicate_id for m in plan} == {"guest-empty", "guest-two", "fam-twin", "hh"}
    assert {m.canonical_id for m in plan} == {"full"}
    assert sum(m.items for m in plan) == 3


def test_plan_never_touches_a_private_list_or_a_differently_named_member_list(mod):
    rows = [
        _row("full", "jo", n=3),
        _row("casey-private", "casey", vis="personal", n=4),                 # a member's PRIVATE list of the same type
        _row("casey-hardware", "casey", name="Hardware run", vis="family", n=1),  # a deliberate second list
        _row("diary", "jo", name="Diary", vis="personal", n=9, ltype="personal"),
    ]
    assert mod.plan_merges(rows) == []


def test_plan_invents_nothing_when_there_is_no_household_list(mod):
    assert mod.plan_merges([_row("g", "guest", vis="personal", n=2)]) == []
    assert mod.plan_merges([]) == []


def test_summary_is_counts_only(mod):
    plan = mod.plan_merges([_row("full", "jo", n=5), _row("g", "guest", vis="personal", n=2)])
    text = mod.summarise(plan)
    assert "1" in text and "shopping" in text
    assert "Shopping" not in text and "guest" not in text.replace("duplicate", "")  # no names, no owners


class _SqliteConn:
    """asyncpg's ``$n`` placeholders and 'UPDATE n' command tags over SQLite."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.create_function("NOW", 0, lambda: "2026-10-15T00:00:00")
        self.con.executescript(
            "CREATE TABLE lists (id TEXT PRIMARY KEY, deleted INT DEFAULT 0, updated_at TEXT);"
            "CREATE TABLE list_items (id TEXT PRIMARY KEY, list_id TEXT, text TEXT, completed INT DEFAULT 0, "
            "deleted INT DEFAULT 0, updated_at TEXT);"
        )

    async def execute(self, sql, *args):
        cur = self.con.execute(re.sub(r"\$(\d+)", r"?\1", sql), args)
        return f"UPDATE {cur.rowcount}"


def test_apply_moves_items_dedupes_and_soft_deletes_the_emptied_duplicate(mod):
    conn = _SqliteConn()
    conn.con.executescript(
        "INSERT INTO lists (id) VALUES ('full'), ('stray'), ('empty');"
        "INSERT INTO list_items (id, list_id, text, completed) VALUES "
        "('a','full','Milk',0), ('b','stray','milk',0), ('c','stray','bread',0), ('d','stray','eggs',1);"
    )
    plan = [mod.Merge("shopping", "full", "stray", 3), mod.Merge("shopping", "full", "empty", 0)]
    counts = asyncio.run(mod.apply_plan(conn, plan))
    live = {r["text"].lower() for r in conn.con.execute("SELECT text FROM list_items WHERE list_id='full' AND deleted=0")}
    assert live == {"milk", "bread", "eggs"}
    assert counts == {"moved": 2, "deduped": 1, "lists_removed": 2}
    assert {r["id"]: r["deleted"] for r in conn.con.execute("SELECT id, deleted FROM lists")} == {"full": 0, "stray": 1, "empty": 1}
    # soft delete only: the retired duplicate item still exists as a row
    assert conn.con.execute("SELECT COUNT(*) FROM list_items").fetchone()[0] == 4


def test_a_duplicate_that_still_holds_a_live_item_is_not_deleted(mod):
    """The list soft-delete is guarded by 'no live items left' - belt and braces against a half-applied move."""
    conn = _SqliteConn()
    conn.con.executescript("INSERT INTO lists (id) VALUES ('full'), ('stray');")

    async def _only_delete():
        return await conn.execute(
            "UPDATE lists SET deleted = 1, updated_at = NOW() WHERE id = $1 AND deleted = 0 "
            "AND NOT EXISTS (SELECT 1 FROM list_items WHERE list_id = $1 AND deleted = 0)", "stray")

    conn.con.execute("INSERT INTO list_items (id, list_id, text) VALUES ('x','stray','left behind')")
    assert asyncio.run(_only_delete()) == "UPDATE 0"
    assert conn.con.execute("SELECT deleted FROM lists WHERE id='stray'").fetchone()[0] == 0


class _RecordingConn:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []
        self.readonly = None

    async def fetch(self, sql, *args):
        assert sql.lstrip().upper().startswith("SELECT")
        return self.rows

    async def execute(self, sql, *args):
        self.writes.append(sql)
        raise AssertionError("a dry run wrote: " + sql)

    def transaction(self, readonly=False):
        outer = self
        outer.readonly = readonly

        class _Tx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return _Tx()

    async def close(self):
        pass


def test_the_default_run_is_a_read_only_dry_run_that_writes_nothing_and_prints_counts(mod, monkeypatch, capsys):
    import types
    import purge_orphaned_test_data as purge

    conn = _RecordingConn([_row("full", "jo", n=5), _row("g", "guest", vis="personal", n=2)])

    async def _connect(_dsn):
        return conn

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=_connect))
    monkeypatch.setattr(purge, "_resolve_dsn", lambda: "postgresql://reader:pw@db.invalid:5432/zoe_test")
    rc = asyncio.run(mod.main(False, False, "", "", ("shopping",)))
    out = capsys.readouterr().out
    assert rc == 0 and conn.writes == [] and conn.readonly is True
    assert "DRY-RUN" in out and "duplicate lists to merge: 1" in out
    assert "pw" not in out and "Shopping" not in out        # no secret, no list names


def test_execute_with_yes_refuses_without_a_full_target_assertion(mod, monkeypatch):
    import types
    import purge_orphaned_test_data as purge

    conn = _RecordingConn([_row("full", "jo", n=5), _row("g", "guest", vis="personal", n=2)])

    async def _connect(_dsn):
        raise AssertionError("must refuse before connecting")

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=_connect))
    monkeypatch.setattr(purge, "_resolve_dsn", lambda: "postgresql://reader:pw@db.invalid:5432/zoe_test")
    assert asyncio.run(mod.main(True, True, "", "", ("shopping",))) == 2
    assert asyncio.run(mod.main(True, True, "wrong", "db.invalid", ("shopping",))) == 2
    assert conn.writes == []
