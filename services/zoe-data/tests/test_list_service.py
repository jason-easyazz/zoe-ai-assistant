"""Unit tests for the single canonical list writer.

``list_service`` owns the one INSERT site each for ``lists`` and ``list_items``
shared by the Wave-3 trio (voice/direct executor, the list_add_item MCP tool,
and the /api/lists router). These tests pin the exact column lists, the value
mapping, the 'normal' priority default the narrower voice INSERTs relied on,
the family-first list resolution, the retry-dedup window, and — the bug this
module fixes — that a fresh list and its first item land in ONE transaction so
an induced item-insert failure cannot orphan an empty list.
"""

import re

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

from list_service import (
    add_item_to_list,
    add_items_to_list,
    item_key,
    create_item_record,
    create_list_record,
    default_visibility,
)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _FakeDB:
    """Transactional fake mirroring AsyncpgCompat's surface.

    SELECT results are served from ``select_results`` (one list of rows per
    SELECT, in order). Writes go straight to ``committed`` outside a
    transaction; inside ``db.transaction()`` they buffer and only land in
    ``committed`` on clean exit — an exception discards the buffer, simulating
    asyncpg's rollback.
    """

    def __init__(self, select_results=None, fail_on=None):
        self.select_results = list(select_results or [])
        self.fail_on = fail_on
        self.committed = []
        self.selects = []
        self.txn_entered = 0
        self.locks = []
        self._txn_buffer = None

    async def execute(self, sql, params=()):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("induced write failure")
        if "pg_advisory_xact_lock" in sql:
            self.locks.append(tuple(params))
            return _FakeCursor([])
        if sql.lstrip().upper().startswith("SELECT"):
            self.selects.append((sql, tuple(params)))
            rows = self.select_results.pop(0) if self.select_results else []
            return _FakeCursor(rows)
        target = self._txn_buffer if self._txn_buffer is not None else self.committed
        target.append((sql, tuple(params)))
        return _FakeCursor([])

    def transaction(self):
        db = self

        class _Txn:
            async def __aenter__(self):
                db.txn_entered += 1
                db._depth = getattr(db, "_depth", 0) + 1
                if db._txn_buffer is None:
                    db._txn_buffer = []
                return self

            async def __aexit__(self, exc_type, exc, tb):
                # nested transactions are savepoints: only the outermost exit commits (an exception anywhere unwinds to it)
                db._depth -= 1
                if db._depth == 0:
                    buf, db._txn_buffer = db._txn_buffer, None
                    if exc_type is None:
                        db.committed.extend(buf)
                return False

        return _Txn()


class _NoTxnFakeDB(_FakeDB):
    transaction = None  # not callable → service takes the shim fallback path


def _columns(sql: str, table: str):
    m = re.search(rf"INSERT INTO {table}\s*\((.*?)\)\s*VALUES", sql, re.DOTALL)
    assert m, f"could not parse columns from: {sql!r}"
    return [c.strip() for c in m.group(1).split(",")]


def _writes(db, table: str):
    return [(sql, params) for sql, params in db.committed if f"INSERT INTO {table}" in sql]


def test_default_visibility_rule():
    # ONE household shopping list: auto-created family-visible, never as the first speaker's personal list (household-lists fix).
    assert default_visibility("shopping") == "family"
    assert default_visibility("tasks") == "personal"
    assert default_visibility("personal") == "personal"
    assert default_visibility("work") == "family"
    assert default_visibility("bucket") == "family"


@pytest.mark.asyncio
async def test_create_list_record_columns_and_mapping():
    db = _FakeDB()
    record = await create_list_record(
        db,
        user_id="u1",
        name="Groceries",
        list_type="shopping",
        description="weekly run",
        visibility="personal",
    )

    assert len(db.committed) == 1
    sql, params = db.committed[0]
    assert _columns(sql, "lists") == [
        "id", "user_id", "name", "list_type", "description", "visibility",
    ]
    assert params == (record["id"], "u1", "Groceries", "shopping", "weekly run", "personal")
    assert isinstance(record["id"], str) and len(record["id"]) == 36
    assert record["visibility"] == "personal"


