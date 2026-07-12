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
import asyncio
import asyncpg
import logging
import os
import re
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None

# Bounded acquire: a leaked connection must fail fast with a diagnosable error,
# not wedge every request forever (live outage 2026-07-12: person_extractor leak
# drained the 10-slot pool and /api/chat hung while /health stayed 200).
_DEFAULT_ACQUIRE_TIMEOUT_S = 10.0


class PoolExhaustedError(RuntimeError):
    """Raised when a pooled connection cannot be acquired within the timeout."""


def _acquire_timeout_s() -> float:
    """Acquire timeout, env-tunable via ZOE_DB_ACQUIRE_TIMEOUT_S (read per call)."""
    raw = os.environ.get("ZOE_DB_ACQUIRE_TIMEOUT_S", "")
    try:
        value = float(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    return _DEFAULT_ACQUIRE_TIMEOUT_S


async def _acquire(pool, timeout_s: float | None = None):
    """Acquire a pooled connection with a bounded wait.

    Uses asyncio.wait_for (not asyncpg's acquire(timeout=)) so it also bounds
    test fakes and any pool-like object. On timeout raises PoolExhaustedError
    with a clear, log-greppable message. Updates the pool gauges best-effort.
    """
    if timeout_s is None:
        timeout_s = _acquire_timeout_s()
    try:
        conn = await asyncio.wait_for(pool.acquire(), timeout=timeout_s)
    except (asyncio.TimeoutError, TimeoutError):
        _update_pool_gauges(pool)
        msg = (
            f"db pool exhausted — possible connection leak "
            f"(acquire timed out after {timeout_s:.1f}s)"
        )
        logger.error("db_pool: %s", msg)
        raise PoolExhaustedError(msg) from None
    _update_pool_gauges(pool)
    return conn


def _update_pool_gauges(pool) -> None:
    """Best-effort refresh of the zoe_db_pool_* Prometheus gauges. Never raises."""
    try:
        from memory_metrics import db_pool_size, db_pool_in_use, db_pool_free

        size = pool.get_size()
        idle = pool.get_idle_size()
        db_pool_size.set(size)
        db_pool_free.set(idle)
        db_pool_in_use.set(size - idle)
    except Exception:
        pass  # metrics must never affect DB access


_SQLITE_DATETIME_OFFSET_RE = re.compile(
    r"datetime\s*\(\s*'now'\s*,\s*'([+-]?)(\d+)\s+(\w+)'\s*\)",
    re.IGNORECASE,
)
_SQLITE_DATETIME_NOW_RE = re.compile(r"datetime\s*\(\s*'now'\s*\)", re.IGNORECASE)
_NOW_RE = re.compile(r"\bNOW\(\)(?!::)", re.IGNORECASE)


async def _release_safely(pool: "asyncpg.Pool", conn: "asyncpg.Connection") -> None:
    """Return `conn` to `pool`, tolerating the request-cancellation teardown race.

    When a request is cancelled mid-query (client disconnect / timeout) the
    dependency generator is closed while an operation is still in flight on the
    connection. asyncpg's `PoolConnectionHolder.release` then fails its reset with
    `InterfaceError('another operation is in progress')` — but it has ALREADY
    terminated the connection and freed the holder before re-raising, so the pool
    stays healthy and a fresh connection is created on the next acquire. We only
    need to swallow the re-raised exception so it doesn't bubble out of the
    dependency teardown as an unretrieved-task error (the log spam this fixes).
    """
    try:
        await pool.release(conn)
    except asyncpg.InterfaceError as exc:
        # The known teardown race ONLY: request cancelled mid-query → asyncpg's
        # reset fails with 'another operation is in progress'. asyncpg has already
        # terminated the connection and freed the holder, so the pool is healthy.
        logger.debug("db_pool: connection release raced after cancellation (%s)", exc)
    except Exception:
        # Any OTHER release failure (closed pool, wrong pool, internal error) is a
        # real pool-health signal — surface it loudly rather than hiding it, but
        # still don't let it break the dependency teardown.
        logger.warning("db_pool: unexpected error releasing connection", exc_info=True)
    finally:
        _update_pool_gauges(pool)


def _bound_loop(pool: "asyncpg.Pool") -> asyncio.AbstractEventLoop | None:
    """Return the loop associated with a pool, preferring our explicit tracking."""
    return _pool_loop or getattr(pool, "_loop", None)


async def _discard_pool(
    pool: "asyncpg.Pool",
    pool_loop: asyncio.AbstractEventLoop | None,
    current_loop: asyncio.AbstractEventLoop,
) -> None:
    """Close a same-loop pool, or terminate a stale cross-loop pool."""
    if pool.is_closing():
        return
    if pool_loop is current_loop:
        await pool.close()
        return

    # asyncpg pools own loop-bound futures/timers. Closing a stale pool from a
    # replacement loop can raise "Event loop is closed" or cross-loop Future
    # errors, so terminate synchronously and replace it on the current loop.
    terminate = getattr(pool, "terminate", None)
    if callable(terminate):
        terminate()
    else:
        logger.warning("db_pool: stale pool has no terminate() method; discarding without close")


async def init_pool() -> asyncpg.Pool:
    """Initialize the asyncpg connection pool.

    Safe to call repeatedly during tests/startup retries: if a live pool exists
    on the current event loop, return it. If the cached pool belongs to a stale
    loop, discard it and create a replacement on the running loop.
    """
    global _pool, _pool_loop
    current_loop = asyncio.get_running_loop()
    if _pool is not None:
        pool_loop = _bound_loop(_pool)
        if not _pool.is_closing() and pool_loop is current_loop:
            return _pool

        stale_pool = _pool
        _pool = None
        _pool_loop = None
        await _discard_pool(stale_pool, pool_loop, current_loop)

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
    _pool_loop = current_loop
    return _pool


async def close_pool() -> None:
    """Close the connection pool. Call on shutdown."""
    global _pool, _pool_loop
    if _pool is not None:
        current_loop = asyncio.get_running_loop()
        await _discard_pool(_pool, _bound_loop(_pool), current_loop)
        _pool = None
        _pool_loop = None


def get_pool() -> asyncpg.Pool:
    """Return the pool, raising if not initialised."""
    if _pool is None:
        raise RuntimeError("db_pool not initialised — call init_pool() first")
    return _pool


def _parse_status_rowcount(status: str) -> int:
    """Affected-row count from an asyncpg command-status tag.

    asyncpg's `Connection.execute` returns tags like "DELETE 5", "UPDATE 3",
    "INSERT 0 2" — the count is the trailing integer. Returns -1 (DB-API's
    "unknown") for tags without one (e.g. "CREATE TABLE").
    """
    try:
        return int(status.rsplit(None, 1)[-1])
    except (AttributeError, ValueError, IndexError):
        return -1


class _Cursor:
    """Buffered cursor providing aiosqlite-compatible fetchall/fetchone/iteration."""
    __slots__ = ("_rows", "_idx", "_rowcount")

    def __init__(self, rows: list, rowcount: int | None = None):
        self._rows = rows
        self._idx = 0
        # Mirror aiosqlite/DB-API: row count of the last operation. For SELECT/
        # RETURNING this is the number of buffered rows; for write statements the
        # caller passes the parsed command-status count.
        self._rowcount = len(rows) if rowcount is None else rowcount

    @property
    def rowcount(self) -> int:
        return self._rowcount

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
            status = await self._conn.execute(sql_pg, *args)
            return _Cursor([], rowcount=_parse_status_rowcount(status))

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
    conn = await _acquire(pool)
    try:
        yield AsyncpgCompat(conn)
    except Exception:
        # Handler raised: roll back any open transaction so the connection returns
        # clean (best-effort; the rollback itself may race and is ignored).
        try:
            if conn.is_in_transaction():
                await conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        # Release through the cancellation-tolerant wrapper so a mid-query teardown
        # race doesn't bubble out of the dependency as an unretrieved-task error.
        await _release_safely(pool, conn)


async def check_pool_health(timeout_s: float = 2.0) -> tuple[bool, str]:
    """Verify a pooled connection can be acquired + used within `timeout_s`.

    Returns (healthy, detail). CONSERVATIVE by design — /health consumers
    (watchdogs, deploy checks) restart the service on 503, so only a genuine
    acquire timeout (pool exhausted) reports unhealthy. Any other condition
    (pool not initialised yet, transient query error) fails OPEN with a detail
    string. Acquire → SELECT 1 → release; never holds a connection.
    """
    global _pool
    if _pool is None:
        return True, "pool not initialised (startup)"
    try:
        conn = await _acquire(_pool, timeout_s=timeout_s)
    except PoolExhaustedError as exc:
        return False, str(exc)
    except Exception as exc:  # acquisition error other than exhaustion → fail open
        return True, f"pool check inconclusive: {exc}"
    try:
        await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=timeout_s)
        return True, "ok"
    except Exception as exc:
        # Connection acquired fine — the pool is not exhausted. A query hiccup
        # is not the outage signature this check exists for; fail open.
        return True, f"pool check query failed (non-fatal): {exc}"
    finally:
        await _release_safely(_pool, conn)


