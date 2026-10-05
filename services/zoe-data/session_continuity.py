"""session_continuity — a chat request with NO session id continues the user's last ask.

The bug (live 2026-10-05): the estate's home ask-box posts ``/api/chat/`` without a
``session_id``, and ``routers/chat.py`` answered every such request with a fresh
``web_<8hex>`` session. So "i live here, its good" could not attach to the answer it
was replying to, and a follow-up "where do i live" found nothing: six messages in
twelve seconds were six sessions of one turn each.

``ZOE_STICKY_SESSION`` (default ON; ``ZOE_STICKY_SESSION_MINUTES`` = 20) gives id-less
requests their OWN namespace: they are minted as ``ask_<8hex>`` and a later id-less
request reuses the user's most recent ``ask_`` session when its last activity is inside
the window. The namespace is the channel boundary — this module only ever reuses rows
it minted itself:

* NEVER a ``web_`` session. ``POST /api/chat/sessions/`` ("New Chat", the desktop chat
  page) mints ``web_`` ids, and an open desktop conversation must not absorb the
  ask-box's questions, the music page's fire-and-forget commands or the planner's
  natural-language input (and, because ``locked_chat_stream`` serialises a session, a
  second concurrent writer there would be rejected with ``session_busy``),
* never across users (the lookup is ``user_id = ?``),
* never for the shared identities (``guest`` / ``voice-guest`` / blank — many different
  people hide behind those),
* never for a non-``chat`` channel tag, and never an old conversation (the window),
* never a session whose turn is still in flight (``busy`` callback — the chat route
  passes its per-session lock probe): a second id-less request that arrives while the
  first is still answering gets a fresh ``ask_`` session instead of ``session_busy``.

An explicit ``session_id`` is returned untouched (every harness, Telegram, voice, the
desktop chat page and the orb pass one), and ``POST /sessions/`` still mints ``web_``.

Known and accepted: two ask-box tabs / panels of the same user inside the window share
one ``ask_`` transcript (the busy fallback keeps them from colliding mid-turn). The
Pi voice daemon never reaches this module — it posts only ``/api/voice/*``.

Set ``ZOE_STICKY_SESSION=0`` to return to mint-per-request (the minted id is then
still ``ask_``-prefixed, never ``web_``).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

MINT_PREFIX = "ask_"
DEFAULT_WINDOW_MINUTES = 20.0
_SHARED_IDENTITIES = frozenset({"", "guest", "voice-guest", "default", "anonymous"})


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_STICKY_SESSION", True)


def window() -> timedelta:
    from typed_env import env_float

    minutes = env_float("ZOE_STICKY_SESSION_MINUTES", DEFAULT_WINDOW_MINUTES)
    return timedelta(minutes=minutes if minutes > 0 else DEFAULT_WINDOW_MINUTES)


LEGACY_PREFIX = "web_"


def mint() -> str:
    """``ask_<8hex>`` — or the legacy ``web_<8hex>`` with ZOE_STICKY_SESSION off, which is
    exactly what the route minted before this module existed."""
    return f"{MINT_PREFIX if enabled() else LEGACY_PREFIX}{uuid.uuid4().hex[:8]}"


def parse_ts(value: Any) -> Optional[datetime]:
    """An aware UTC datetime from the formats ``chat_sessions.updated_at`` holds
    (``2026-10-05 00:08:23.595067+00``, ``…+00:00``, ``…T…Z``, naive = UTC), else None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value or "").strip()
    if not s:
        return None
    s = s.replace(" ", "T", 1)
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    s = re.sub(r"([+-]\d\d)$", r"\1:00", s)  # "+00" → "+00:00" (py3.10 fromisoformat)
    m = re.match(r"^(.*?T[\d:]+)(\.\d+)?(.*)$", s)
    if m:  # fromisoformat (3.10) wants exactly 3 or 6 fractional digits
        frac = (m.group(2) or "")[1:]
        s = m.group(1) + ("." + (frac + "000000")[:6] if frac else "") + m.group(3)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def recent_session_id(
    user_id: str,
    *,
    now: Optional[datetime] = None,
    db=None,
    busy: Optional[Callable[[str], bool]] = None,
) -> Optional[str]:
    """The user's most recently active ``ask_`` session inside the window whose turn is not
    in flight (``busy(sid)`` false), else None."""
    now = now or datetime.now(timezone.utc)
    sql = (
        "SELECT id, updated_at FROM chat_sessions "
        "WHERE user_id = ? AND substr(id, 1, 4) = ? "
        "ORDER BY updated_at DESC LIMIT 5"
    )
    params = (user_id, MINT_PREFIX)
    if db is not None:
        rows = await (await db.execute(sql, params)).fetchall()
    else:
        from db_pool import get_db_ctx  # type: ignore[import]

        async with get_db_ctx() as conn:
            rows = await (await conn.execute(sql, params)).fetchall()
    fresh: list[tuple[datetime, str]] = []
    for row in rows or []:
        try:
            sid, ts = row["id"], parse_ts(row["updated_at"])
        except (KeyError, TypeError, IndexError):
            sid, ts = row[0], parse_ts(row[1])
        # A timestamp in the future (clock skew) counts as fresh; only a stale one is refused.
        if ts is not None and now - ts <= window():
            fresh.append((ts, str(sid)))
    for _ts, sid in sorted(fresh, reverse=True):
        if busy is not None and busy(sid):
            logger.info("SESSION_STICKY_BUSY session=%s — skipped", sid)
            continue
        return sid
    return None


async def resolve_session_id(
    body: dict,
    user_id: str,
    *,
    channel: str = "chat",
    now: Optional[datetime] = None,
    db=None,
    busy: Optional[Callable[[str], bool]] = None,
) -> str:
    """The session id for a request: the caller's explicit one, else the sticky ``ask_``
    one (not busy), else a freshly minted ``ask_<8hex>``. NEVER raises (a lookup failure
    mints)."""
    raw = body.get("session_id") if isinstance(body, dict) else None
    if isinstance(raw, str):
        if raw.strip():
            return raw
    elif raw is not None and raw != "":
        return raw  # a non-string id is the caller's own business, exactly as before
    uid = (user_id or "").strip()
    if enabled() and channel == "chat" and uid.lower() not in _SHARED_IDENTITIES:
        try:
            sid = await recent_session_id(uid, now=now, db=db, busy=busy)
            if sid:
                logger.info("SESSION_STICKY user=%s session=%s", uid, sid)
                return sid
        except Exception as exc:  # noqa: BLE001 — continuity is optional; the turn is not
            logger.debug("session_continuity: lookup failed, minting: %r", exc)
    return mint()