@pytest.mark.asyncio
async def test_create_item_record_columns_and_priority_default():
    db = _FakeDB()
    record = await create_item_record(db, list_id="L1", text="milk")

    assert len(db.committed) == 1
    sql, params = db.committed[0]
    assert _columns(sql, "list_items") == [
        "id", "list_id", "text", "priority", "category", "quantity",
        "parent_id", "assigned_to",
    ]
    # priority explicitly 'normal' — the DB default the narrower voice/MCP
    # INSERTs relied on by omitting the column; everything else NULL.
    assert params == (record["id"], "L1", "milk", "normal", None, None, None, None)


@pytest.mark.asyncio
async def test_create_item_record_router_superset():
    db = _FakeDB()
    record = await create_item_record(
        db,
        list_id="L1",
        text="milk",
        priority="high",
        category="dairy",
        quantity="2",
        parent_id="P1",
        assigned_to="u2",
    )
    _sql, params = db.committed[0]
    assert params == (record["id"], "L1", "milk", "high", "dairy", "2", "P1", "u2")


@pytest.mark.asyncio
async def test_add_item_existing_list_inserts_item_only():
    db = _FakeDB(select_results=[[{"id": "L1"}]])
    outcome = await add_item_to_list(
        db,
        user_id="u1",
        list_type="shopping",
        list_name="Shopping",
        text="milk",
        quantity="2",
        category="dairy",
        new_list_visibility=default_visibility("shopping"),
    )

    assert outcome == {
        "list_id": "L1", "item_id": outcome["item_id"],
        "created_list": False, "deduped": False,
    }
    assert _writes(db, "lists") == []
    (sql, params), = _writes(db, "list_items")
    assert params[1:] == ("L1", "milk", "normal", "dairy", "2", None, None)
    # Household-first resolution BY TYPE: the family-visible list of the type, whatever it is called (name is a tie-break).
    resolve_sql, resolve_params = db.selects[0]
    assert "visibility = 'family'" in resolve_sql and "l.list_type = ?" in resolve_sql
    assert resolve_params == ("shopping",)


@pytest.mark.asyncio
async def test_dedup_hit_returns_existing_item_and_writes_nothing():
    db = _FakeDB(select_results=[[{"id": "L1"}], [{"id": "existing-item"}]])
    outcome = await add_item_to_list(
        db,
        user_id="u1",
        list_type="shopping",
        list_name="Shopping",
        text="Milk",
        new_list_visibility="personal",
        dedup_window_s=10,
    )

    assert outcome == {
        "list_id": "L1", "item_id": "existing-item",
        "created_list": False, "deduped": True,
    }
    assert db.committed == []
    dup_sql, dup_params = db.selects[1]
    # The TEXT created_at column must be cast before the timestamp comparison
    # (Postgres: `text > timestamp` has no operator), and the caller's window
    # must reach the interval.
    assert "created_at::timestamptz" in dup_sql
    assert "interval '10 seconds'" in dup_sql
    assert dup_params == ("L1", "Milk")


@pytest.mark.asyncio
async def test_dedup_window_zero_skips_the_probe():
    db = _FakeDB(select_results=[[{"id": "L1"}]])
    outcome = await add_item_to_list(
        db,
        user_id="u1",
        list_type="shopping",
        list_name="Shopping",
        text="milk",
        new_list_visibility="personal",
        dedup_window_s=0,
    )
    assert outcome["deduped"] is False
    # resolve + the open-items probe; NO replay-window probe
    assert len(db.selects) == 2
    assert not any('created_at::timestamptz' in q for q, _ in db.selects)
    assert len(_writes(db, "list_items")) == 1


@pytest.mark.asyncio
async def test_new_list_and_item_land_in_one_transaction():
    db = _FakeDB(select_results=[[]])  # list does not exist
    outcome = await add_item_to_list(
        db,
        user_id="u1",
        list_type="work",
        list_name="Work",
        text="ship PR",
        new_list_visibility=default_visibility("work"),
    )

    assert outcome["created_list"] is True
    assert db.txn_entered == 1
    (list_sql, list_params), = _writes(db, "lists")
    (item_sql, item_params), = _writes(db, "list_items")
    assert list_params == (outcome["list_id"], "u1", "Work", "work", None, "family")
    assert item_params[:3] == (outcome["item_id"], outcome["list_id"], "ship PR")


