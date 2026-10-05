"""memory_disputes - a held-back write becomes ONE question to the person, not a lost row.

``memory_authority`` parks a write that would overwrite something the user said as a
``disputed`` candidate (``contradicts_id`` -> the row it disagrees with). A candidate nobody can
see is a stale fact served back with the fresh one lost (review of #1868, finding 4). So:

* the candidates are listed in ``GET /api/memories/review`` (with the disputed row's text);
* ``queue_questions`` turns each into ONE offer through the existing ``pending_suggestions``
  mechanism (``action_type="memory_dispute"``) the turn it appears and whenever the topic comes
  up again - "Earlier you told me X; I just heard Y. Which is right?". The offer is surfaced to
  the brain by the same ``load_for_prompt`` path as every other offer. **yes** executes
  ``MemoryService.review(approve)`` by the person (the candidate becomes ``user_confirmed`` and
  retires the row it disputed); a dismissal rejects the candidate (the old row stays);
* ``expire_stale`` resolves a candidate nobody answered within ``TTL_DAYS`` (30) to a LOGGED
  ``STALE_DISPUTE`` - ``status=rejected`` with a review note and an audit row. Never a silent
  deletion: the old row keeps standing, the candidate stays in the palace for audit.

Everything here is best-effort and never raises into a turn.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

ACTION = "memory_dispute"
TTL_DAYS = 30
MAX_QUESTIONS_PER_TURN = 1


def question_text(old_text: str, new_text: str) -> str:
    """The one question, in the person's own stored words (it is THEIR memory shown back)."""
    old = " ".join((old_text or "").split()).rstrip(".")
    new = " ".join((new_text or "").split()).rstrip(".")
    return f"Earlier you told me \"{old}\" and I've just heard \"{new}\" - which is right?"


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 3}


async def _disputes(svc, user_id: str) -> list[Any]:
    rows = await svc.list_by_status(user_id=user_id, status="disputed", limit=200)
    return [r for r in rows if (r.metadata or {}).get("contradicts_id")
            and not str((r.metadata or {}).get("contradicts_id")).startswith("edge:")]


async def _already_asked(user_id: str, candidate_id: str) -> bool:
    try:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            rows = await db.fetch(
                "SELECT pre_filled_slots FROM pending_suggestions "
                "WHERE user_id = $1 AND action_type = $2 AND resolved = 0", user_id, ACTION)
        return any(json.loads(r["pre_filled_slots"] or "{}").get("candidate_id") == candidate_id
                   for r in rows)
    except Exception:  # noqa: BLE001
        return False


async def queue_questions(svc, user_id: str, session_id: str, user_message: str = "",
                          fresh: Iterable[Any] = ()) -> int:
    """Queue at most ``MAX_QUESTIONS_PER_TURN`` dispute question(s) for this turn: the candidates
    this turn just created (``fresh``), else one whose topic the user's message touches. Returns
    how many were queued. Never raises."""
    if not user_id or not session_id:
        return 0
    try:
        import memory_authority as ma

        if not ma.enabled():
            return 0
        cands = [c for c in fresh if ma.is_candidate(c) and (c.metadata or {}).get("contradicts_id")]
        if not cands and user_message:
            msg = _tokens(user_message)
            for c in await _disputes(svc, user_id):
                old = await svc.get(str(c.metadata["contradicts_id"]))
                if old is not None and msg & (_tokens(old.text) | _tokens(c.text)):
                    cands.append(c)
                    break
        queued = 0
        for c in cands[:MAX_QUESTIONS_PER_TURN]:
            old = await svc.get(str(c.metadata["contradicts_id"]))
            if old is None or str(old.metadata.get("status")) != "approved":
                continue
            if await _already_asked(user_id, c.id):
                continue
            q = question_text(old.text, c.text)
            from pending_suggestions import store_suggestions

            queued += await store_suggestions(user_id, session_id, [{
                "action_type": ACTION, "description": q[:500], "offer_phrase": q[:300],
                "pre_filled_slots": {"candidate_id": c.id, "old_id": old.id},
            }])
            logger.info("MEMORY_DISPUTE_QUESTION user=%s", user_id)
        return queued
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory_disputes.queue_questions skipped (%s)", type(exc).__name__)
        return 0


async def resolve(user_id: str, candidate_id: str, *, accept: bool, svc=None) -> bool:
    """The person's answer: ``accept`` -> approve the candidate (it becomes ``user_confirmed``
    and retires the disputed row); otherwise reject it (the old row stands)."""
    try:
        if svc is None:
            from memory_service import get_memory_service

            svc = get_memory_service()
        cur = await svc.get(candidate_id)
        if cur is None or (cur.metadata.get("user_id") or cur.metadata.get("wing")) != user_id:
            return False
        done = await svc.review(candidate_id, decision="approve" if accept else "reject",
                                actor=user_id, note="dispute answered by the person")
        return done is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_disputes.resolve failed (%s)", type(exc).__name__)
        return False


async def expire_stale(svc, user_id: str, *, ttl_days: int = TTL_DAYS, now: Optional[float] = None) -> int:
    """Resolve disputes nobody answered in ``ttl_days`` to a LOGGED stale dispute (status
    ``rejected`` + review note + audit row; the disputed row keeps standing)."""
    try:
        cutoff = (now if now is not None else datetime.datetime.now(datetime.timezone.utc).timestamp()) \
            - ttl_days * 86400
        n = 0
        for c in await svc.list_by_status(user_id=user_id, status="disputed", limit=500):
            if float((c.metadata or {}).get("added_ts") or 0) > cutoff:
                continue
            if await svc.review(c.id, decision="reject", actor="operator",
                                note=f"stale dispute: unanswered for {ttl_days} days") is not None:
                n += 1
                logger.info("STALE_DISPUTE user=%s kind=%s", user_id,
                            (c.metadata or {}).get("authority_basis") or "authority_blocked")
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_disputes.expire_stale failed (%s)", type(exc).__name__)
        return 0
