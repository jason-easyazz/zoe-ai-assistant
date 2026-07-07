"""P-F1 — `rowcount` on the compat cursor the proactive prune actually uses.

Packet P-F1 (docs/architecture/remediation-packets-2026-07.md) targeted
`db_compat.AsyncpgCompat._Cursor`, but that class is DEAD CODE: every runtime
caller (including `proactive/engine.py`) goes through `db_compat.get_compat_db()`
→ `db_pool.get_db_ctx()` → **`db_pool.AsyncpgCompat`**, whose `_do_execute`
already parses asyncpg's command-status tag ("DELETE 5") into
`_Cursor.rowcount` (landed in #860). These tests pin that live path end-to-end
so `_cleanup_expired_pending`'s hourly prune can never silently regress to the
caught-and-swallowed AttributeError the audit described.

SELECT behaviour (documented choice): `rowcount` is the number of buffered
rows, not DB-API's -1 — the cursor is fully materialised, and this matches
`db_pool._Cursor`'s default (pinned in test_db_pool_rowcount.py).
"""
import asyncio
import logging
import types
from contextlib import asynccontextmanager

import pytest

import db_compat
import db_pool
import proactive.engine as engine

pytestmark = pytest.mark.ci_safe


class _FakeConn:
    """Minimal asyncpg.Connection stand-in (execute → status tag, fetch → rows)."""

    def __init__(self, status="DELETE 0", rows=None):
        self._status = status
        self._rows = rows or []
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return self._status

    async def fetch(self, sql, *args):
        return list(self._rows)


def _patch_pool(monkeypatch, conn):
    """Route db_compat.get_compat_db() at the fake connection.

    get_compat_db imports db_pool.get_db_ctx at call time, so patching db_pool
    is enough — the test still exercises the real db_compat + db_pool wrappers.
    """
    @asynccontextmanager
    async def fake_ctx():
        yield db_pool.AsyncpgCompat(conn)

    monkeypatch.setattr(db_pool, "get_db_ctx", fake_ctx)


def test_delete_rowcount_via_get_compat_db(monkeypatch):
    _patch_pool(monkeypatch, _FakeConn(status="DELETE 5"))

    async def go():
        async with db_compat.get_compat_db() as db:
            # The exact engine access pattern: async with + cur.rowcount.
            async with db.execute(
                "DELETE FROM proactive_pending WHERE claimed = 0 AND expires_at < ?",
                ("2026-01-01T00:00:00Z",),
            ) as cur:
                return cur.rowcount

    assert asyncio.run(go()) == 5


def test_update_rowcount_via_get_compat_db(monkeypatch):
    _patch_pool(monkeypatch, _FakeConn(status="UPDATE 3"))

    async def go():
        async with db_compat.get_compat_db() as db:
            cur = await db.execute("UPDATE t SET x = ? WHERE y = ?", (1, 2))
            return cur.rowcount

    assert asyncio.run(go()) == 3


def test_select_rowcount_is_buffered_row_count(monkeypatch):
    _patch_pool(monkeypatch, _FakeConn(rows=[{"id": 1}, {"id": 2}]))

    async def go():
        async with db_compat.get_compat_db() as db:
            cur = await db.execute("SELECT id FROM t")
            return cur.rowcount

    assert asyncio.run(go()) == 2


# ---------------------------------------------------------------------------
# The engine prune itself: one loop iteration deletes a seeded expired row.
# ---------------------------------------------------------------------------

class _StopLoop(BaseException):
    """Escape _cleanup_expired_pending's while-True (BaseException so the
    engine's `except Exception` swallow-guard can't eat it)."""


class _FakeTableConn:
    """asyncpg stand-in backed by an in-memory proactive_pending table."""

    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = []

    async def execute(self, sql, *args):
        # No assert here: an AssertionError raised inside the fake would be
        # swallowed by the engine's `except Exception` guard and surface as a
        # misleading "prune failed". Record the SQL and assert after the run.
        self.executed_sql.append(sql)
        cutoff = args[0]
        before = len(self.rows)
        self.rows = [
            r for r in self.rows
            if not (r["claimed"] == 0 and r["expires_at"] < cutoff)
        ]
        return f"DELETE {before - len(self.rows)}"

    async def fetch(self, sql, *args):
        return []


def test_cleanup_expired_pending_prunes_seeded_row(monkeypatch, caplog):
    conn = _FakeTableConn([
        # Expired + unclaimed → must be pruned.
        {"id": 1, "claimed": 0, "expires_at": "2000-01-01T00:00:00Z"},
        # Expired but claimed → kept.
        {"id": 2, "claimed": 1, "expires_at": "2000-01-01T00:00:00Z"},
        # Unclaimed but not yet expired → kept.
        {"id": 3, "claimed": 0, "expires_at": "2999-01-01T00:00:00Z"},
    ])

    @asynccontextmanager
    async def fake_db():
        yield db_pool.AsyncpgCompat(conn)

    monkeypatch.setattr(engine, "_get_compat_db", fake_db)

    calls = {"n": 0}

    async def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] > 1:
            raise _StopLoop  # second iteration → stop the loop

    # Surgical: only the engine module's `asyncio.sleep` lookup is redirected.
    monkeypatch.setattr(engine, "asyncio", types.SimpleNamespace(sleep=fake_sleep))

    async def run_one_iteration():
        # Catch _StopLoop inside the coroutine so nothing non-standard
        # propagates through asyncio.run() (BaseException propagation from
        # run_until_complete tightened after 3.10).
        try:
            await engine._cleanup_expired_pending()
        except _StopLoop:
            pass

    with caplog.at_level(logging.INFO, logger="proactive.engine"):
        asyncio.run(run_one_iteration())

    assert conn.executed_sql and "DELETE FROM proactive_pending" in conn.executed_sql[0]
    assert [r["id"] for r in conn.rows] == [2, 3]
    assert "Pruned 1 expired proactive_pending rows" in caplog.text
    # The audit's failure mode was an AttributeError caught and logged here —
    # prove nothing was swallowed.
    assert "cleanup_expired_pending:" not in caplog.text
