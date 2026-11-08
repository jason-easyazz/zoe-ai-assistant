"""db_pool's NOW() -> NOW()::text rewrite must never break timestamp ARITHMETIC.

QA 2026-11-05: every brain / intent-dispatch list add failed with
``operator does not exist: text - interval``. ``list_service._find_recent_duplicate``
runs ``created_at::timestamptz > now() - interval '10 seconds'`` through the
AsyncpgCompat ``execute`` path, whose SQLite-compat rewrite turned it into
``NOW()::text - interval`` - a type error on Postgres. Every ``_execute_list_add_direct``
passes ``dedup_window_s=10``, so any add to a list that already exists failed, and the
brain told the member to use the household shopping list instead.

The fix is in the rewrite (the class), not the one call site: NOW() as an operand of
``+`` / ``-`` / ``AT TIME ZONE`` keeps its timestamp type. These tests run the REAL
``_adapt_params`` + ``AsyncpgCompat`` over a fake asyncpg connection that models the one
Postgres type rule at stake (text in timestamp arithmetic is an error) - no live DB.

Negative control: with the operand detection disabled (the pre-fix rewrite), the same
brain-path add fails with the live error text and writes nothing.
"""
from __future__ import annotations

import contextlib
import pathlib
import re

import pytest

import db_pool
import intent_router
import routers.system as system
import write_receipts

pytestmark = pytest.mark.ci_safe

# The Postgres rule the live failure hit: a ``::text`` expression used as an operand of
# + / - (or AT TIME ZONE) has no operator. The rewrite's own output is the only producer
# of ``NOW()::text`` here, so this models exactly the live error class.
_TEXT_ARITH = re.compile(r"::text\s*[-+](?!-)|[-+]\s*NOW\(\)::text|::text\s+AT\s+TIME\s+ZONE", re.IGNORECASE)


class UndefinedFunctionError(Exception):
    """Stand-in for asyncpg.exceptions.UndefinedFunctionError (same message as live)."""


def _pg_type_check(sql: str) -> None:
    if _TEXT_ARITH.search(sql):
        raise UndefinedFunctionError("operator does not exist: text - interval")


def test_type_model_rejects_the_live_failing_shape():
    """Guard the guard: the checker is not vacuous - it rejects the exact pre-fix SQL."""
    with pytest.raises(UndefinedFunctionError, match="text - interval"):
        _pg_type_check("SELECT 1 WHERE created_at::timestamptz > NOW()::text - interval '10 seconds'")
    _pg_type_check("SELECT 1 WHERE created_at::timestamptz > now() - interval '10 seconds'")  # fixed shape passes
    _pg_type_check("UPDATE t SET updated_at = NOW()::text WHERE id = $1")  # a text VALUE is fine


@pytest.mark.parametrize("sql", [
    "SELECT id FROM t WHERE created_at::timestamptz > now() - interval '10 seconds'",
    "SELECT COUNT(*) FROM t WHERE timestamp > NOW() - INTERVAL '7 days'",
    "SELECT 1 WHERE x < NOW()+interval '1 hour'",
    "SELECT 1 WHERE x > interval '1 day' + NOW()",
    "SELECT 1 WHERE x > interval '1 day' +\n        NOW()",
    "SELECT NOW() AT TIME ZONE 'UTC'",
    "SELECT (NOW()) - interval '1 day'",
    "SELECT interval '1 day' + (NOW())",
])
def test_now_as_arithmetic_operand_is_not_text_cast(sql):
    out, _ = db_pool._adapt_params(sql, ())
    assert "NOW()::text" not in out, out
    _pg_type_check(out)


@pytest.mark.parametrize("sql, expected", [
    # The rewrite's purpose is kept: NOW() as a VALUE for a TEXT column is still cast.
    ("UPDATE t SET updated_at = NOW() WHERE id = ?", "UPDATE t SET updated_at = NOW()::text WHERE id = $1"),
    ("INSERT INTO t (a, b) VALUES (?, NOW())", "INSERT INTO t (a, b) VALUES ($1, NOW()::text)"),
    ("UPDATE t SET a = COALESCE(a, now()), b = NOW() -- trailing comment", "UPDATE t SET a = COALESCE(a, NOW()::text), b = NOW()::text -- trailing comment"),
    ("SELECT 'NOW() - 1'", "SELECT 'NOW() - 1'"),  # literals untouched
    # an operator inside a -- line comment is not arithmetic: still a value
    ("UPDATE t SET a = 1 -- bump +\n, updated_at = NOW()", "UPDATE t SET a = 1 -- bump +\n, updated_at = NOW()::text"),
    ("SELECT -- +\n NOW()", "SELECT -- +\n NOW()::text"),
    ("SELECT --\nNOW()", "SELECT --\nNOW()::text"),
])
def test_now_as_value_is_still_text_cast(sql, expected):
    assert db_pool._adapt_params(sql, ())[0] == expected