@asynccontextmanager
async def get_db_ctx():
    """Async context manager version of get_db() for use in non-route code.

    Usage:
        async with get_db_ctx() as db:
            rows = await db.fetch("SELECT * FROM users")
    """
    pool = get_pool()
    conn = await _acquire(pool)
    try:
        yield AsyncpgCompat(conn)
    finally:
        await _release_safely(pool, conn)


def _copy_quoted_sql(sql: str, start: int) -> tuple[str, int]:
    """Return the quoted token starting at `start`, preserving doubled escapes."""
    quote = sql[start]
    pos = start + 1
    while pos < len(sql):
        if sql[pos] == quote:
            if pos + 1 < len(sql) and sql[pos + 1] == quote:
                pos += 2
                continue
            pos += 1
            break
        pos += 1
    return sql[start:pos], pos


def _adapt_params(sql: str, params) -> tuple[str, list]:
    """Convert ? placeholders to $1, $2, $3... for asyncpg.

    Also rewrites bare NOW() → NOW()::text so callers that write timestamps
    into TEXT columns (migrated from SQLite) don't get DatatypeMismatchError.
    Only replaces NOW() not already followed by :: to avoid double-casting.

    SQL quoted literals/identifiers are copied verbatim so literal question
    marks and text like 'NOW()' do not become placeholders or casts.
    """
    param_index = 0
    pos = 0
    converted: list[str] = []

    while pos < len(sql):
        char = sql[pos]
        if char in ("'", '"'):
            quoted, pos = _copy_quoted_sql(sql, pos)
            converted.append(quoted)
            continue

        # Rewrite SQLite datetime('now', '±N unit') → PostgreSQL CURRENT_TIMESTAMP ± INTERVAL.
        # Result is cast to ::text so it compares correctly against TEXT timestamp columns.
        # Uses CURRENT_TIMESTAMP (not NOW()) to avoid triggering the NOW()::text rewrite below.
        # Handles: datetime('now', '-7 days'), datetime('now', '+1 day'), etc.
        # Does NOT handle datetime('now', ?) — fix those at the call site.
        datetime_match = _SQLITE_DATETIME_OFFSET_RE.match(sql, pos)
        if datetime_match is not None:
            sign = "-" if datetime_match.group(1) == "-" else "+"
            converted.append(
                f"(CURRENT_TIMESTAMP {sign} INTERVAL "
                f"'{datetime_match.group(2)} {datetime_match.group(3)}')::text"
            )
            pos = datetime_match.end()
            continue

        datetime_now_match = _SQLITE_DATETIME_NOW_RE.match(sql, pos)
        if datetime_now_match is not None:
            converted.append("CURRENT_TIMESTAMP::text")
            pos = datetime_now_match.end()
            continue

        now_match = _NOW_RE.match(sql, pos)
        if now_match is not None:
            converted.append("NOW()::text")
            pos = now_match.end()
            continue

        if char == "?":
            param_index += 1
            converted.append(f"${param_index}")
            pos += 1
            continue

        converted.append(char)
        pos += 1

    return "".join(converted), list(params) if params is not None else []
