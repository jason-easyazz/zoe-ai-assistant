"""reply_ledger - the "what did each reply stand on" ledger, persisted (BM5 follow-up; ``ZOE_PROVENANCE_PERSIST``, default ON).

Why. ``memory_provenance`` keeps the id-only record of each reply in process memory, so a zoe-data restart (or a reply that came
in on another worker) answered "why did you say that?" with "I don't have a record of what that answer came from"
(``docs/knowledge/open-problems.md``, BM5). Honest, but a restart is routine and the owner asked a fair question.

What. One small row per reply in ``reply_sources`` (migration 0044), keyed by (user, session, reply id):

  * IDS and labels only - the memory-row / owner-turn ids the reply restated, the tool NAMES the turn called, the tier and domain of
    a direct reply, how many rows the packet held. NEVER the reply, a quote or the user's words: ``provenance_answers.explain``
    re-reads each row at answer time, so a note forgotten since the reply is not quoted from this table ("forgotten means forever").
  * Per CONVERSATION. The read is by (user, session): a voice session never explains a chat reply and a chat never explains the
    voice. A turn with no session id writes nothing and reads nothing - no key, no claim.
  * Off the record and distress turns are never written (the existing primitives: ``memory_provenance.is_off_record`` /
    ``reply_is_off_record``, which fold in ``distress_handoff``), and a guest / unregistered id keeps no state at all.
  * Short retention (``RETENTION_S`` = 3 days), purged on write at most every ``PURGE_EVERY_S``.

The write is fire-and-forget (a task on the running loop): a reply never waits for it and a failed write costs only the persisted
record. The read is used ONLY when the in-process ledger has nothing for this conversation AND this is the conversation's first
turn since the process started (``memory_provenance.first_turn_in_session``) - the restart signature. A lane that simply recorded
nothing mid-session still answers "I can't tell", never the previous reply's sources.

Everything here fails soft: a missing table (migration 0044 not applied), a DB blip or a bad row returns "no record".
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import memory_provenance as mp
from typed_env import env_bool

logger = logging.getLogger(__name__)

ENV = "ZOE_PROVENANCE_PERSIST"
#: a persisted reply row is kept this long
RETENTION_S = 3 * 86400.0
#: a persisted reply explains "why did you say that?" for this long after it was made (the in-process ledger's own window is
#: ``mp.REPLY_TTL_S``; a restart takes a minute or two, so the persisted one is allowed a little longer)
READ_WINDOW_S = 1800.0
PURGE_EVERY_S = 600.0
MAX_TOOLS = 12
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

_pending: set = set()
_last_purge = 0.0


def enabled() -> bool:
    """``ZOE_PROVENANCE_PERSIST`` - default ON, per-call read; needs the BM5 feature on too (``ZOE_MEMORY_PROVENANCE_ANSWERS``)."""
    return mp.enabled() and env_bool("ZOE_PROVENANCE_PERSIST", True)     # a LITERAL name: tools/audit/flag_inventory.py reads call sites


def new_reply_id() -> str:
    return uuid.uuid4().hex[:16]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime(_TS_FMT)


def tool_names(deltas: Sequence[str]) -> tuple:
    """The distinct tool NAMES in a stream's ``__TOOL__:`` sentinels (``phase`` start / args carry ``name``), in call order. Pure;
    a malformed sentinel is skipped. Names only - never an argument or a result."""
    out: list = []
    for d in deltas:
        if not isinstance(d, str) or not d.startswith("__TOOL__:"):
            continue
        try:
            tc = json.loads(d[len("__TOOL__:"):])
        except Exception:  # noqa: BLE001
            continue
        if isinstance(tc, dict) and tc.get("phase") in ("start", "args", None):
            name = str(tc.get("name") or "").strip()[:60]
            if name and name not in out:
                out.append(name)
    return tuple(out[:MAX_TOOLS])


def skip_reason(user_id: str, session_id: str, message: str = "") -> str:
    """Why this reply must NOT be written ('' = write it). The off-record / distress walls and the no-key wall."""
    if not enabled():
        return "off"
    if not (session_id or "").strip():
        return "no_session"
    if not mp.is_tracked(user_id):          # a guest / unregistered id keeps nothing
        return "untracked"
    try:
        if message and mp._distress(message):
            return "distress"
        if message and mp.is_off_record(user_id, message):
            return "off_record"
        if mp.reply_is_off_record(user_id):
            return "off_record"
    except Exception:  # noqa: BLE001 - when the walls cannot be asked, nothing is written
        return "wall_error"
    return ""


def _to_row(user_id: str, rec: "mp.ReplyRecord") -> tuple:
    return (user_id, rec.session_id, rec.reply_id, float(rec.ts), rec.kind, rec.tier or "", rec.domain or "", int(rec.served or 0),
            json.dumps([[s.kind, s.id, float(s.said_at or 0.0)] for s in rec.sources]),
            json.dumps(list(rec.extra)), json.dumps(list(rec.tools)), _iso(rec.ts))


def _from_row(row: Any) -> Optional["mp.ReplyRecord"]:
    try:
        reply_id, ts, kind, tier, domain, served, sources, extra, tools = (row[i] for i in range(9))
        srcs = tuple(mp.Source(str(k), str(i), float(s or 0.0)) for k, i, s in json.loads(sources or "[]"))
        return mp.ReplyRecord(seq=0, ts=float(ts), kind=str(kind), tier=str(tier or ""), domain=str(domain or ""), sources=srcs,
                              served=int(served or 0), session_id="", extra=tuple(json.loads(extra or "[]")),
                              reply_id=str(reply_id), tools=tuple(json.loads(tools or "[]")))
    except Exception:  # noqa: BLE001
        return None


async def write(user_id: str, rec: "mp.ReplyRecord") -> bool:
    """Insert one reply row (idempotent on the key). False on any failure. Never raises."""
    global _last_purge
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            await db.execute(
                "INSERT INTO reply_sources (user_id, session_id, reply_id, ts, kind, tier, domain, served, sources, extra, tools, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, session_id, reply_id) DO NOTHING",
                _to_row(user_id, rec))
            now = float(rec.ts)      # the ledger's own clock (injectable), so a purge can never take the row it just wrote
            if now - _last_purge >= PURGE_EVERY_S:
                _last_purge = now
                await db.execute("DELETE FROM reply_sources WHERE created_at < ?", (_iso(now - RETENTION_S),))
            await db.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - the reply is already out; a failed write costs only the persisted record
        logger.debug("reply_ledger: write failed (%s)", type(exc).__name__)
        return False


def schedule(user_id: str, rec: "mp.ReplyRecord", message: str = "") -> bool:
    """Persist ``rec`` in the background if it may be persisted. True when a write was scheduled. Never raises, never blocks."""
    try:
        reason = skip_reason(user_id, rec.session_id, message)
        if reason:
            if reason in ("off_record", "distress"):
                logger.info("REPLY_LEDGER skipped reason=%s", reason)
            return False
        loop = asyncio.get_running_loop()
        task = loop.create_task(write(user_id, rec))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        return True
    except RuntimeError:        # no running loop (a sync caller / a test): nothing to schedule on
        return False
    except Exception:  # noqa: BLE001
        return False


async def flush() -> None:
    """Await the writes in flight (tests; a clean shutdown)."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def read_latest(user_id: str, session_id: str, *, now: Optional[float] = None) -> Optional["mp.ReplyRecord"]:
    """The newest persisted reply of THIS conversation, within ``READ_WINDOW_S``, or None. The key is (user, session): an empty
    session id reads nothing. Never raises."""
    try:
        sid = (session_id or "").strip()
        if not enabled() or not sid or not mp.is_tracked(user_id):
            return None
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            cur = await db.execute(
                "SELECT reply_id, ts, kind, tier, domain, served, sources, extra, tools FROM reply_sources "
                "WHERE user_id = ? AND session_id = ? ORDER BY ts DESC LIMIT 1", (user_id, sid))
            row = await cur.fetchone()
        if row is None:
            return None
        rec = _from_row(row)
        t = time.time() if now is None else float(now)
        if rec is None or t - rec.ts > READ_WINDOW_S or rec.ts > t + 60:
            return None
        return dataclasses.replace(rec, session_id=sid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("reply_ledger: read failed (%s)", type(exc).__name__)
        return None


async def forget_user(user_id: str) -> int:
    """Drop a user's persisted replies (the right-to-be-forgotten path). Returns the row count removed; 0 on failure."""
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            cur = await db.execute("DELETE FROM reply_sources WHERE user_id = ?", (user_id,))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0