def test_every_now_arithmetic_site_in_zoe_data_survives_the_rewrite():
    """Class guard over the source: every ``NOW() +/-`` / ``+/- NOW()`` in zoe-data, run through the
    rewrite, stays type-valid. Non-vacuous: the two sites that were broken live must be found."""
    root = pathlib.Path(__file__).resolve().parents[1]
    # NOW() with an operator before or after it; the operand on the other side is replaced by a typed
    # stand-in so the fragment is valid SQL on its own (the call sites embed f-string / quoted pieces).
    site = re.compile(r"([-+]\s*)?(?<![\w.])NOW\(\)(?!::)(\s*[-+](?!-)|\s*AT\s+TIME\s+ZONE)?", re.IGNORECASE)
    found = set()
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", "alembic/")):
            continue
        text = path.read_text(errors="ignore")
        for m in site.finditer(text):
            before, after = m.group(1), m.group(2)
            if not (before or after):
                continue
            near = text[max(0, m.start() - 60): m.end() + 60].lower()
            if "interval" not in near and "time zone" not in near:
                continue  # Python clock arithmetic (``now() - started``), not SQL
            frag = "SELECT 1 WHERE x > " + ("interval '1 second' " if before else "") + m.group(0)
            if after:
                frag += " 'UTC'" if "zone" in after.lower() else " interval '1 second'"
            out, _ = db_pool._adapt_params(frag, ())
            assert "NOW()::text" not in out, (rel, out)
            _pg_type_check(out)
            found.add(rel)
    assert {"list_service.py", "self_model.py"} <= found, found


# --- the brain path end to end: intent-dispatch -> execute_intent -> list_service -> AsyncpgCompat ---------------


class _FakeConn:
    """A minimal asyncpg Connection: applies the Postgres type rule, serves an EXISTING household to-do list
    (the case that failed live - a new list skips the dedup query), records list_items INSERTs."""

    def __init__(self):
        self.inserted: list[tuple] = []
        self.sql: list[str] = []

    async def fetch(self, sql, *args):
        _pg_type_check(sql)
        self.sql.append(sql)
        if "FROM lists l WHERE l.list_type" in sql:
            return [{"id": "list-todo", "name": "To-do", "created_at": "2026-01-01T00:00:00Z", "n": 1}]
        if "FROM lists WHERE list_type" in sql and "user_id" in sql:
            # a to-do (personal type) is the MEMBER'S OWN list: the add resolves it by owner (the canonical-list-slots rule)
            return [{"id": "list-todo"}]
        return []

    async def execute(self, sql, *args):
        _pg_type_check(sql)
        self.sql.append(sql)
        if "INSERT INTO list_items" in sql:
            self.inserted.append(args)
            return "INSERT 0 1"
        return "SELECT 0"

    def transaction(self):
        @contextlib.asynccontextmanager
        async def _txn():
            yield

        return _txn()


@pytest.fixture
def brain_env(monkeypatch):
    conn = _FakeConn()

    @contextlib.asynccontextmanager
    async def _ctx():
        yield db_pool.AsyncpgCompat(conn)

    import database

    monkeypatch.setattr(database, "get_db_ctx", _ctx)

    async def _quiet(*_a, **_k):
        return None

    monkeypatch.setattr(intent_router, "_notify_lists_ui", _quiet)
    return conn


def _dispatch_body():
    return system._IntentDispatchBody(user_id="demo-member", intent="list_add",
                                      slots={"item": "packing tape", "list_type": "tasks"})


@pytest.mark.asyncio
async def test_brain_list_add_writes_the_row_and_records_the_receipt(brain_env):
    started = write_receipts.begin()
    out = await system.intent_dispatch(_dispatch_body(), None)

    assert out["ok"] is True, out
    assert len(brain_env.inserted) == 1, f"the item row must be written: {brain_env.sql}"
    assert "packing tape" in brain_env.inserted[0]
    assert any("interval '10 seconds'" in s for s in brain_env.sql), "the 10 s dedup query must have run"
    receipts = write_receipts.since("demo-member", started, ["list"])
    assert [r.row_id for r in receipts] == [str(brain_env.inserted[0][0])], receipts


@pytest.mark.asyncio
async def test_negative_control_pre_fix_rewrite_fails_the_add(brain_env, monkeypatch, caplog):
    """Restore the pre-fix rewrite (operand detection off): the same add hits the live error and writes nothing."""
    never = re.compile(r"(?!)")
    monkeypatch.setattr(db_pool, "_NOW_ARITH_AFTER_RE", never)
    monkeypatch.setattr(db_pool, "_NOW_ARITH_BEFORE_RE", never)
    started = write_receipts.begin()
    with caplog.at_level("WARNING"):
        out = await system.intent_dispatch(_dispatch_body(), None)

    assert out["ok"] is False
    assert brain_env.inserted == []
    assert write_receipts.since("demo-member", started, ["list"]) == []
    assert "operator does not exist: text - interval" in caplog.text
