"""erase_watermark - a durable per-member erase WATERMARK, so no transcript miner re-derives an erased member's facts.

Why. ``MemoryService.delete_user`` erases what Zoe derived from a member but NOT the chat transcript (``chat_sessions`` /
``chat_messages``: whether it should is the owner's decision). The in-memory erase gate (``user_erase_gate``, #2569) drops a write
SCHEDULED before or during the erase; a pass that STARTS after it has a fresh snapshot. Seen live 2026-11-14 with a synthetic demo
member: the idle-consolidation sweep three minutes after the erase re-read the session's surviving turns and stored two rows. The
nightly dreaming job runs in a separate process and cannot see the in-memory gate at all.

What. One row per member in the forget ledger's own table ``memory_forgotten`` (alembic 0038; no new migration):
``user_id`` = the member id (the ledger keeps ids in clear), ``key_hash`` = ``KEY`` (a fixed literal, never a hash: ledger hashes
are hex, so no lookup can ever match it), ``scope`` = ``erase``, ``forgotten_at`` = the erase instant (UTC, microseconds,
fixed width), ``shield_until`` = ``NEVER`` (a far-past sentinel, so every ledger read - ``shield_until > now``: active hashes,
``users()`` - skips the row and entity forgetting behaves exactly as before). No text. A later erase moves it forward, never back.
``delete_user`` never removes ``memory_forgotten`` (``forget_reach``: out of scope), so the mark survives the erase it records, a
restart, and is read by any process that opens the pool. Never prune ``memory_forgotten`` by ``shield_until``: that drops these.

Rule (``keep_after``, the ONE filter every transcript miner calls): a member's turn at or before their watermark is skipped; a turn
strictly after it is mined as normal (a member who keeps using Zoe gets new memories from new turns only). FAIL CLOSED: an
unreadable watermark raises ``Unavailable`` (a ``memory_forgotten.LedgerUnavailable``, so the callers' existing "defer this run"
paths apply) or, with ``strict=False``, drops that member's rows for this pass; a turn whose time cannot be read is skipped. Logs a
reader, a code and counts: never an id, never text.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional, Union

from memory_forgotten import LedgerUnavailable

logger = logging.getLogger(__name__)

KEY = "erase|watermark"
SCOPE = "erase"
NEVER = "0000-01-01T00:00:00Z"
_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


class Unavailable(LedgerUnavailable):
    """The watermark could not be read (or written): the caller must not mine (or erase) on a guess."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_FMT)


def _now() -> datetime:
    """Wall clock (UTC): the same host clock Postgres stamps ``chat_messages.created_at`` with (``NOW()::TEXT``). A test seam."""
    return datetime.now(timezone.utc)


_TS = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(?:\.(\d{1,6})\d*)?\s*(Z|[+-]\d\d(?::?\d\d)?)?$")


