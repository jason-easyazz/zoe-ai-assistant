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
    """The ``memory_forgotten`` table over ``db_pool`` (portable SQL, so the tests run these statements on SQLite)."""

    async def put(self, user_id: str, at: str, actor: str) -> None:
        from db_pool import get_db_ctx  # type: ignore[import]
        async with get_db_ctx() as db:
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
        from db_pool import get_db_ctx  # type: ignore[import]
        async with get_db_ctx() as db:
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


async def watermark(user_id: str) -> Optional[datetime]:
    """When ``user_id`` was last erased, or None (never). RAISES ``Unavailable`` when the store cannot be read."""
    try:
        raw = await get_backend().get(str(user_id))
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
                     strict: bool = True) -> list:
    """``rows`` without the turns of an erased member said at or before their watermark. ``owner`` is the member id (or a function
    of the row, for a batch of several members' rows); ``at`` gives the row's time. Rows with no owner are kept (they are no
    member's). An unreadable watermark: ``strict`` raises ``Unavailable`` (the caller defers the pass), otherwise that member's
    rows are dropped for this pass. Order is kept."""
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
                marks[uid] = await watermark(uid)
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
