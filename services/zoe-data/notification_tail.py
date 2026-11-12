"""The spoken side of household notifications: one line at the END of a turn the member started, and "read them".

Flag ``ZOE_NOTIFICATIONS`` (``notifications_store.enabled``; default ON). Off = nothing is noted, nothing is appended (the brain
stream is not even wrapped), the tier returns None before any work. Spec: ``docs/knowledge/notifications.md``.

The tail copies the transcript-check contract (``transcript_check.prepare_tail``) and adds the do-not-overwhelm caps:
  * only on a turn whose speaker was VERIFIED (never a guest, an unverified voice, a typed or PIN-only turn), after the reply
    finished with real text that does not end in a question, never on an emotional turn, never in quiet hours;
  * at most ONE appended line per reply across all tails: the transcript check goes first and wins (its pending ask blocks ours);
  * at most once per ``TAIL_EVERY_S`` (4 h) and ``TAIL_MAX_PER_DAY`` (3) a household day per member, and only when at least one unread
    notification has not been announced yet (the same set is never announced twice). The time and the day's count persist in one
    ``app_settings`` row per member (``notifications_store.tail_key``), so a restart does not reset them; the "already announced" set
    is in memory (after a restart the same set may be announced once more, still within the caps).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import weakref
from datetime import datetime
from typing import AsyncIterator, Optional

import notifications_store as store
import user_erase_gate as erase_gate

logger = logging.getLogger(__name__)

TIER = "notifications"
TURN_TTL_S = 180.0
ASK_TTL_S = 120.0
TAIL_EVERY_S = 4 * 3600.0
TAIL_MAX_PER_DAY = 3
SPOKEN_MAX = 3
PRIVATE_REPLY = "That's private. Say it again so I know it's you, or unlock with your PIN."
LATER_REPLY = "Okay, they'll wait on the panel."


class _Turn:
    __slots__ = ("panel_id", "scope_user", "verified", "at")

    def __init__(self, panel_id: str, scope_user: Optional[str], verified: bool, at: float):
        self.panel_id, self.scope_user, self.verified, self.at = panel_id, scope_user, verified, at


_TURNS: dict[str, _Turn] = {}
_PENDING: dict[str, tuple[str, float]] = {}       # session -> (member, asked_at): "read them" in the next ASK_TTL_S answers the tail
_LAST: dict[str, float] = {}                       # member -> time of the last tail (persisted, see _load / _save)
_DAY: dict[str, tuple[str, int]] = {}              # member -> (household day, tails said that day) (persisted)
_LOADED: set = set()                               # members whose persisted caps were read in this process
#: per-member lock, one table per event loop (an ``asyncio.Lock`` binds to a loop): eligibility check + confirmation are one step
_LOCKS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _member_lock(member: str) -> asyncio.Lock:
    table = _LOCKS.setdefault(asyncio.get_running_loop(), {})
    lock = table.get(member)
    if lock is None:
        lock = table[member] = asyncio.Lock()
    return lock


def _clock() -> float:
    return time.time()             # wall clock: the caps persist across a restart (``app_settings``), so they need a shared time base


def _now() -> datetime:
    from time_utils import zoe_timezone

    return datetime.now(zoe_timezone())


def note_voice_turn(session_id: str, panel_id: str, *, scope_user: Optional[str], verdict: Optional[bool]) -> None:
    """Called by the voice lane once per spoken turn after its identity is resolved. Only verdict True counts as verified. Never raises."""
    try:
        if not store.enabled() or not session_id:
            return
        now = _clock()
        _TURNS[str(session_id)] = _Turn(str(panel_id or ""), scope_user or None, verdict is True, now)
        for s in [s for s, t in _TURNS.items() if now - t.at > TURN_TTL_S]:
            _TURNS.pop(s, None)
    except Exception:  # noqa: BLE001
        pass


def verified_member(session_id: str) -> Optional[str]:
    t = _TURNS.get(str(session_id or ""))
    if t is None or not t.verified or not t.scope_user or _clock() - t.at > TURN_TTL_S:
        return None
    return store._account(t.scope_user)


def tail_line(n: int) -> str:
    if n == 1:
        return "You also have a notification. You can check it on the panel, or I can read it to you now."
    return f"You also have {store.count_words(n)}. You can check them on the panel, or I can read them to you now."


def _quiet() -> bool:
    try:
        from proactive.pull import _quiet as q

        return q(_now())
    except Exception:  # noqa: BLE001 - cannot tell = quiet (fail closed)
        return True


def _other_tail_pending(session_id: str) -> bool:
    """The transcript check asked on this session (or is waiting for its answer): one tail per reply, and it wins."""
    try:
        import transcript_check

        return str(session_id) in transcript_check._PENDING
    except Exception:  # noqa: BLE001
        return False


async def prepare_tail(user_id: str, session_id: str, user_text: str, reply_text: str) -> Optional[tuple[str, str, list, int]]:
    """``(line, member, ids, erase generation)`` to append, or None. The generation is taken BEFORE any read, so ``confirm_tail``
    drops the line when the member was erased in between. Every refusal is a plain None. Never raises."""
    try:
        if not store.enabled():
            return None
        member = verified_member(session_id)
        if not member:
            return None
        gen = erase_gate.generation(member)
        if not reply_text.strip() or reply_text.rstrip().endswith("?"):
            return None
        if _quiet():
            return None
        import transcript_check

        if transcript_check._is_emotional(user_text, member):
            return None
        if transcript_check.classify_reply(user_text) or str(session_id) in _PENDING or _other_tail_pending(session_id):
            return None
        if not await _load(member):
            return None
        if _clock() - _LAST.get(member, -TAIL_EVERY_S) < TAIL_EVERY_S:
            return None
        day = _now().date().isoformat()
        d, said = _DAY.get(member, (day, 0))
        if d == day and said >= TAIL_MAX_PER_DAY:
            return None
        await store.refresh(member)
        rows = await store.unread(member, limit=50)
        ids = [r["id"] for r in rows]
        if not rows or not (set(ids) - store.ANNOUNCED.get(member, set())):
            return None
        return tail_line(len(rows)), member, ids, gen
    except Exception as exc:  # noqa: BLE001
        logger.debug("notification_tail: skipped (%s)", exc.__class__.__name__)
        return None


async def _load(member: str) -> bool:
    """The member's persisted caps (one ``app_settings`` row, ``notifications_store.tail_key``), read once per process. No row = no tail
    yet. FAIL CLOSED: an unreadable row returns False (no tail this turn) and is read again next time."""
    if member in _LOADED:
        return True
    try:
        async with store._db() as db:
            cur = await db.execute("SELECT value FROM app_settings WHERE key = ?", (store.tail_key(member),))
            row = await cur.fetchone()
        v = json.loads(row[0]) if row else {}
        if v.get("at"):
            _LAST[member] = max(_LAST.get(member, 0.0), float(v["at"]))
        if v.get("day") and member not in _DAY:
            _DAY[member] = (str(v["day"]), int(v.get("said") or 0))
    except Exception as exc:  # noqa: BLE001
        logger.info("NOTIFY tail=0 reason=caps_unreadable (%s)", exc.__class__.__name__)
        return False
    _LOADED.add(member)
    return True


async def _save(member: str, at: float, day: str, said: int, gen: int) -> bool:
    """Persist the caps under the member's erase gate (held through the commit): an erase since ``gen`` (taken before the tail's
    reads) drops the save, so the forgotten member's row is never recreated and nothing is said. Forgotten means forever."""
    value = json.dumps({"at": at, "day": day, "said": said})
    try:
        async with erase_gate.writing(member, gen) as live:
            if not live:
                logger.info("NOTIFY tail=0 reason=erased_since_prepared")
                return False
            async with store._db() as db:
                await db.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET "
                                 "value = excluded.value, updated_at = excluded.updated_at",
                                 (store.tail_key(member), value, _now().isoformat()))
                await db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("NOTIFY tail=0 reason=caps_unsaved (%s)", exc.__class__.__name__)
        return False


