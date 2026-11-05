"""The outcome edge (Samantha initiative lane D, E1/E7) - ``ZOE_OUTCOME_EDGES`` = off | on, default OFF.

The proactive loop already decides what is worth raising (``selector``), carries it (a raise, a
pull) and counts the delivery (``ledger``, ``lines``). It never wrote down what the person DID with
an item, so follow-through could not be measured and a dismissed item was only ever tuned at the
class level. This module is that edge, in one small table (migration 0069, ``proactive_outcome_edges``)
keyed ``(member, item, local day, touch point)`` and fed ONLY by events Zoe already records - no model,
no new text:

  * ``raise`` / ``pull`` edges open when ``selector._settle`` / ``pull.pull`` deliver an item. A pull
    is born ``acted`` (the person asked; source ``pull_ask``).
  * the outcome (``acted`` | ``dismissed`` | ``ignored`` | ``unknown``) is set by, strongest first:
    ``tap`` (the one-tap "that was welcome" / "not now", ``lines.record_tap``) > ``loop_resolved``
    (the open loop was resolved after the raise; ``sweep``) > ``ledger_sweep`` (the delivery
    ledger's judgement of the member's next turn) > ``pull_ask`` > ``expiry`` (nothing decided it in
    24 h). A stronger source overwrites a weaker one, never the reverse; a later tap replaces an
    earlier one.
  * the FIRST-PRESENCE RE-SCORE (``on_presence``, called from ``selector.prepare``): at a member's
    first turn of the local day, today's due items are re-scored in code (SQL + arithmetic) instead of
    waiting for 03:00. ``score = base * outcome_factor + DUE_BOOST`` (capped at 1.0), where the factor
    drops for an item the member dismissed (x0.5) or ignored (x0.85 each, at most 3) in the last
    ``LOOKBACK_DAYS``. The order is total and deterministic (score desc, due time, ref), the new
    score is written to the candidate (so the same turn's raise pick and a pull rank by it) and each
    due item gets a ``presence`` edge with its rank and before / after scores. Once per member per day
    (a ``*`` day marker row, so a restart does not repeat it).

Items are identified by the selector's ``source_ref`` (an opaque id), never by text; nothing here logs
or stores an utterance, an item's words or a name. Off: every entry point returns before any DB access
and nothing is written. Every entry point NEVER raises. Forget: ``erase_user`` (MemoryService.delete_user)
deletes the member's rows whatever the flag says; rows hold opaque ids and labels only, so an entity forget
("forget Dana") has no name to erase from them.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from typed_env import env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_OUTCOME_EDGES"
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
RAISE, PULL, PRESENCE = "raise", "pull", "presence"
DAY_MARKER = "*"
OUTCOMES = ("acted", "dismissed", "ignored", "unknown")
# Stronger evidence wins; equal strength = the later event (a second tap replaces the first).
SOURCE_RANK = {"expiry": 0, "pull_ask": 1, "ledger_sweep": 2, "loop_resolved": 3, "tap": 4}
_TAP_OUTCOME = {"welcome": "acted", "not_now": "dismissed"}   # "that was fine" decides nothing
_LEDGER_OUTCOME = {"accepted": "acted", "ignored": "ignored"}  # undelivered / unknown -> unknown
DUE_BOOST = 0.2
DISMISS_FACTOR = 0.5
IGNORE_FACTOR = 0.85
IGNORE_CAP = 3
LOOKBACK_DAYS = 14
EXPIRE_AFTER = timedelta(hours=24)
_SWEEP_BATCH = 200
_seen: dict[str, str] = {}   # member -> local day already re-scored in this process (the DB marker is the truth)


def mode() -> str:
    """``on`` | ``off`` (default). A typo is off. Read per call."""
    return "on" if env_str("ZOE_OUTCOME_EDGES", "").lower() in {"1", "true", "yes", "on"} else "off"


def enabled() -> bool:
    return mode() == "on"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _day(now: datetime) -> str:
    from time_utils import zoe_timezone

    return now.astimezone(zoe_timezone()).date().isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value) -> datetime | None:
    from brief_first_turn import _as_utc

    try:
        return _as_utc(value)
    except Exception:  # noqa: BLE001
        return None


# -- edges: open and decide --------------------------------------------------------------------
async def open_edge(db, *, user_id: str, source_ref: str, kind: str, touch_point: str, now: datetime,
                    outcome: str | None = None, source: str | None = None) -> bool:
    """Insert the edge for today (idempotent on the key). True when a NEW row was written."""
    if not enabled() or not user_id or not source_ref:
        return False
    try:
        cur = await db.execute(
            """INSERT INTO proactive_outcome_edges (id, user_id, item_id, kind, local_day, touch_point,
                   outcome, outcome_at, outcome_source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, item_id, local_day, touch_point) DO NOTHING""",
            (uuid.uuid4().hex, user_id, source_ref, kind or "", _day(now), touch_point, outcome,
             _iso(now) if outcome else None, source if outcome else None, _iso(now)))
        return (getattr(cur, "rowcount", 1) or 0) > 0
    except Exception as exc:  # noqa: BLE001 - the edge must never break a settle
        logger.warning("outcome-edges: open failed user=%s: %r", user_id, exc)
        return False


async def open_for_candidate(db, *, candidate_id: str, user_id: str, touch_point: str, now: datetime) -> bool:
    """A raise just settled: read the candidate's identity, open the edge."""
    if not enabled():
        return False
    try:
        async with db.execute("SELECT kind, source_ref FROM proactive_candidates WHERE id = ?",
                              (candidate_id,)) as cur:
            row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("outcome-edges: candidate read failed user=%s: %r", user_id, exc)
        return False
    if not row:
        return False
    return await open_edge(db, user_id=user_id, source_ref=str(row[1]), kind=str(row[0]),
                           touch_point=touch_point, now=now)


