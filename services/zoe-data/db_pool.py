"""
db_pool.py — PostgreSQL async connection pool for zoe-data.

Migration strategy:
  - get_db() is an async generator that yields AsyncpgCompat wrappers.
  - AsyncpgCompat supports BOTH the aiosqlite cursor API (for unmigrated code)
    AND raw asyncpg method delegation (fetch/fetchrow/fetchval/execute).
  - This allows FastAPI Depends(get_db), async for db in get_db(), and
    async with get_db() as db: to ALL work without changing every route handler.

New code should use db.fetch(), db.fetchrow(), db.execute() directly.
Old code using cursor = await db.execute() + cursor.fetchall() still works.
"""
import asyncpg
import os
import re
from contextlib import asynccontextmanager

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    """Initialize the asyncpg connection pool. Call once at startup."""
    global _pool
    # Read at call time (not import time) so EnvironmentFile values are available
    postgres_url = os.environ.get("POSTGRES_URL", "")
    if not postgres_url:
        raise RuntimeError(
            "POSTGRES_URL environment variable is not set. "
            "Cannot initialize PostgreSQL connection pool."
        )
    _pool = await asyncpg.create_pool(
        postgres_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )


async def close_pool() -> None:
    """Close the connection pool. Call on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the pool, raising if not initialised."""
    if _pool is None:
        raise RuntimeError("db_pool not initialised — call init_pool() first")
    return _pool


class _Cursor:
    """Buffered cursor providing aiosqlite-compatible fetchall/fetchone/iteration."""
    __slots__ = ("_rows", "_idx")

    def __init__(self, rows: list):
        self._rows = rows
        self._idx = 0

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._idx]
        self._idx += 1
        return row

    @property
    def lastrowid(self):
        if self._rows:
            try:
                return self._rows[0]["id"]
            except (KeyError, TypeError):
                return None
        return None


class _ExecResult:
    """Dual-mode execute result — supports both await and async with (like aiosqlite).

    - cursor = await db.execute(sql, params)
    - async with db.execute(sql, params) as cursor:
    """
    __slots__ = ("_coro", "_result")

    def __init__(self, coro):
        self._coro = coro
        self._result: _Cursor | None = None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self) -> _Cursor:
        self._result = await self._coro
        return self._result

    async def __aexit__(self, *_) -> None:
        pass


class AsyncpgCompat:
    """Compatibility wrapper providing aiosqlite-style execute API on top of asyncpg.

    Supports:
    - cursor = await db.execute(sql, params)       → _Cursor with rows
    - async with db.execute(sql, params) as cursor → same, aiosqlite style
    - rows = await cursor.fetchall()
    - row  = await cursor.fetchone()
    - await db.commit()                            → no-op
    - await db.close()                             → no-op
    - await db.fetch(sql, *args)                   → native asyncpg (delegated)
    - await db.fetchrow(sql, *args)                → native asyncpg (delegated)
    - await db.fetchval(sql, *args)                → native asyncpg (delegated)
    - db.row_factory = ...                         → ignored (no-op property)
    """
    __slots__ = ("_conn",)

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _value):
        pass  # no-op: asyncpg Records already behave like dicts

    def execute(self, sql: str, *params) -> _ExecResult:
        """Return dual-mode result: awaitable or async context manager."""
        # Accept both aiosqlite style: execute(sql, (p1, p2))
        # and asyncpg native style:    execute(sql, p1, p2)
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = params[0]
        return _ExecResult(self._do_execute(sql, params))

    async def _do_execute(self, sql: str, params) -> _Cursor:
        sql_pg, args = _adapt_params(sql, params)
        stripped = sql_pg.strip().upper().lstrip("(")
        if (
            stripped.startswith("SELECT")
            or stripped.startswith("WITH ")
            or stripped.startswith("EXPLAIN")
            or "RETURNING" in stripped
        ):
            rows = await self._conn.fetch(sql_pg, *args)
            return _Cursor(list(rows))
        else:
            await self._conn.execute(sql_pg, *args)
            return _Cursor([])

    async def execute_fetchall(self, sql: str, params=()) -> list:
        """aiosqlite-compatible shorthand: execute and return all rows immediately.

        Accepts ? placeholders (converted to $N) or $N placeholders directly.
        """
        cursor = await self._do_execute(sql, params)
        return list(cursor._rows)

    async def commit(self) -> None:
        pass  # asyncpg auto-commits outside explicit transactions

    async def close(self) -> None:
        pass  # connection managed by pool

    def __getattr__(self, name: str):
        """Delegate native asyncpg methods (fetch, fetchrow, fetchval, execute)."""
        return getattr(self._conn, name)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass


