"""memory_episodes - episode objects: a stretch of one member's conversation as a POINTER to their turns, never a copy of them.

Why (Samantha initiative item 15, lane C section 2 "Episodic"; the prerequisite of chapters, E9): recall is turn-shaped. Nothing
says "these twelve turns on Tuesday evening were one conversation", so nothing can cite one, count them, or later assemble a month
from them. The lane's rule for every new layer is that it is a VIEW over pointers: no copied text, so a forget stays complete.

What. An ``Episode`` is ``{id, user_id, turn_ids, session_ids, started_at, ended_at}`` - message ids and times only. Consecutive
user turns of ONE member belong to one episode while the gap between them is at most ``gap_s`` (default 45 minutes,
``ZOE_MEMORY_EPISODE_GAP_S``); a longer silence starts the next one. The id is derived from the member and the first turn's id, so
the same turns always give the same id.

Nothing is stored. ``build`` is a pure function of the turn pointers; ``load_turn_pointers`` reads ids and times from
``chat_messages`` (never ``content``) with the same ownership rule the night jobs use (``user_filters.message_owner_expr``) and
off-the-record turns excluded. A turn that is erased is simply not read next time, so its episode shrinks or disappears; a caller
that holds an ``Episode`` across a forget re-checks it with ``resolve`` against a fresh read (ids, sessions and times are rebuilt
from the surviving turns; an episode with no turn left is ``None``). There is no derived store to erase: forget-complete by construction.

Flag ``ZOE_MEMORY_EPISODES`` (default OFF, per-call read) gates the database read (``load``): off = no I/O, ``[]``. The pure
functions need no flag.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

ENV = "ZOE_MEMORY_EPISODES"
GAP_ENV = "ZOE_MEMORY_EPISODE_GAP_S"
DEFAULT_GAP_S = 45 * 60
#: the read is bounded: one member's turns over the window, at most this many
MAX_TURNS = 5000


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool(ENV, False)


def gap_s() -> float:
    from typed_env import env_float

    g = env_float(GAP_ENV, 2700.0)
    return g if g > 0 else float(DEFAULT_GAP_S)


@dataclass(frozen=True)
class TurnPointer:
    turn_id: str
    session_id: str
    at: float          # epoch seconds


@dataclass(frozen=True)
class Episode:
    id: str
    user_id: str
    turn_ids: "tuple[str, ...]"
    session_ids: "tuple[str, ...]"
    started_at: float
    ended_at: float

    @property
    def n_turns(self) -> int:
        return len(self.turn_ids)

    def as_dict(self) -> dict:
        return {"id": self.id, "user_id": self.user_id, "turn_ids": list(self.turn_ids), "session_ids": list(self.session_ids),
                "started_at": self.started_at, "ended_at": self.ended_at, "n_turns": self.n_turns}


def episode_id(user_id: str, first_turn_id: str) -> str:
    return "ep_" + hashlib.sha256(f"{user_id}\x00{first_turn_id}".encode()).hexdigest()[:16]


def _make(user_id: str, turns: "Sequence[TurnPointer]") -> Episode:
    sessions: "list[str]" = []
    for t in turns:
        if t.session_id and t.session_id not in sessions:
            sessions.append(t.session_id)
    return Episode(episode_id(user_id, turns[0].turn_id), user_id, tuple(t.turn_id for t in turns), tuple(sessions),
                   turns[0].at, turns[-1].at)


def build(user_id: str, pointers: Iterable[TurnPointer], *, gap: Optional[float] = None) -> "list[Episode]":
    """The member's turns grouped into episodes, oldest first. Duplicate turn ids count once; order is by time, then id. Pure."""
    g = gap_s() if gap is None else float(gap)
    seen: "set[str]" = set()
    turns: "list[TurnPointer]" = []
    for p in pointers:
        if p.turn_id and p.turn_id not in seen:
            seen.add(p.turn_id)
            turns.append(p)
    turns.sort(key=lambda t: (t.at, t.turn_id))
    out: "list[Episode]" = []
    run: "list[TurnPointer]" = []
    for t in turns:
        if run and t.at - run[-1].at > g:
            out.append(_make(user_id, run))
            run = []
        run.append(t)
    if run:
        out.append(_make(user_id, run))
    return out


def episode_of(turn_id: str, episodes: Iterable[Episode]) -> Optional[Episode]:
    """The episode a turn belongs to, or None. Pure."""
    for e in episodes:
        if turn_id in e.turn_ids:
            return e
    return None


def resolve(episode: Episode, alive: Iterable[TurnPointer]) -> Optional[Episode]:
    """The episode rebuilt from only its turns that still exist (``alive`` = the pointers a fresh read returned): ids, sessions and
    bounds all come from the survivors, so a forgotten turn leaves no session id or time behind. None when none is left. Pure."""
    mine = set(episode.turn_ids)
    keep = sorted({p.turn_id: p for p in alive if p.turn_id in mine}.values(), key=lambda t: (t.at, t.turn_id))
    if not keep:
        return None
    rebuilt = _make(episode.user_id, keep)
    return episode if rebuilt == episode else rebuilt


# ── the read (ids and times only) ─────────────────────────────────────────────

def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, datetime.datetime):
        d = value
    else:
        s = str(value or "").strip().replace(" ", "T", 1)
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if len(s) >= 3 and s[-3] in "+-" and s[-2:].isdigit() and ":" not in s[-3:]:
            s += ":00"          # Postgres NOW()::TEXT ends "+00"
        try:
            d = datetime.datetime.fromisoformat(s)
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


async def load_turn_pointers(user_id: str, start: datetime.datetime, end: datetime.datetime, *, db: Any = None) -> "list[TurnPointer]":
    """The member's own user turns in ``[start, end)`` as pointers: id, session and time. ``content`` is never selected. Raises on a
    failed read (the caller decides; a quiet window is ``[]``)."""
    from memory_provenance import off_record_sql
    from user_filters import message_owner_expr

    sql = ("SELECT cm.id, cm.session_id, cm.created_at FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id "
           "WHERE " + message_owner_expr() + " = ? AND cm.role = 'user' AND " + off_record_sql("cm") + " "
           "AND cm.created_at::timestamptz >= ?::timestamptz AND cm.created_at::timestamptz < ?::timestamptz "
           "ORDER BY cm.created_at ASC LIMIT " + str(MAX_TURNS))
    params = (user_id, start, end)
    if db is not None:
        rows = await (await db.execute(sql, params)).fetchall()
    else:
        from db_pool import get_db_ctx

        async with get_db_ctx() as conn:
            rows = await (await conn.execute(sql, params)).fetchall()
    out: "list[TurnPointer]" = []
    for r in rows or ():
        at = _epoch(r[2])
        if r[0] and at is not None:
            out.append(TurnPointer(str(r[0]), str(r[1] or ""), at))
    return out


async def load(user_id: str, start: datetime.datetime, end: datetime.datetime, *, db: Any = None,
               force: bool = False) -> "list[Episode]":
    """The member's episodes in the window (flag-gated: off = ``[]`` and no read; ``force`` = the report's explicit run). Fail-open:
    a failed read is ``[]`` and one WARNING line without ids."""
    if not (force or enabled()) or not user_id:
        return []
    try:
        return build(user_id, await load_turn_pointers(user_id, start, end, db=db))
    except Exception as exc:  # noqa: BLE001 - a view: nothing depends on it yet
        logger.warning("MEMORY_EPISODES read failed kind=%s", type(exc).__name__)
        return []
