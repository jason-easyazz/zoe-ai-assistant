"""Regression tests for the compat cursor's `rowcount`.

The aiosqlite-style `_Cursor` advertised aiosqlite compatibility but never
exposed `rowcount`, so callers doing `cur.rowcount` (proactive cleanup,
notifications, people, worktree bootstrap) raised
`'_Cursor' object has no attribute 'rowcount'`. These pin that write statements
surface the affected-row count parsed from asyncpg's command-status tag, and
SELECTs surface the buffered row count.
"""
import asyncio

import pytest

import db_pool

# Slim-dep-green (imports only db_pool → asyncpg, installed in the GitHub lane).
# Without this marker the file only ran in the Jetson full-dir catch-all.
pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


class _FakeConn:
    """Minimal asyncpg.Connection stand-in for AsyncpgCompat."""

    def __init__(self, status="DELETE 3", rows=None):
        self._status = status
        self._rows = rows or []

    async def execute(self, sql, *args):
        return self._status

    async def fetch(self, sql, *args):
        return list(self._rows)


@pytest.mark.parametrize(
    "status,expected",
    [
        ("DELETE 5", 5),
        ("UPDATE 3", 3),
        ("INSERT 0 2", 2),   # asyncpg INSERT tag is "INSERT <oid> <count>"
        ("DELETE 0", 0),
        ("CREATE TABLE", -1),  # no count → DB-API "unknown"
        ("", -1),
        (None, -1),
    ],
)
def test_parse_status_rowcount(status, expected):
    assert db_pool._parse_status_rowcount(status) == expected


def test_cursor_rowcount_defaults_to_row_len():
    assert db_pool._Cursor([{"id": 1}, {"id": 2}]).rowcount == 2
    assert db_pool._Cursor([]).rowcount == 0
    assert db_pool._Cursor([], rowcount=7).rowcount == 7


def test_write_execute_exposes_rowcount():
    db = db_pool.AsyncpgCompat(_FakeConn(status="DELETE 3"))

    async def go():
        # The proactive-cleanup access pattern: async with + cur.rowcount.
        async with db.execute("DELETE FROM t WHERE claimed = 0 AND x < ?", (1,)) as cur:
            return cur.rowcount

    assert _run(go()) == 3


def test_select_execute_exposes_rowcount():
    db = db_pool.AsyncpgCompat(_FakeConn(rows=[{"id": 1}, {"id": 2}, {"id": 3}]))

    async def go():
        cur = await db.execute("SELECT id FROM t")
        return cur.rowcount

    assert _run(go()) == 3