async def get_db() -> AsyncpgCompat:
    """Async generator yielding AsyncpgCompat for a pooled connection.

    Works with:
    - FastAPI Depends(get_db)              → injects AsyncpgCompat as db
    - async for db in get_db():            → yields AsyncpgCompat once
    - async with get_db() as db:           → NOT supported directly; use get_db_ctx()

    Supports both aiosqlite cursor API (legacy code) and asyncpg native API.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            yield AsyncpgCompat(conn)
        except Exception:
            # On abrupt cancellation or early return asyncpg raises InterfaceError
            # ("another operation is in progress") during generator cleanup.
            # Roll back any open transaction so the connection returns cleanly to the pool.
            try:
                if conn.is_in_transaction():
                    await conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


@asynccontextmanager
async def get_db_ctx():
    """Async context manager version of get_db() for use in non-route code.

    Usage:
        async with get_db_ctx() as db:
            rows = await db.fetch("SELECT * FROM users")
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        yield AsyncpgCompat(conn)


def _adapt_params(sql: str, params) -> tuple[str, list]:
    """Convert ? placeholders to $1, $2, $3... for asyncpg.

    Also rewrites bare NOW() → NOW()::text so callers that write timestamps
    into TEXT columns (migrated from SQLite) don't get DatatypeMismatchError.
    Only replaces NOW() not already followed by :: to avoid double-casting.

    WARNING: Transition shim — replaces ALL '?' characters including those
    inside string literals. Migrate callers to explicit $N params.
    """
    i = 0

    def _replace(_match):
        nonlocal i
        i += 1
        return f"${i}"

    # Rewrite SQLite datetime('now', '±N unit') → PostgreSQL CURRENT_TIMESTAMP ± INTERVAL.
    # Result is cast to ::text so it compares correctly against TEXT timestamp columns.
    # Uses CURRENT_TIMESTAMP (not NOW()) to avoid triggering the NOW()::text rewrite below.
    # Handles: datetime('now', '-7 days'), datetime('now', '+1 day'), etc.
    # Does NOT handle datetime('now', ?) — fix those at the call site.
    def _rewrite_sqlite_datetime(m: re.Match) -> str:
        sign = "-" if m.group(1) == "-" else "+"
        return f"(CURRENT_TIMESTAMP {sign} INTERVAL '{m.group(2)} {m.group(3)}')::text"

    sql = re.sub(
        r"datetime\s*\(\s*'now'\s*,\s*'([+-]?)(\d+)\s+(\w+)'\s*\)",
        _rewrite_sqlite_datetime,
        sql,
        flags=re.IGNORECASE,
    )
    # Rewrite bare datetime('now') → CURRENT_TIMESTAMP::text
    sql = re.sub(r"datetime\s*\(\s*'now'\s*\)", "CURRENT_TIMESTAMP::text", sql, flags=re.IGNORECASE)

    # Auto-cast NOW() → NOW()::text for TEXT timestamp columns (SQLite migration compat)
    sql = re.sub(r"\bNOW\(\)(?!::)", "NOW()::text", sql, flags=re.IGNORECASE)

    converted = re.sub(r"\?", _replace, sql)
    return converted, list(params) if params is not None else []