async def set_outcome(db, *, user_id: str, source_ref: str, outcome: str, source: str, now: datetime,
                      day: str | None = None, touch_points: tuple[str, ...] = (RAISE, PULL)) -> int:
    """Decide the item's edge(s) for ``day`` (default today). Only an edge with no outcome, or one decided
    by an equal or weaker source, changes. Returns rows changed."""
    if not enabled() or outcome not in OUTCOMES or source not in SOURCE_RANK or not user_id or not source_ref:
        return 0
    n = 0
    try:
        marks = ",".join("?" for _ in touch_points)
        # The precedence lives IN the UPDATE (one atomic statement per edge): an edge decided by a stronger
        # source, or by an equal one at a LATER event time, is never overwritten, whatever order events are
        # processed in. Source names come from the closed SOURCE_RANK map, never from input.
        rank_sql = "CASE outcome_source " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in SOURCE_RANK.items()) + " ELSE -1 END"
        cur = await db.execute(
            "UPDATE proactive_outcome_edges SET outcome = ?, outcome_at = ?, outcome_source = ? "
            "WHERE user_id = ? AND item_id = ? AND local_day = ? "
            f"AND touch_point IN ({marks}) AND (outcome_source IS NULL OR {rank_sql} < ? "
            f"OR ({rank_sql} = ? AND (outcome_at IS NULL OR outcome_at <= ?)))",
            (outcome, _iso(now), source, user_id, source_ref, day or _day(now), *touch_points,
             SOURCE_RANK[source], SOURCE_RANK[source], _iso(now)))
        n = int(getattr(cur, "rowcount", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("outcome-edges: set failed user=%s: %r", user_id, exc)
        return 0
    if n:
        logger.info("OUTCOME_EDGE user=%s outcome=%s source=%s n=%d", user_id, outcome, source, n)
    return n


async def on_tap(db, user_id: str, source_ref: str, signal: str, now: datetime,
                 delivered_at: datetime | None = None) -> int:
    """The member's one-tap signal about a delivery (``lines.record_tap``). The edge belongs to the DELIVERY's local day: a 23:58 raise
    tapped at 00:02 decides yesterday's edge (``delivered_at``), not today's (which does not exist)."""
    outcome = _TAP_OUTCOME.get(signal)
    if outcome is None:
        return 0
    return await set_outcome(db, user_id=user_id, source_ref=source_ref, outcome=outcome,
                             source="tap", now=now, day=_day(delivered_at) if delivered_at else None)


async def on_ledger_close(db, delivery_id: str, ledger_outcome: str, now: datetime) -> int:
    """The delivery ledger closed ``delivery_id`` (``ledger.sweep``): carry its judgement to the edge."""
    if not enabled():
        return 0
    try:
        async with db.execute(
            "SELECT user_id, source_ref, delivered_by, surfaced_at FROM proactive_deliveries WHERE id = ?",
            (delivery_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return 0
        surfaced = _utc(row[3]) or now
        return await set_outcome(
            db, user_id=str(row[0]), source_ref=str(row[1]),
            outcome=_LEDGER_OUTCOME.get(ledger_outcome, "unknown"), source="ledger_sweep", now=now,
            day=_day(surfaced), touch_points=(PULL,) if str(row[2]) == "pull" else (RAISE,))
    except Exception as exc:  # noqa: BLE001
        logger.warning("outcome-edges: ledger carry failed: %r", exc)
        return 0


async def sweep(*, now: datetime | None = None) -> int:
    """Engine slow loop: a raised open loop the member then resolved is ``acted``; an edge nothing decided
    in 24 h is ``unknown``. Returns rows changed. No DB access with the flag off. Never raises."""
    if not enabled():
        return 0
    from db_compat import get_compat_db

    now = now or _now()
    changed = 0
    try:
        async with get_compat_db() as db:
            async with db.execute(
                "SELECT user_id, item_id, local_day, created_at FROM proactive_outcome_edges "
                "WHERE touch_point IN ('raise', 'pull') AND item_id LIKE 'open_loops:%' "
                "AND (outcome IS NULL OR outcome_source IN ('expiry', 'pull_ask', 'ledger_sweep')) "
                "AND created_at >= ? ORDER BY created_at LIMIT ?",
                (_iso(now - timedelta(days=3)), _SWEEP_BATCH)) as cur:
                rows = [tuple(r) for r in await cur.fetchall()]
            for uid, ref, day, created in rows:
                try:
                    loop_id = int(str(ref).split(":", 1)[1])
                except (IndexError, ValueError):
                    continue
                async with db.execute(
                    "SELECT resolved, resolved_at FROM open_loops WHERE id = ? AND user_id = ?",
                    (loop_id, uid)) as cur:
                    lp = await cur.fetchone()
                at, born = (_utc(lp[1]) if lp else None), _utc(created)
                if lp and lp[0] and at and born and at >= born:
                    changed += await set_outcome(db, user_id=uid, source_ref=ref, outcome="acted",
                                                 source="loop_resolved", now=now, day=day)
            cur = await db.execute(
                "UPDATE proactive_outcome_edges SET outcome = 'unknown', outcome_at = ?, "
                "outcome_source = 'expiry' WHERE outcome IS NULL AND touch_point IN ('raise', 'pull') "
                "AND created_at < ?", (_iso(now), _iso(now - EXPIRE_AFTER)))
            changed += int(getattr(cur, "rowcount", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("outcome-edges: sweep failed: %r", exc)
    return changed


# -- the first-presence re-score ---------------------------------------------------------------
@dataclass(frozen=True)
class Due:
    candidate_id: str
    source_ref: str
    kind: str
    base: float
    due: datetime | None


@dataclass(frozen=True)
class Scored:
    candidate_id: str
    source_ref: str
    kind: str
    before: float
    after: float
    rank: int


def outcome_factor(dismissed: int, ignored: int) -> float:
    if dismissed > 0:
        return DISMISS_FACTOR
    return IGNORE_FACTOR ** min(max(ignored, 0), IGNORE_CAP)


def rescore(items: list[Due], prior: dict[str, tuple[int, int]]) -> list[Scored]:
    """Pure and total: ``score = min(1, base * outcome_factor + DUE_BOOST)``; ordered by score (desc), then
    the earlier due time, then ``source_ref`` - the same result whatever order ``items`` arrive in.
    ``prior`` is ``{source_ref: (dismissed, ignored)}``."""
    far = datetime.max.replace(tzinfo=timezone.utc)
    scored = []
    for it in items:
        dis, ign = prior.get(it.source_ref, (0, 0))
        after = round(min(1.0, it.base * outcome_factor(dis, ign) + DUE_BOOST), 4)
        scored.append((-after, it.due or far, it.source_ref, it, after))
    scored.sort(key=lambda t: t[:3])
    return [Scored(t[3].candidate_id, t[3].source_ref, t[3].kind, t[3].base, t[4], i + 1)
            for i, t in enumerate(scored)]


async def _due_today(db, uid: str, now: datetime) -> list[Due]:
    """The member's still-raisable candidates that are due today (or overdue): an event that starts today, an
    open loop whose follow-up time is today or past. Same eligibility as the pull / raise pick."""
    from proactive.selector import MAX_SURFACED
    from time_utils import zoe_timezone

    stamp, tz = _iso(now), zoe_timezone()
    today = now.astimezone(tz).date()
    async with db.execute(
        "SELECT id, kind, source_ref, salience, expires_at FROM proactive_candidates "
        "WHERE user_id = ? AND kind <> 'commitment' AND expires_at > ? AND surfaced_count < ? "
        "AND (cooldown_until IS NULL OR cooldown_until <= ?)", (uid, stamp, MAX_SURFACED, stamp)) as cur:
        rows = [tuple(r) for r in await cur.fetchall()]
    out: list[Due] = []
    for cid, kind, ref, sal, expires in rows:
        due = None
        if kind == "event":
            due = _utc(expires)
        elif kind == "open_loop":
            try:
                loop_id = int(str(ref).split(":", 1)[1])
            except (IndexError, ValueError):
                continue
            async with db.execute("SELECT follow_up_after FROM open_loops WHERE id = ? AND user_id = ?",
                                  (loop_id, uid)) as cur:
                lp = await cur.fetchone()
            due = _utc(lp[0]) if lp else None
        if due is None or due.astimezone(tz).date() > today:
            continue
        out.append(Due(str(cid), str(ref), str(kind), float(sal or 0), due))
    return out


async def _prior(db, uid: str, now: datetime) -> dict[str, tuple[int, int]]:
    since = _day(now - timedelta(days=LOOKBACK_DAYS))
    async with db.execute(
        "SELECT item_id, outcome, COUNT(*) FROM proactive_outcome_edges WHERE user_id = ? "
        "AND touch_point IN ('raise', 'pull') AND outcome IN ('dismissed', 'ignored') AND local_day >= ? "
        "GROUP BY item_id, outcome", (uid, since)) as cur:
        rows = await cur.fetchall()
    out: dict[str, tuple[int, int]] = {}
    for ref, outcome, n in rows:
        dis, ign = out.get(str(ref), (0, 0))
        out[str(ref)] = (dis + int(n), ign) if outcome == "dismissed" else (dis, ign + int(n))
    return out


async def on_presence(uid: str, now: datetime | None = None) -> list[str] | None:
    """The member's first turn of the local day: re-score today's due items, once. Returns the source_refs in
    the new order, ``[]`` when nothing is due, ``None`` when it did not run (off, already done today, error).
    Never raises."""
    if not enabled() or not uid:
        return None
    now = now or _now()
    day = _day(now)
    if _seen.get(uid) == day:
        return None
    marked = False
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            if not await open_edge(db, user_id=uid, source_ref=DAY_MARKER, kind="day",
                                   touch_point=PRESENCE, now=now):
                _seen[uid] = day      # an earlier turn (or process) already did today
                return None
            marked = True
            order = rescore(await _due_today(db, uid, now), await _prior(db, uid, now))
            for s in order:
                # The edge FIRST: it records the item's stable before-score, which is what a failed attempt's rollback restores
                # (_unmark_presence). The statement-by-statement auto-commit wrapper has no transaction to roll back.
                await db.execute(
                    """INSERT INTO proactive_outcome_edges (id, user_id, item_id, kind, local_day, touch_point,
                           rescore_rank, score_before, score_after, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (user_id, item_id, local_day, touch_point) DO NOTHING""",
                    (uuid.uuid4().hex, uid, s.source_ref, s.kind, day, PRESENCE, s.rank, s.before, s.after,
                     _iso(now)))
                if s.after != s.before:
                    await db.execute("UPDATE proactive_candidates SET salience = ?, updated_at = ? "
                                     "WHERE id = ? AND user_id = ?", (s.after, _iso(now), s.candidate_id, uid))
        _seen[uid] = day
        logger.info("OUTCOME_RESCORE user=%s due=%d", uid, len(order))
        return [s.source_ref for s in order]
    except BaseException as exc:  # noqa: BLE001 - presence must never break a turn; a CANCEL (selector.prepare's timeout) too
        cancelled = isinstance(exc, asyncio.CancelledError)
        if not cancelled and not isinstance(exc, Exception):
            raise
        logger.warning("outcome-edges: presence re-score %s user=%s: %r", "cancelled" if cancelled else "failed", uid, exc)
        if marked:   # let the next turn retry rather than strand the day: the production DB wrapper commits each statement, so the
            # marker is already durable - take it back, shielded so a second cancel cannot interrupt the cleanup
            try:
                await asyncio.shield(_unmark_presence(uid, day))
            except BaseException:  # noqa: BLE001
                pass
        if cancelled:
            raise
        return None


async def _unmark_presence(uid: str, day: str) -> None:
    """Take a failed / cancelled re-score back COMPLETELY: put every touched candidate's salience back to its recorded before-score,
    drop the attempt's presence edges, then the day marker - so the retry starts from the true scores and an audit record never
    reports a score the retry then compounded."""
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        async with db.execute(
            "SELECT item_id, score_before FROM proactive_outcome_edges WHERE user_id = ? AND local_day = ? AND touch_point = ? "
            "AND item_id <> ? AND score_before IS NOT NULL", (uid, day, PRESENCE, DAY_MARKER),
        ) as cur:
            done = [(str(r[0]), r[1]) for r in await cur.fetchall()]
        for ref, before in done:
            await db.execute("UPDATE proactive_candidates SET salience = ? WHERE user_id = ? AND source_ref = ?",
                             (before, uid, ref))
        await db.execute("DELETE FROM proactive_outcome_edges WHERE user_id = ? AND local_day = ? AND touch_point = ? "
                         "AND item_id <> ?", (uid, day, PRESENCE, DAY_MARKER))
        await db.execute("DELETE FROM proactive_outcome_edges WHERE user_id = ? AND item_id = ? "
                         "AND local_day = ? AND touch_point = ?", (uid, DAY_MARKER, day, PRESENCE))


async def todays_ranks(db, uid: str, now: datetime | None = None) -> dict[str, int]:
    """source_ref -> the rank today's first-presence re-score gave it ({} when off, not run yet, or on any fault). Delivery uses it
    to break equal-salience ties the way the re-score decided (due time, then ref), not by kind / candidate id."""
    if not enabled() or not uid:
        return {}
    try:
        async with db.execute(
            "SELECT item_id, rescore_rank FROM proactive_outcome_edges WHERE user_id = ? AND local_day = ? AND touch_point = ? "
            "AND item_id <> ? AND rescore_rank IS NOT NULL", (uid, _day(now or _now()), PRESENCE, DAY_MARKER),
        ) as cur:
            return {str(r[0]): int(r[1]) for r in await cur.fetchall()}
    except Exception:  # noqa: BLE001
        return {}


# -- forget ------------------------------------------------------------------------------------
async def erase_user(user_id: str) -> int:
    """Right-to-be-forgotten: every edge of the member goes, whatever the flag says."""
    uid = (user_id or "").strip()
    if not uid:
        return 0
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        cur = await db.execute("DELETE FROM proactive_outcome_edges WHERE user_id = ?", (uid,))
        n = int(getattr(cur, "rowcount", 0) or 0)
    _seen.pop(uid, None)
    return n


def _reset_state() -> None:
    _seen.clear()
