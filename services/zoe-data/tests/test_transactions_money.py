"""Exact-money transaction tests.

Covers the P2 float-drift + pending-in-summary fixes:
  - integer cents sum EXACTLY (no float drift) where float dollars do not;
  - the 0013 migration round-trips existing REAL values to exact cents
    (run forward on a sample SQLite DB);
  - the weekly summary excludes pending and aggregates exact cents, presenting
    dollars at the boundary;
  - both parsers (regex + NLU) produce the exact integer-cents type.
"""
import asyncio
import importlib.util
import sys
from datetime import date
from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy import create_engine, text
from alembic.migration import MigrationContext
from alembic.operations import Operations

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))

import money  # noqa: E402
from routers import transactions  # noqa: E402

pytestmark = pytest.mark.ci_safe


# ─── Exact cents vs float-sum drift ───────────────────────────────────────────

def test_to_cents_exact_values():
    cases = {
        "0.10": 10, "0.20": 20, "19.99": 1999, "1234.56": 123456,
        "0.07": 7, "100.00": 10000, "0.01": 1, "0": 0,
    }
    for dollars, cents in cases.items():
        assert money.to_cents(dollars) == cents
        assert money.to_cents(float(dollars)) == cents
    # round-trip
    for cents in (1, 7, 10, 1999, 123456):
        assert money.to_cents(money.to_dollars(cents)) == cents


def test_integer_cents_sum_has_no_float_drift():
    amounts = [0.10, 0.20, 19.99, 1234.56, 0.07, 100.00]
    cents = [money.to_cents(a) for a in amounts]
    assert cents == [10, 20, 1999, 123456, 7, 10000]

    total_cents = sum(cents)
    assert total_cents == 135492                       # exact integer sum
    assert money.to_dollars(total_cents) == 1354.92    # exact at the boundary

    # The bug we fix: summing the floats directly drifts off the exact total.
    naive_float_sum = sum(amounts)
    assert naive_float_sum != 1354.92                  # demonstrates the drift
    assert round(naive_float_sum, 2) == 1354.92        # only correct once re-rounded


def test_non_finite_amount_rejected():
    for bad in (float("nan"), float("inf"), "not-money"):
        with pytest.raises(ValueError):
            money.to_cents(bad)


# ─── Migration round-trips existing REAL values ───────────────────────────────

def _load_migration():
    path = SVC / "alembic" / "versions" / "0013_transaction_amount_cents.py"
    spec = importlib.util.spec_from_file_location("mig_0013", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_roundtrips_real_values_on_sample_db():
    migration = _load_migration()
    eng = create_engine("sqlite://")
    samples = [
        ("a", 0.10), ("b", 0.20), ("c", 19.99),
        ("d", 1234.56), ("e", 0.07), ("f", 100.00),
    ]
    with eng.connect() as conn:
        conn.execute(text(
            "CREATE TABLE transactions ("
            " id TEXT PRIMARY KEY, amount REAL NOT NULL, type TEXT DEFAULT 'expense')"
        ))
        for tid, amt in samples:
            conn.execute(
                text("INSERT INTO transactions (id, amount) VALUES (:id, :amt)"),
                {"id": tid, "amt": amt},
            )

        # Run the real migration forward against the sample DB.
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

        rows = conn.execute(
            text("SELECT amount, amount_cents FROM transactions ORDER BY id")
        ).fetchall()
        # Every REAL value migrated to EXACTLY its cents (no cent lost/gained).
        for amount, cents in rows:
            assert cents == money.to_cents(amount)
        assert [r[1] for r in rows] == [10, 20, 1999, 123456, 7, 10000]

        # Aggregating the migrated cents is exact.
        total = conn.execute(text("SELECT SUM(amount_cents) FROM transactions")).scalar()
        assert total == 135492
        assert money.to_dollars(total) == 1354.92

        # Downgrade cleanly drops the column.
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(transactions)")).fetchall()]
        assert "amount_cents" not in cols
        assert "amount" in cols


# ─── Weekly summary: pending excluded, exact cents, dollars at the boundary ───

class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSummaryDb:
    """Minimal functional fake interpreting the three summary queries."""

    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, params):
        return self._exec(sql, params)

    async def _exec(self, sql, params):
        self.executed.append(sql)
        user_id, start, end = params[0], params[1], params[2]

        def visible(r):
            return (
                (r["visibility"] == "family" or r["user_id"] == user_id)
                and not r["deleted"]
                and start <= r["transaction_date"] <= end
                and r["status"] == "completed"          # pending excluded
            )

        def cents(r):
            # Mirrors the COALESCE(amount_cents, ROUND(amount*100)) in the query.
            return r["amount_cents"] if r["amount_cents"] is not None else round(r["amount"] * 100)

        sel = [r for r in self.rows if visible(r)]
        if "GROUP BY type" in sql:
            agg = {}
            for r in sel:
                agg[r["type"]] = agg.get(r["type"], 0) + cents(r)
            return _Cursor([(t, c) for t, c in agg.items()])
        if "type = 'expense'" in sql:
            tot = sum(cents(r) for r in sel if r["type"] == "expense")
            return _Cursor([(tot,)])
        if "type = 'income'" in sql:
            tot = sum(cents(r) for r in sel if r["type"] == "income")
            return _Cursor([(tot,)])
        return _Cursor([])

    async def commit(self):
        pass


