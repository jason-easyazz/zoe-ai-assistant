"""session_continuity — a chat request with NO session id continues the user's last chat.

The bug (live 2026-10-05): the estate's home ask-box posts ``/api/chat/`` without a
``session_id``, and ``routers/chat.py`` answered every such request with a fresh
``web_<8hex>`` session. So "i live here, its good" could not attach to the answer it
was replying to, and a follow-up "where do i live" found nothing: six messages in
twelve seconds were six sessions of one turn each.

``ZOE_STICKY_SESSION`` (default ON; ``ZOE_STICKY_SESSION_MINUTES`` = 20) makes the
server reuse the caller's most recent web-minted session when its last activity is
within the window, and mint only when there is none (or it is stale).

Why default ON: it only ever fires when the client sent NO id — a case that today
yields a guaranteed context loss — so nothing that worked can change. An explicit
``session_id`` is returned untouched (every harness, Telegram, voice, the desktop chat
page and the orb pass one), and the rule is scoped hard:

* never across users (the lookup is ``user_id = ?``),
* never for the shared identities (``guest`` / ``voice-guest`` / blank — many different
  people hide behind those, so "their last session" is someone else's conversation),
* never across channels (only ``web_``-prefixed ids — what this module itself mints —
  and only for the default ``chat`` channel; telegram / voice sessions are other ids),
* never an old conversation (the window), and
* ``POST /sessions/`` ("New Chat") still mints unconditionally — that is the user
  explicitly asking for a fresh one.

Set ``ZOE_STICKY_SESSION=0`` to return to mint-per-request.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

MINT_PREFIX = "web_"
DEFAULT_WINDOW_MINUTES = 20.0
_SHARED_IDENTITIES = frozenset({"", "guest", "voice-guest", "default", "anonymous"})


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_STICKY_SESSION", True)


def window() -> timedelta:
    from typed_env import env_float

    minutes = env_float("ZOE_STICKY_SESSION_MINUTES", DEFAULT_WINDOW_MINUTES)
    return timedelta(minutes=minutes if minutes > 0 else DEFAULT_WINDOW_MINUTES)


def mint() -> str:
    return f"{MINT_PREFIX}{uuid.uuid4().hex[:8]}"


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


async def recent_session_id(user_id: str, *, now: Optional[datetime] = None, db=None) -> Optional[str]:
    """The user's most recently active ``web_`` session if it is inside the window."""
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
    best: Optional[tuple[datetime, str]] = None
    for row in rows or []:
        try:
            sid, ts = row["id"], parse_ts(row["updated_at"])
        except (KeyError, TypeError, IndexError):
            sid, ts = row[0], parse_ts(row[1])
        if ts is not None and (best is None or ts > best[0]):
            best = (ts, str(sid))
    if best is None:
        return None
    age = now - best[0]
    # A timestamp in the future (clock skew) counts as fresh; only a stale one is refused.
    return best[1] if age <= window() else None


async def resolve_session_id(
    body: dict,
    user_id: str,
    *,
    channel: str = "chat",
    now: Optional[datetime] = None,
    db=None,
) -> str:
    """The session id for a request: the caller's explicit one, else the sticky one,
    else a freshly minted ``web_<8hex>``. NEVER raises (a lookup failure mints)."""
    raw = body.get("session_id") if isinstance(body, dict) else None
    if isinstance(raw, str):
        if raw.strip():
            return raw
    elif raw is not None and raw != "":
        return raw  # a non-string id is the caller's own business, exactly as before
    uid = (user_id or "").strip()
    if enabled() and channel == "chat" and uid.lower() not in _SHARED_IDENTITIES:
        try:
            sid = await recent_session_id(uid, now=now, db=db)
            if sid:
                logger.info("SESSION_STICKY user=%s session=%s", uid, sid)
                return sid
        except Exception as exc:  # noqa: BLE001 — continuity is optional; the turn is not
            logger.debug("session_continuity: lookup failed, minting: %r", exc)
    return mint()