def instant(value: Any) -> Optional[datetime]:
    """A turn time as an aware UTC ``datetime``, or None when it cannot be read. Takes a datetime, epoch seconds, or the text
    Postgres writes (``2026-01-02 03:04:05.12345+08``). A NAIVE time is ambiguous (UTC or Zoe-local): the EARLIER reading is used,
    so an ambiguous turn is kept only when both readings are after the watermark (fail closed)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        d = value
    else:
        m = _TS.match(str(value).strip())
        if not m:
            return None
        day, clock, frac, off = m.groups()
        off = "+00:00" if off in (None, "Z") else (off + ":00" if len(off) == 3 else off[:3] + ":" + off[-2:])
        try:
            d = datetime.fromisoformat(f"{day}T{clock}.{(frac or '0').ljust(6, '0')}" + ("" if m.group(4) is None else off))
        except ValueError:
            return None
    if d.tzinfo is not None:
        return d.astimezone(timezone.utc)
    from time_utils import zoe_timezone
    return min(d.replace(tzinfo=timezone.utc), d.replace(tzinfo=zoe_timezone()).astimezone(timezone.utc))


class MemoryBackend:
    """In-process store (tests, lab)."""

    def __init__(self):
        self.rows: dict = {}

    async def put(self, user_id: str, at: str, actor: str) -> None:
        self.rows[user_id] = max(at, self.rows.get(user_id, ""))

    async def get(self, user_id: str) -> Optional[str]:
        return self.rows.get(user_id)


class SqlBackend:
    """The ``memory_forgotten`` table over ``db_pool`` (portable SQL, so the tests run these statements on SQLite). ``ctx`` is an
    optional connection factory (an async context manager yielding an ``AsyncpgCompat``-style connection) for a script with no pool."""

    def __init__(self, ctx: Optional[Callable[[], Any]] = None):
        self._ctx = ctx

    def _db(self):
        if self._ctx is not None:
            return self._ctx()
        from db_pool import get_db_ctx  # type: ignore[import]
        return get_db_ctx()

    async def put(self, user_id: str, at: str, actor: str) -> None:
        async with self._db() as db:
            await db.execute(
                """INSERT INTO memory_forgotten (user_id, key_hash, scope, actor, forgotten_at, shield_until)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (user_id, key_hash) DO UPDATE
                   SET forgotten_at = CASE WHEN excluded.forgotten_at > memory_forgotten.forgotten_at
                                           THEN excluded.forgotten_at ELSE memory_forgotten.forgotten_at END,
                       actor = excluded.actor, scope = excluded.scope, shield_until = excluded.shield_until""",
                (user_id, KEY, SCOPE, actor, at, NEVER))
            await db.commit()

    async def get(self, user_id: str) -> Optional[str]:
        async with self._db() as db:
            cur = await db.execute("SELECT forgotten_at FROM memory_forgotten WHERE user_id = ? AND key_hash = ?", (user_id, KEY))
            row = await cur.fetchone()
        return str(row[0]) if row and row[0] else None


_backend: Optional[Any] = None


def get_backend():
    global _backend
    if _backend is None:
        _backend = SqlBackend()
    return _backend


def set_backend(backend) -> None:
    """Swap the store (tests / lab); ``None`` restores the SQL default."""
    global _backend
    _backend = backend


async def record(user_id: str, *, actor: str = "") -> datetime:
    """Mark ``user_id`` erased NOW (called by ``MemoryService.delete_user`` before it erases anything). RAISES ``Unavailable`` when
    the mark cannot be made durable: the caller erases nothing (an erase without its mark would let the transcript re-mine it)."""
    at = _now()
    try:
        await get_backend().put(str(user_id), _iso(at), str(actor or "")[:64])
    except Exception as exc:  # noqa: BLE001
        logger.warning("ERASE_WATERMARK_WRITE_FAILED code=%s", type(exc).__name__)
        raise Unavailable(f"erase watermark not recorded ({type(exc).__name__})") from exc
    return at


async def watermark(user_id: str, *, backend=None) -> Optional[datetime]:
    """When ``user_id`` was last erased, or None (never). RAISES ``Unavailable`` when the store cannot be read."""
    try:
        raw = await (backend or get_backend()).get(str(user_id))
    except Exception as exc:  # noqa: BLE001
        raise Unavailable(f"erase watermark unreadable ({type(exc).__name__})") from exc
    if raw is None:
        return None
    at = instant(raw)
    if at is None:
        raise Unavailable("erase watermark unparseable")
    return at


_UNREADABLE = object()


async def keep_after(rows: Iterable[Any], *, owner: Union[str, Callable[[Any], Any]], at: Callable[[Any], Any], reader: str,
                     strict: bool = True, backend=None) -> list:
    """``rows`` without the turns of an erased member said at or before their watermark. ``owner`` is the member id (or a function
    of the row, for a batch of several members' rows); ``at`` gives the row's time. Rows with no owner are kept (they are no
    member's). An unreadable watermark: ``strict`` raises ``Unavailable`` (the caller defers the pass), otherwise that member's
    rows are dropped for this pass. Order is kept. ``backend`` overrides the store (a script with its own connection)."""
    rows = list(rows)
    owner_of = owner if callable(owner) else (lambda _r: owner)
    marks: dict = {}
    kept, skipped, unread = [], 0, 0
    for r in rows:
        uid = str(owner_of(r) or "")
        if not uid:
            kept.append(r)
            continue
        if uid not in marks:
            try:
                marks[uid] = await watermark(uid, backend=backend)
            except Unavailable as exc:
                logger.warning("ERASE_WATERMARK_UNREADABLE reader=%s code=%s strict=%s", reader,
                               type(exc.__cause__ or exc).__name__, int(strict))
                if strict:
                    raise
                marks[uid] = _UNREADABLE
        mark = marks[uid]
        if mark is None:
            kept.append(r)
        elif mark is _UNREADABLE:
            unread += 1
        else:
            try:
                t = instant(at(r))
            except Exception:  # noqa: BLE001 - a row whose time cannot be read is skipped (fail closed)
                t = None
            if t is not None and t > mark:
                kept.append(r)
            else:
                skipped += 1
    if skipped or unread:
        logger.info("ERASE_WATERMARK_SKIP reader=%s skipped=%d unreadable=%d kept=%d", reader, skipped, unread, len(kept))
    return kept


# ── night writers: a member erased after the pass STARTED is skipped (the reads it already made are pre-erase) ─────────────────
#: when the current night pass started (``night_cycle``); None outside one
_cycle: "contextvars.ContextVar" = contextvars.ContextVar("erase_watermark_cycle", default=None)


def night_cycle(fn):
    """Decorate an async night pass (the dreaming cycle, the nightly digest, a weekly / music / portrait pass): its start is recorded for
    ``erased_since_cycle``. A pass already inside one keeps the outer, earlier start."""
    @functools.wraps(fn)
    async def call(*args, **kwargs):
        token = _cycle.set(_now()) if _cycle.get() is None else None
        try:
            return await fn(*args, **kwargs)
        finally:
            if token is not None:
                _cycle.reset(token)
    return call


ERASED, CLEAR, UNKNOWN = "erased", "clear", "unknown"


async def erase_state(user_id: str, phase: str) -> str:
    return (await _erase_state(user_id, phase))[0]


async def _erase_state(user_id: str, phase: str) -> "tuple[str, str]":
    """Inside a night pass: ``ERASED`` (the member's mark is at or after the pass start), ``CLEAR`` (it is not), or ``UNKNOWN`` (the mark
    could not be read). ``CLEAR`` outside a night pass. A night pass runs in its own process, where the in-memory erase gate never moves,
    so this is the check that reaches it. Logs a phase name and an error type only."""
    started = _cycle.get()
    if started is None or not user_id:
        return CLEAR, ""
    try:
        mark = await watermark(str(user_id))
    except Unavailable as exc:
        code = type(exc.__cause__ or exc).__name__
        logger.warning("ERASE_WATERMARK_UNREADABLE reader=%s code=%s strict=1", phase, code)
        return UNKNOWN, code
    if mark is not None and mark >= started:
        logger.info("ERASE_WATERMARK_PHASE_SKIP phase=%s", phase)
        return ERASED, ""
    return CLEAR, ""


async def erased_since_cycle(user_id: str, phase: str) -> bool:
    """The PRE-write check: True = do NOT write (erased since the pass began, or the mark is unreadable: fail closed - skipping a write
    loses nothing that the next pass will not redo). Never use it to decide a destructive step: that is ``erase_state``'s three answers."""
    return await erase_state(user_id, phase) != CLEAR


async def guarded_write(user_id: str, phase: str, write: Callable[[], Any], undo: Callable[[list], Any]) -> Any:
    """The ONE write rule for a night pass, whose erase check can go stale while it writes: ``delete_user`` runs in another process and
    no in-process lock spans the two.

    (1) PRE-check (``erased_since_cycle``, fail closed): erased or unreadable -> no write, None.
    (2) ``created = await write()``: the write returns the ids / keys it CREATED, and only those (``[]`` when it only MODIFIED rows that
        already existed; None when it declined to write). A modification must use a write that never creates (Chroma ``update``, SQL
        ``UPDATE``), so a row the erase removed meanwhile is not brought back - and an undo must never touch a row it did not create:
        that would destroy a stored fact.
    (3) POST-check (``erase_state``), three answers: ERASED -> ``await undo(created)`` (skipped when nothing was created) and None;
        CLEAR -> keep, return ``created``; UNKNOWN (the mark could not be read) -> keep, log ``ERASE_WATERMARK_UNKNOWN``, return
        ``created``. Fail closed applies to the pre-check only, never to a destructive undo: the mark is written BEFORE the erase starts
        and a real erase removes the member's rows itself, so an unreadable mark is no evidence of an erase, while undoing on a blip
        could delete a fact that was never at risk.
    Why this closes the race for created rows: a write that commits after the erase enumerated its store sees the mark at (3); one that
    commits before is erased by the erase itself. Outside a night pass both checks are no-ops. A failed undo RAISES (logged, codes only)."""
    if await erased_since_cycle(user_id, phase):
        return None
    created = await write()
    if created is None:
        return None              # the write itself declined: nothing to keep, nothing to undo
    state, code = await _erase_state(user_id, phase)
    if state == UNKNOWN:
        logger.warning("ERASE_WATERMARK_UNKNOWN phase=%s code=%s", phase, code)
        return created
    if state == CLEAR:
        return created
    if created:
        try:
            await undo(list(created))
        except Exception as exc:  # noqa: BLE001
            logger.error("ERASE_WATERMARK_COMPENSATE_FAILED phase=%s code=%s", phase, type(exc).__name__)
            raise
        logger.warning("ERASE_WATERMARK_COMPENSATED phase=%s", phase)
    return None


async def run_sql(db, sql: str, params: tuple) -> None:
    """Execute + commit on ``db``, or on a short-lived pooled connection when ``db`` is None (a ``guarded_write`` undo step)."""
    if db is not None:
        await db.execute(sql, params)
        await db.commit()
        return
    from db_pool import get_db_ctx  # type: ignore[import]
    async with get_db_ctx() as conn:
        await conn.execute(sql, params)
        await conn.commit()


async def insert_or_update(db, exists: tuple, insert: tuple, update: tuple) -> bool:
    """One row, the night-write way: ``UPDATE`` it when it exists (an UPDATE never brings back a row an erase removed meanwhile), else
    ``INSERT`` it. Each argument is ``(sql, params)``. Returns True when the row was CREATED (only then may a ``guarded_write`` undo
    delete it). On ``db``, or a short-lived pooled connection when ``db`` is None."""
    async def go(conn) -> bool:
        cur = await conn.execute(*exists)
        found = await cur.fetchone()
        await conn.execute(*(update if found else insert))
        await conn.commit()
        return not found
    if db is not None:
        return await go(db)
    from db_pool import get_db_ctx  # type: ignore[import]
    async with get_db_ctx() as conn:
        return await go(conn)


async def erased_since(user_id: str, made_at: Any) -> bool:
    """True when ``user_id`` has an erase mark at or after ``made_at`` (a row's creation time; an unreadable time counts as erased).
    No mark at all: False. RAISES ``Unavailable`` when the mark cannot be read (the caller does not act on a guess)."""
    mark = await watermark(user_id)
    if mark is None:
        return False
    made = instant(made_at)
    return made is None or made <= mark


#: when the delivery in progress began (``delivery``), for the send steps deep in its path that take no ``since`` of their own
_delivery: "contextvars.ContextVar" = contextvars.ContextVar("erase_watermark_delivery", default=None)


@contextlib.contextmanager
def delivery(since: Any):
    """Run one delivery (a notification fan-out) with ``since`` = when it began: ``send_refused(uid, None, ...)`` deeper in its path
    (``routers.push.send_push_to_user``, the spoken lanes) uses it. Tasks the delivery starts inherit it (contextvars)."""
    token = _delivery.set(since)
    try:
        yield
    finally:
        _delivery.reset(token)


async def send_refused(user_id: Any, since: Any, lane: str) -> bool:
    """The LAST-STEP send guard, called after every await that can stall and right before a send leaves the box (a push, a queued
    spoken line, a phone message): True = send NOTHING, because ``user_id`` was deleted at or after ``since`` (when the delivery began,
    or when the queued row was made; None = the enclosing ``delivery``'s). No member id, or no ``since`` at all: False. RAISES
    ``Unavailable`` when the mark cannot be read (the caller does not send on a guess). Logs the lane only."""
    uid = str(user_id or "").strip()
    since = _delivery.get() if since is None else since
    if not uid or since is None:
        return False
    if await erased_since(uid, since):
        logger.info("ERASE_WATERMARK_SEND_REFUSED lane=%s", lane)
        return True
    return False


async def refuse_send(user_id: Any, since: Any, lane: str) -> bool:
    """``send_refused`` that FAILS CLOSED: an unreadable mark also refuses (the caller sends nothing on a guess, and retries later where
    it can). For the senders that have no claim to release and no caller to raise into (the phone digests)."""
    try:
        return await send_refused(user_id, since, lane)
    except Unavailable:
        logger.warning("ERASE_WATERMARK_SEND_UNREADABLE lane=%s", lane)
        return True


def delivery_began() -> Optional[datetime]:
    """When the enclosing ``delivery`` began, or None outside one. A row a delivery QUEUES is stamped with it (not with the insert
    time), so a delete that lands at any moment after the delivery began refuses the row where it is consumed."""
    return instant(_delivery.get())


# -- in-flight member turns: the delete waits for them, and each late write re-checks the mark against the turn's start -------------
#: token -> {"session", "member", "began", "refs"}: every member turn in flight in this process. A chat turn is keyed by its session
#: (``routers/chat.locked_chat_stream`` holds the session lock: one at a time); a VOICE turn (``routers/voice_tts.voice_command``) also
#: binds its token in the task context (``_TURN_CTX``), which its stream body and its background tasks inherit, and is held open by
#: them (``turn_hold`` / ``turn_release``) until its last write. The member is whatever is known at the start and RE-KEYED when the
#: speaker is resolved (``turn_identify``); the START time never changes.
_TURNS: dict = {}
_TURN_CTX: "contextvars.ContextVar" = contextvars.ContextVar("erase_watermark_turn", default=None)
#: a turn older than this is not waited for (a stream nobody iterated never runs its ``finally``): it is CANCELLED instead
#: (``cancel_stale_turns``), and every store write it could still make is refused at the database wrapper (``store_write_guard``)
TURN_STALE_S = 600.0


def _current_task():
    try:
        import asyncio

        return asyncio.current_task()
    except RuntimeError:
        return None


def turn_open(session_id: Optional[str], user_id: str) -> str:
    """A member turn began (``session_id``: a chat turn's session; None for a voice turn, which is found through the context).
    Returns its token. The task running it is kept, so a STALE turn of a deleted member can be cancelled (``cancel_stale_turns``)."""
    import uuid

    token = uuid.uuid4().hex
    _TURNS[token] = {"session": str(session_id) if session_id else None, "member": str(user_id or ""), "began": _now(), "refs": 1,
                     "task": _current_task()}
    return token


def turn_bind(token: str):
    """Bind a turn opened with ``turn_open`` (a chat turn) in the CURRENT context, as a voice turn binds itself: the store writes its
    tools make in this task (and the tasks it starts) are then judged against this turn's start (``store_write_guard``). Returns the
    context token for ``turn_unbind``."""
    entry = _TURNS.get(str(token or ""))
    return _TURN_CTX.set((str(token), entry["began"])) if entry else None


def turn_unbind(ctx_token) -> None:
    """Undo ``turn_bind`` / ``bind_member_turn`` (the latter also closes the tool call's own turn)."""
    if ctx_token is None:
        return
    if isinstance(ctx_token, tuple) and ctx_token and ctx_token[0] == "tool":
        _kind, token, ctx_token = ctx_token
        _TURNS.pop(token, None)
    try:
        _TURN_CTX.reset(ctx_token)
    except ValueError:      # finalised in another context (a stream closed by the server): that context dies with it
        pass


def bind_member_turn(user_id: str):
    """A write that arrives WITHOUT a turn context but on behalf of a member's turn - a brain TOOL call, which reaches zoe-data as a
    separate request (``/api/system/intent-dispatch``), an MCP tool call - is a turn of its OWN in the registry: the member's id and
    the START of their OLDEST turn in flight (the conservative choice: an erase at or after it refuses), and the task running it. So
    it outlives its parent turn closing, the delete waits for it (or cancels it once stale) like any turn, and it is bound in the
    current context. No turn of the member in flight: nothing is opened or bound. Returns the token for ``turn_unbind``."""
    uid = str(user_id or "")
    starts = [e["began"] for e in list(_TURNS.values()) if uid and e["member"] == uid]
    if not starts:
        return None
    began = min(starts)
    token = turn_open(None, uid)
    _TURNS[token]["began"] = began
    return ("tool", token, _TURN_CTX.set((token, began)))


def release_task_turns(task) -> None:
    """Close every turn ``task`` opened and never closed (a tool call bound in a task of its own, ``mcp_server.handle_tool``)."""
    for t in [t for t, e in list(_TURNS.items()) if task is not None and e.get("task") is task]:
        _TURNS.pop(t, None)


def stale_turn_tasks(user_id: str) -> list:
    """The tasks of ``user_id``'s turns older than ``TURN_STALE_S`` that are still running."""
    uid, cutoff = str(user_id or ""), _now().timestamp() - TURN_STALE_S
    return [e["task"] for e in list(_TURNS.values()) if e["member"] == uid and e["began"].timestamp() < cutoff
            and e.get("task") is not None and not e["task"].done()]


def cancel_stale_turns(user_id: str) -> list:
    """Cancel the member's STALE turns (the delete does not wait for them): a turn stalled for ten minutes that resumed after the
    delete would otherwise write as a deleted member. Returns the cancelled tasks (the caller waits for them to end). The task
    running the caller is never cancelled."""
    me = _current_task()
    out = [t for t in stale_turn_tasks(user_id) if t is not me]
    for t in out:
        t.cancel()
    if out:
        logger.warning("ERASE_WATERMARK_STALE_TURNS_CANCELLED count=%d", len(out))
    return out


class TurnWriteRefused(RuntimeError):
    """A store write a turn made for a member erased at or after the turn BEGAN (``store_write_guard``): the write never runs."""


#: an INSERT INTO / UPDATE / DELETE FROM <table> statement's target table (lower case)
_WRITE_RX = re.compile(r"^\s*(?:with\b.*?\)\s*)?(?:insert\s+into|update|delete\s+from)\s+\"?([a-z_][a-z0-9_]*)", re.IGNORECASE | re.DOTALL)


def write_target(sql: str) -> str:
    m = _WRITE_RX.match(str(sql or ""))
    return m.group(1).lower() if m else ""


@contextlib.asynccontextmanager
async def _on(conn):
    yield conn


async def store_write_guard(sql: str, conn: Any = None) -> None:
    """THE ONE CHECK every member-owned store write of a turn passes (``db_pool.AsyncpgCompat``: every pooled statement): inside a
    bound turn (voice, chat, or a brain tool call bound by ``bind_member_turn``), a write to a member-owned table
    (``member_owned_forget.TURN_WRITE_TABLES``) for a turn whose member was erased at or after the turn began raises
    ``TurnWriteRefused`` BEFORE the statement runs. Outside a turn it costs one ContextVar read; inside one, one mark read per
    member-owned write, read on the CALLER's connection (``conn``: no second pooled connection, so a busy pool neither slows the write
    nor turns the check into an unreadable mark). A member delete's own statements are never judged (``user_erase_gate.whole_member_erase``
    unbinds). An unreadable mark lets the write run (the late-write rule's choice for a turn's own save; the erase fails closed)."""
    bound = _TURN_CTX.get()
    if bound is None:
        return
    table = write_target(sql)
    if not table:
        return
    import member_owned_forget

    if table not in member_owned_forget.TURN_WRITE_TABLES:
        return
    entry = _TURNS.get(bound[0]) if bound[0] else None
    member = (entry or {}).get("member") or ""
    if not member:
        return
    backend = get_backend()
    in_tx = False
    if conn is not None and isinstance(backend, SqlBackend) and backend._ctx is None:
        backend = SqlBackend(ctx=lambda: _on(conn))
        probe = getattr(conn, "is_in_transaction", None)
        in_tx = bool(callable(probe) and probe())
    try:
        if in_tx:      # inside the caller's transaction the read runs in a SAVEPOINT: a failed read never aborts the caller's work
            async with conn.transaction():
                mark = await watermark(member, backend=backend)
        else:
            mark = await watermark(member, backend=backend)
    except Unavailable:
        return
    if mark is not None and bound[1] <= mark:
        logger.warning("ERASE_WATERMARK_STORE_WRITE_REFUSED table=%s", table)
        raise TurnWriteRefused(table)


def turn_close(token_or_session: str) -> None:
    """Release a turn (its token), or every turn of a chat session (the session id)."""
    key = str(token_or_session or "")
    if key in _TURNS:
        turn_release(key)
        return
    for t in [t for t, e in list(_TURNS.items()) if e["session"] == key]:
        _TURNS.pop(t, None)


def voice_turn_begin():
    """``turn_open`` for a voice turn, bound in the CURRENT context (its stream body and background tasks inherit it). Returns the
    context token for ``voice_turn_end``."""
    token = turn_open(None, "")
    return _TURN_CTX.set((token, _TURNS[token]["began"]))


def voice_turn_end(ctx_token) -> None:
    bound = _TURN_CTX.get()
    if bound is not None:
        turn_release(bound[0])
    _TURN_CTX.reset(ctx_token)


def turn_identify(user_id: str) -> None:
    """The context's turn is now known to be ``user_id``'s (the speaker was resolved): the delete waits for it from here on. The
    turn keeps the START it had."""
    bound = _TURN_CTX.get()
    if bound is not None and bound[0] in _TURNS and user_id:
        _TURNS[bound[0]]["member"] = str(user_id)


def turn_hold() -> Optional[str]:
    """One more piece of work of the context's turn (a background task, a stream body): the turn stays in flight until it releases."""
    bound = _TURN_CTX.get()
    if bound is None or bound[0] not in _TURNS:
        return None
    _TURNS[bound[0]]["refs"] += 1
    return bound[0]


def turn_release(token: Optional[str]) -> None:
    entry = _TURNS.get(str(token or ""))
    if entry is None:
        return
    entry["refs"] -= 1
    if entry["refs"] <= 0:
        _TURNS.pop(str(token), None)


#: the caller's own turn token, remembered by user_erase_gate.whole_member_erase when it clears _TURN_CTX for a delete
_ERASE_OWN_TURN: "contextvars.ContextVar" = contextvars.ContextVar("erase_watermark_erase_own_turn", default=None)


def inflight_turns(user_id: str, *, exclude_own: bool = False) -> int:
    """How many turns of ``user_id`` are in flight in this process now (the member delete waits for them; stale ones excepted).
    ``exclude_own``: leave out the turn bound in the CALLER's context - a delete must not wait for the very request that runs it (an
    administrator forgetting their own memories: their request is a turn, ``auth._bind_request_turn``)."""
    uid, cutoff = str(user_id or ""), _now().timestamp() - TURN_STALE_S
    # OWN = exactly the turn the caller had bound: the bound context, or - inside user_erase_gate.whole_member_erase, which clears
    # the context before the erasers - the token it remembered as it cleared it (_ERASE_OWN_TURN). Only that one turn: an earlier
    # turn of the same member still held by background work, or a turn of another task, is still waited for.
    own = None
    if exclude_own:
        bound = _TURN_CTX.get()
        token = bound[0] if bound else _ERASE_OWN_TURN.get()
        entry = _TURNS.get(token) if token else None
        # ...and only when that turn is run by THIS task: a task started from inside a turn inherits its context but is a different
        # task, and must still wait for the turn (a delete scheduled from a voice turn waits for the turn's own tool write)
        if entry is not None and entry.get("task") is not None and entry["task"] is _current_task():
            own = token
    return sum(1 for t, e in list(_TURNS.items()) if e["member"] == uid and e["began"].timestamp() >= cutoff
               and not (own is not None and t == own))


def turn_began(session_id: Optional[str] = None) -> Optional[datetime]:
    """When the turn this write belongs to began: the context's voice turn, else the chat turn in flight on ``session_id``."""
    bound = _TURN_CTX.get()
    if bound is not None:
        return bound[1]
    starts = [e["began"] for e in list(_TURNS.values()) if session_id and e["session"] == str(session_id)]
    return min(starts) if starts else None


async def turn_write_refused(session_id: Optional[str], lane: str, user_id: Optional[str] = None) -> bool:
    """THE LATE-WRITE RULE for a turn's writes: True = drop (or undo) this write, because the member it is written AS (``user_id``,
    else the turn's member) was erased at or after the turn BEGAN (its start, never the moment the speaker was resolved). Asked twice
    by every writer: right before its write and right after it (undoing the write when True) - a delete that lands between the two
    finds the row with its erase or is caught by the second check, since the mark is recorded before anything is erased, and the mark
    is DURABLE, so this holds for a turn in any process. No turn known: False. An unreadable mark: False (a turn's own save is never
    dropped on a guess; the erase itself fails closed on an unreadable store)."""
    began = turn_began(session_id)
    if began is None:
        return False
    uid = user_id
    if not uid:
        bound = _TURN_CTX.get()
        entries = ([_TURNS.get(bound[0])] if bound is not None else
                   [e for e in list(_TURNS.values()) if session_id and e["session"] == str(session_id)])
        uid = next((e["member"] for e in entries if e and e["member"]), "")
    try:
        return await send_refused(uid, began, lane)
    except Unavailable:
        return False


def hold_session_turn(session_id: Optional[str]) -> Optional[str]:
    """One more piece of work of the chat turn in flight on ``session_id`` (its post-turn passes, queued from inside the turn): the
    turn stays in flight - the member delete waits - until the work releases it (``turn_release``). None when no turn is open there."""
    for token, entry in list(_TURNS.items()):
        if session_id and entry["session"] == str(session_id):
            entry["refs"] += 1
            return token
    return None


def held_at_call(session_arg: str):
    """Decorate an async post-turn writer queued from inside a chat turn: at the CALL (the moment it is queued, before the turn can
    end) the turn on the session named by argument ``session_arg`` is held, and released when the writer finishes."""
    import functools
    import inspect

    def wrap(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def call(*args, **kwargs):
            bound = sig.bind_partial(*args, **kwargs)
            token = hold_session_turn(bound.arguments.get(session_arg))
            inner = fn(*args, **kwargs)        # created NOW: an inner call-time wrapper (the erase gate's snapshot) reads at queue time

            async def run():
                try:
                    return await inner
                finally:
                    turn_release(token)
            return run()
        return call
    return wrap
