"""The forget cascade (memory fidelity audit P2.2): "forget Dana" also clears what was DERIVED from Dana.

Forgetting archived the palace rows and nothing else, so the user portrait, the user-model card, open
loops, proactive candidates and the ``people`` row all kept naming the person (audit 3.7). This runs
right after the sweep, with the entity name the person just spoke in hand (it is matched here and never
stored anywhere new):

  * ``user_portraits``      - the row is deleted when its text names the entity (derived text; the next
                              dreaming pass writes a fresh one from rows that no longer carry the name)
  * ``user_model_cards``    - deleted likewise; ``load_card_block`` rebuilds from the live rows
  * ``open_loops``          - an unresolved loop naming the entity is RESOLVED (archived, history kept)
  * ``proactive_candidates``- deleted (derived, rebuilt nightly from loops / moments / events)
  * ``people``              - the row is SOFT-deleted (``deleted = 1``, the row and its history are kept),
                              and the person's current relationship edges are CLOSED (``valid_to``)

Every step is best-effort and independent: a missing table or a DB blip in one never blocks the others, and
the forget itself never fails because of the cascade. Counts only are logged, never the name.

UNDO: the people soft-delete and the edge closes are recorded in-process for ``UNDO_WINDOW_S`` so
"keep the contact" (``restore_contacts``) can put exactly those rows back. It restores the contact, not the
forgotten memories, and not past a restart (the People panel can re-add the contact after that).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

UNDO_WINDOW_S = 900.0

# {user_id: (recorded_at_monotonic, [person ids], closed_at_iso)}
_last_cascade: dict[str, tuple[float, list[str], str]] = {}


@dataclass
class CascadeResult:
    portrait: int = 0
    card: int = 0
    loops: int = 0
    candidates: int = 0
    people: int = 0
    edges: int = 0
    person_ids: list[str] = field(default_factory=list)
    closed_at: str = ""

    @property
    def summary_cleared(self) -> int:
        return self.portrait + self.card + self.loops + self.candidates

    @property
    def touched(self) -> bool:
        return bool(self.summary_cleared or self.people)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _name_re(name: str) -> "re.Pattern[str]":
    # the same whole-word pattern the ledger's tokenisation implies (separator-blind between the words of a name),
    # so a confirmed split alias ("Mari sol") clears "Mari-sol" in a contact or a summary too
    from memory_forgotten import name_pattern
    return name_pattern(name)


def _names(rx: "re.Pattern[str]", *texts: Any) -> bool:
    return any(t and rx.search(str(t)) for t in texts)


async def _rows(db, sql: str, params: tuple) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


async def _step(label: str, fn) -> int:
    try:
        return int(await fn() or 0)
    except Exception as exc:  # noqa: BLE001 - a derived store failing must not fail the forget
        logger.warning("memory_forget_cascade: %s step failed (%s)", label, type(exc).__name__)
        return 0


async def cascade_forget(user_id: str, name: str, *, db=None) -> CascadeResult:
    """Clear everything derived from ``name`` for ``user_id`` and record the contact undo. Never raises."""
    res = CascadeResult()
    if not user_id or not (name or "").strip():
        return res
    rx = _name_re(name)
    try:
        if db is not None:
            await _run(db, user_id, rx, res)
        else:
            from db_pool import get_db_ctx  # type: ignore[import]
            async with get_db_ctx() as conn:
                await _run(conn, user_id, rx, res)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_forget_cascade: no database (%s) - derived stores not cleared",
                       type(exc).__name__)
    logger.info("memory_forget_cascade: user=%s portrait=%d card=%d loops=%d candidates=%d people=%d edges=%d",
                user_id, res.portrait, res.card, res.loops, res.candidates, res.people, res.edges)
    return res


async def _run(db, user_id: str, rx: "re.Pattern[str]", res: CascadeResult) -> None:
    async def portrait() -> int:
        rows = await _rows(db, "SELECT portrait_text FROM user_portraits WHERE user_id = ?", (user_id,))
        if rows and _names(rx, rows[0][0]):
            await db.execute("DELETE FROM user_portraits WHERE user_id = ?", (user_id,))
            await db.commit()
            return 1
        return 0

    async def card() -> int:
        rows = await _rows(db, "SELECT card_text, card_json FROM user_model_cards WHERE user_id = ?", (user_id,))
        if rows and _names(rx, rows[0][0], rows[0][1]):
            await db.execute("DELETE FROM user_model_cards WHERE user_id = ?", (user_id,))
            await db.commit()
            return 1
        return 0

    async def loops() -> int:
        rows = await _rows(
            db, "SELECT id, loop_text, context, follow_up_hint FROM open_loops "
                "WHERE user_id = ? AND resolved IS NOT TRUE", (user_id,))
        n = 0
        for r in rows:
            if _names(rx, r[1], r[2], r[3]):
                await db.execute(
                    "UPDATE open_loops SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (r[0],))
                n += 1
        if n:
            await db.commit()
        return n

    async def candidates() -> int:
        rows = await _rows(db, "SELECT id, text, hint, cue_words FROM proactive_candidates WHERE user_id = ?",
                           (user_id,))
        n = 0
        for r in rows:
            if _names(rx, r[1], r[2], r[3]):
                await db.execute("DELETE FROM proactive_candidates WHERE id = ?", (r[0],))
                n += 1
        if n:
            await db.commit()
        return n

    async def people() -> int:
        rows = await _rows(
            db, "SELECT id, name FROM people WHERE user_id = ? AND (deleted = 0 OR deleted IS NULL)", (user_id,))
        now = _now_iso()
        ids = [str(r[0]) for r in rows if _names(rx, r[1])]
        for pid in ids:
            await db.execute("UPDATE people SET deleted = 1, updated_at = ? WHERE id = ? AND user_id = ?",
                             (now, pid, user_id))
        if ids:
            await db.commit()
            res.person_ids = ids
        return len(ids)

    async def edges() -> int:
        if not res.person_ids:
            return 0
        closed_at = _now_iso()
        n = 0
        for pid in res.person_ids:
            cur = await db.execute(
                "UPDATE person_relationships SET valid_to = ?, updated_at = ? "
                "WHERE user_id = ? AND valid_to IS NULL AND (person_a_id = ? OR person_b_id = ?)",
                (closed_at, closed_at, user_id, pid, pid))
            n += int(getattr(cur, "rowcount", 0) or 0)
        await db.commit()
        res.closed_at = closed_at
        return n

    res.portrait = await _step("portrait", portrait)
    res.card = await _step("card", card)
    res.loops = await _step("open_loops", loops)
    res.candidates = await _step("proactive_candidates", candidates)
    res.people = await _step("people", people)
    res.edges = await _step("edges", edges)
    if res.person_ids:
        _last_cascade[user_id] = (time.monotonic(), list(res.person_ids),
                                  res.closed_at)


def pending_undo(user_id: str) -> bool:
    rec = _last_cascade.get(user_id)
    return bool(rec and time.monotonic() - rec[0] <= UNDO_WINDOW_S)


async def restore_contacts(user_id: str, *, db=None) -> Optional[int]:
    """Put back the contacts (and their edges) the last forget cascade removed. The number of people restored,
    0 when there is nothing to undo (none, expired, or already restored), None when the database was
    unreachable (nothing changed, the record is kept so the person can try again)."""
    rec = _last_cascade.get(user_id)
    if not rec or time.monotonic() - rec[0] > UNDO_WINDOW_S:
        _last_cascade.pop(user_id, None)
        return 0
    _t, ids, closed_at = rec
    now = _now_iso()

    async def go(conn) -> int:
        n = 0
        for pid in ids:
            await conn.execute("UPDATE people SET deleted = 0, updated_at = ? WHERE id = ? AND user_id = ?",
                               (now, pid, user_id))
            n += 1
            if closed_at:
                try:
                    await conn.execute(
                        "UPDATE person_relationships SET valid_to = NULL, updated_at = ? "
                        "WHERE user_id = ? AND valid_to = ? AND (person_a_id = ? OR person_b_id = ?)",
                        (now, user_id, closed_at, pid, pid))
                except Exception as exc:  # noqa: BLE001 - the contact matters more than its edges
                    logger.warning("memory_forget_cascade: edge restore failed (%s)", type(exc).__name__)
        await conn.commit()
        return n

    try:
        if db is not None:
            restored = await go(db)
        else:
            from db_pool import get_db_ctx  # type: ignore[import]
            async with get_db_ctx() as conn:
                restored = await go(conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_forget_cascade: restore failed (%s)", type(exc).__name__)
        return None
    _last_cascade.pop(user_id, None)
    return restored


def reset_state() -> None:
    _last_cascade.clear()