async def confirm_tail(member: str, session_id: str, ids: list, gen: int) -> bool:
    """Count the line against the caps BEFORE it is said: the new caps are saved first, and only a saved line may be spoken (False =
    do not say it; also when the member was erased since ``gen``, the generation ``prepare_tail`` took). Then the "read them"
    window opens."""
    now = _clock()
    day = _now().date().isoformat()
    d, said = _DAY.get(member, (day, 0))
    said = (said if d == day else 0) + 1
    if not await _save(member, now, day, said, gen):
        return False
    _LAST[member] = now
    _DAY[member] = (day, said)
    store.ANNOUNCED.setdefault(member, set()).update(ids)
    _PENDING[str(session_id)] = (member, now)
    logger.info("NOTIFY tail=1 count=%d", len(ids))
    return True


async def _tail(user_id: str, session_id: str, user_text: str, reply_text: str) -> Optional[str]:
    """Prepare + confirm as ONE step per member (``_member_lock``): two overlapping turns of the same member (two panels) cannot both
    pass the caps before either is counted. The line to say, or None. Never raises."""
    member = verified_member(session_id)
    if not member:
        return None
    try:
        async with _member_lock(member):
            got = await prepare_tail(user_id, session_id, user_text, reply_text)
            if got and await confirm_tail(got[1], session_id, got[2], got[3]):      # counted (and saved) first: an unsaved line is never said
                return got[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("notification_tail: skipped (%s)", exc.__class__.__name__)
    return None


async def append_stream(turn: AsyncIterator[str], user_text: str, user_id: str, session_id: str, *, skip: tuple = ()) -> AsyncIterator[str]:
    """Wrap the brain's reply stream: the reply goes out untouched; only when it finished with real text may ONE line follow. A consumer
    that stops early (barge-in) gets no line and nothing is counted."""
    reply: list[str] = []
    try:
        async for d in turn:
            if not d.startswith(("__TOOL__:", "__THINKING__:")) and d.strip() and d not in skip:
                reply.append(d)
            yield d
        text = "".join(reply)
        line = await _tail(user_id, session_id, user_text, text) if text.strip() else None
        if line:
            yield " " + line
    finally:
        # A consumer that stops early (barge-in, disconnect, cancel) closes THIS generator: the inner turn is closed deterministically, as
        # every wrapper in the chain does (clean_goodbye, reply_polish), so the Flue submission is aborted and its logs run.
        aclose = getattr(turn, "aclose", None)
        if aclose is not None:
            await aclose()


async def append_to_reply(reply: str, user_text: str, user_id: str, session_id: str, *, replay: bool = False, dry: bool = False) -> str:
    """The non-streaming twin (deterministic tiers). ``replay`` (an instrument / replay turn) or ``dry`` (nothing may be recorded): the
    reply is returned untouched and nothing is counted, saved or opened - the streaming path's ``replay_isolation`` rule."""
    if not store.enabled() or replay or dry:
        return reply
    line = await _tail(user_id, session_id, user_text, reply or "")
    return f"{reply.rstrip()} {line}" if line else reply


# -- "read them" ---------------------------------------------------------------------------------------------------------------------
_READ = re.compile(r"^\W*(?:(?:yes|yeah|ok|okay|sure|please|go on|go ahead)[\s,]+)*(?:read|tell)\s+(?:them|it|me)(?:\s+(?:to\s+me|out|now|please))*\W*$",
                   re.I)


def _classify(text: str) -> Optional[str]:
    if _READ.match((text or "").strip()):
        return "yes"
    import transcript_check

    return transcript_check.classify_reply(text)


def read_out(rows: list) -> str:
    """Titles only, at most ``SPOKEN_MAX``; a sensitive one is counted, never read."""
    public = [r for r in rows if not store.is_sensitive(r)]
    private = len(rows) - len(public)
    said = [r["title"].rstrip(". ") for r in public[:SPOKEN_MAX] if r["title"].strip()]
    parts = []
    if said:
        parts.append((said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]) + ".")
    more = len(public) - len(said)
    if more > 0:
        parts.append(f"And {more} more on the panel.")
    if private:
        parts.append("One is private, it is on the panel." if private == 1 else f"{private} are private, they are on the panel.")
    return " ".join(parts) or "Nothing new now."


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None, extra_ctx: Optional[dict] = None,
               allow_writes: bool = True):
    """Fast-tier hook (voice only): takes a turn ONLY when it answers the tail Zoe just said. Never raises."""
    try:
        if not store.enabled() or (channel or "").strip().lower() != "voice" or allow_writes is False:
            return None
        sid = str(session_id or "")
        pend = _PENDING.get(sid)
        if not pend:
            return None
        _PENDING.pop(sid, None)
        if _clock() - pend[1] > ASK_TTL_S:
            return None
        kind = _classify(text)
        if not kind:
            return None                     # anything else is a new command: the notifications stay on the panel
        import expert_dispatch as _xd

        member = verified_member(sid)
        if not member or member != pend[0]:
            return _xd.DispatchResult(domain="notifications", reply=PRIVATE_REPLY, intent="notifications", tier=TIER)
        if kind == "later":
            return _xd.DispatchResult(domain="notifications", reply=LATER_REPLY, intent="notifications", tier=TIER)
        rows = await store.unread(member, limit=50)
        t = _TURNS.get(sid)
        if t is not None and t.panel_id:
            await store.push_card(t.panel_id, member)
        return _xd.DispatchResult(domain="notifications", reply=read_out(rows), intent="notifications", tier=TIER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("notification_tail: tier failed (non-fatal): %s", exc.__class__.__name__)
        return None


def reset() -> None:
    for d in (_TURNS, _PENDING, _LAST, _DAY, _LOADED, store.ANNOUNCED):
        d.clear()