@pytest.mark.asyncio
async def test_induced_item_failure_leaves_no_orphaned_list():
    """The MCP-path bug this module fixes: list INSERT then a failing item
    INSERT must roll back together — never an orphaned empty list."""
    db = _FakeDB(select_results=[[]], fail_on="INSERT INTO list_items")
    with pytest.raises(RuntimeError, match="induced write failure"):
        await add_item_to_list(
            db,
            user_id="u1",
            list_type="shopping",
            list_name="Shopping",
            text="milk",
            new_list_visibility="personal",
        )

    assert db.txn_entered == 1
    assert _writes(db, "lists") == []  # rolled back with the failed item
    assert db.committed == []


@pytest.mark.asyncio
async def test_shim_without_transaction_support_still_writes():
    db = _NoTxnFakeDB(select_results=[[]])
    outcome = await add_item_to_list(
        db,
        user_id="u1",
        list_type="shopping",
        list_name="Shopping",
        text="milk",
        new_list_visibility="personal",
    )
    assert outcome["created_list"] is True
    assert len(_writes(db, "lists")) == 1
    assert len(_writes(db, "list_items")) == 1


@pytest.mark.asyncio
async def test_existing_list_add_holds_the_per_list_advisory_lock_inside_a_transaction():
    """Overlapping adds serialise on the check-then-insert: the lock is taken inside a txn, before the open-items read."""
    db = _FakeDB(select_results=[[{"id": "L1"}], []])
    await add_item_to_list(db, user_id="u1", list_type="shopping", list_name="Shopping", text="milk", new_list_visibility="family")
    assert db.txn_entered == 1
    assert db.locks == [("list_add|L1",)]


@pytest.mark.asyncio
async def test_multi_item_add_is_all_or_nothing_and_records_no_receipts_on_failure(monkeypatch):
    import list_service

    recorded = []
    monkeypatch.setattr(list_service.write_receipts, "record", lambda *a, **k: recorded.append(a))
    db = _FakeDB(select_results=[[{"id": "L1"}], [], [{"id": "L1"}], []])
    orig = db.execute
    state = {"n": 0}

    async def flaky(sql, params=()):
        if "INSERT INTO list_items" in sql:
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("induced write failure")
        return await orig(sql, params)

    db.execute = flaky
    with pytest.raises(RuntimeError):
        await add_items_to_list(db, text="eggs and milk", user_id="u1", list_type="shopping", list_name="Shopping",
                                new_list_visibility="family")
    assert _writes(db, "list_items") == []
    assert recorded == []


@pytest.mark.asyncio
async def test_multi_item_add_records_receipts_only_after_commit(monkeypatch):
    import list_service

    recorded = []
    monkeypatch.setattr(list_service.write_receipts, "record", lambda *a, **k: recorded.append(a))
    db = _FakeDB(select_results=[[{"id": "L1"}], [], [{"id": "L1"}], []])
    out = await add_items_to_list(db, text="eggs and milk", user_id="u1", list_type="shopping", list_name="Shopping",
                                  new_list_visibility="family")
    assert len(out["added"]) == 2 and len(recorded) == 2
    assert len(_writes(db, "list_items")) == 2


@pytest.mark.parametrize("a,b", [("cookies", "cookie"), ("brownies", "brownie"), ("berries", "berry"), ("tomatoes", "tomato"),
                                 ("smoothies", "smoothie")])
def test_item_key_plural_matches_singular_both_directions(a, b):
    assert item_key(a) == item_key(b)
    assert item_key(f"chocolate {a}") == item_key(f"Chocolate {b}")


def test_turn_continuation_ttl_uses_typed_env_and_clamps(monkeypatch):
    import conversation_cues

    monkeypatch.setenv("ZOE_TURN_CONTINUATION_TTL_S", "banana")
    assert conversation_cues.turn_continuation_ttl_s() == 60.0
    monkeypatch.setenv("ZOE_TURN_CONTINUATION_TTL_S", "-5")
    assert conversation_cues.turn_continuation_ttl_s() == 0.0
    monkeypatch.setenv("ZOE_TURN_CONTINUATION_TTL_S", "12")
    assert conversation_cues.turn_continuation_ttl_s() == 12.0