def _row(**kw):
    base = dict(
        user_id="u1", visibility="family", deleted=0, status="completed",
        amount_cents=None, amount=0.0, type="expense",
        transaction_date=date.today().isoformat(),
    )
    base.update(kw)
    return base


def test_weekly_summary_excludes_pending_and_is_exact(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(transactions, "require_feature_access", _noop)

    rows = [
        _row(amount_cents=2599, type="expense"),                 # completed 25.99
        _row(amount_cents=5000, type="expense", status="pending"),  # EXCLUDED 50.00
        _row(amount_cents=10000, type="income"),                 # completed 100.00
        _row(amount_cents=None, amount=12.50, type="expense"),   # legacy → COALESCE 12.50
    ]
    db = _FakeSummaryDb(rows)

    result = asyncio.run(transactions.get_weekly_summary(user={"user_id": "u1"}, db=db))

    # Pending 50.00 is excluded; legacy row included via cents fallback.
    assert result["total_expense"] == 38.49     # 25.99 + 12.50
    assert result["total_income"] == 100.00
    assert result["net"] == 61.51
    assert result["by_type"] == {"expense": 38.49, "income": 100.00}

    # Every summary query filters to completed and aggregates exact cents.
    assert db.executed, "no summary queries ran"
    for sql in db.executed:
        assert "status = 'completed'" in sql
        assert "amount_cents" in sql


# ─── Parsers produce the exact integer-cents type ─────────────────────────────

def test_intent_router_parser_produces_exact_cents():
    import intent_router

    intent = intent_router.detect_intent("i spent $19.99 on coffee", log_miss=False)
    assert intent is not None and intent.name == "transaction_create"
    assert intent.slots["amount_cents"] == 1999
    assert intent.slots["amount"] == 19.99
    assert intent.slots["description"] == "coffee"

    intent = intent_router.detect_intent("bought lunch for $3.50", log_miss=False)
    assert intent is not None and intent.name == "transaction_create"
    assert intent.slots["amount_cents"] == 350
    assert intent.slots["amount"] == 3.50


def test_intent_router_rejects_malformed_amount_no_zero_transaction():
    import intent_router

    # A malformed money string must NOT become a $0 transaction (NB5).
    for text in ("i spent $1.2.3 on coffee", "bought coffee for $1.2.3"):
        intent = intent_router.detect_intent(text, log_miss=False)
        assert intent is None or intent.name != "transaction_create", (
            f"{text!r} produced a bogus transaction: {intent}"
        )


def test_nlu_extractor_produces_exact_cents(monkeypatch):
    import nlu_extractor

    async def _fake_tool(text, schema):
        return {"description": "coffee", "amount": 19.99}
    monkeypatch.setattr(nlu_extractor, "_call_with_tool", _fake_tool)

    result = asyncio.run(nlu_extractor._extract_transaction("i spent 19.99 on coffee"))
    assert result["amount_cents"] == 1999
    assert result["amount"] == 19.99
    assert result["description"] == "coffee"


@pytest.mark.parametrize("amount", ["1.2.3", "unknown", None, "", "   ", {}])
def test_nlu_extractor_rejects_bad_or_missing_amount(monkeypatch, amount):
    import nlu_extractor

    async def _fake_tool(text, schema):
        args = {"description": "coffee"}
        if amount != {}:                       # {} sentinel = key absent entirely
            args["amount"] = amount
        return args
    monkeypatch.setattr(nlu_extractor, "_call_with_tool", _fake_tool)

    # Bad / missing / empty amount → None (caller falls through), never a $0 txn.
    result = asyncio.run(nlu_extractor._extract_transaction("i spent x on coffee"))
    assert result is None


def test_nlu_extractor_allows_explicit_zero(monkeypatch):
    import nlu_extractor

    async def _fake_tool(text, schema):
        return {"description": "freebie", "amount": 0}
    monkeypatch.setattr(nlu_extractor, "_call_with_tool", _fake_tool)

    # Zero is reserved for an EXPLICIT 0 amount (not a masked parse failure).
    result = asyncio.run(nlu_extractor._extract_transaction("free sample"))
    assert result is not None
    assert result["amount_cents"] == 0
    assert result["amount"] == 0.0


# ─── SQLite-backed router paths: dialect-safe cents + legacy convergence ───────

_TXN_DDL = """
CREATE TABLE transactions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    description TEXT,
    amount REAL,
    amount_cents BIGINT,
    type TEXT DEFAULT 'expense',
    transaction_date TEXT,
    payment_method TEXT,
    status TEXT DEFAULT 'completed',
    person_id TEXT,
    calendar_event_id TEXT,
    metadata TEXT,
    visibility TEXT DEFAULT 'family',
    deleted INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
)
"""


async def _sqlite_txn_db(rows):
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.create_function("NOW", 0, lambda: "2026-06-29T00:00:00")
    await db.execute(_TXN_DDL)
    for r in rows:
        cols = ", ".join(r.keys())
        ph = ", ".join("?" for _ in r)
        await db.execute(f"INSERT INTO transactions ({cols}) VALUES ({ph})", list(r.values()))
    await db.commit()
    return db


def test_row_cents_sql_is_dialect_aware_and_sqlite_runs():
    pg = transactions._row_cents_sql(object())            # non-sqlite → Postgres
    assert "::numeric" in pg and "::bigint" in pg

    async def _run():
        db = await _sqlite_txn_db([])
        try:
            sqlite_expr = transactions._row_cents_sql(db)
            assert "::numeric" not in sqlite_expr        # no Postgres-only casts
            # The SQLite expression actually executes.
            await db.execute("INSERT INTO transactions (id, user_id, amount) VALUES ('x','u1',12.5)")
            cur = await db.execute(f"SELECT {sqlite_expr} FROM transactions WHERE id='x'")
            assert (await cur.fetchone())[0] == 1250
        finally:
            await db.close()
    asyncio.run(_run())


def test_summary_dialect_safe_on_sqlite(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(transactions, "require_feature_access", _noop)

    today = date.today().isoformat()

    async def _run():
        db = await _sqlite_txn_db([
            dict(id="1", user_id="u1", amount=25.99, amount_cents=2599, type="expense",
                 status="completed", visibility="family", deleted=0, transaction_date=today),
            dict(id="2", user_id="u1", amount=50.0, amount_cents=5000, type="expense",
                 status="pending", visibility="family", deleted=0, transaction_date=today),
            dict(id="3", user_id="u1", amount=100.0, amount_cents=10000, type="income",
                 status="completed", visibility="family", deleted=0, transaction_date=today),
            # Legacy row: amount only, amount_cents NULL → COALESCE fallback.
            dict(id="4", user_id="u1", amount=12.50, type="expense",
                 status="completed", visibility="family", deleted=0, transaction_date=today),
        ])
        try:
            return await transactions.get_weekly_summary(user={"user_id": "u1"}, db=db)
        finally:
            await db.close()

    result = asyncio.run(_run())
    assert result["total_expense"] == 38.49   # 25.99 + 12.50; pending 50.00 excluded
    assert result["total_income"] == 100.00
    assert result["net"] == 61.51


def test_update_converges_legacy_amount_cents(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(transactions, "require_feature_access", _noop)

    async def _bcast(*a, **k):
        return None
    monkeypatch.setattr(transactions.broadcaster, "broadcast", _bcast)

    from models import TransactionUpdate

    async def _run():
        db = await _sqlite_txn_db([
            # Legacy/MCP-style row: REAL amount set, amount_cents NULL.
            dict(id="t1", user_id="u1", description="old", amount=12.50,
                 type="expense", status="completed", visibility="family",
                 deleted=0, transaction_date=date.today().isoformat()),
        ])
        try:
            # Update only the description — amount NOT in the payload.
            payload = TransactionUpdate(description="new label")
            await transactions.update_transaction(
                transaction_id="t1", payload=payload, user={"user_id": "u1"}, db=db
            )
            cur = await db.execute("SELECT amount_cents, description FROM transactions WHERE id='t1'")
            return await cur.fetchone()
        finally:
            await db.close()

    row = asyncio.run(_run())
    # The legacy row converged onto the exact source-of-truth column (NB1).
    assert row["amount_cents"] == 1250
    assert row["description"] == "new label"
