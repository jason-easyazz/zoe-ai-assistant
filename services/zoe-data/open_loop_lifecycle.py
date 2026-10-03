"""Open-loop lifecycle — flag-dark (``ZOE_LOOP_LIFECYCLE``, default OFF, read per call).

The week-in-the-life simulation (scripts/perf/samantha_day_sim.py, 2026-10-03) found
loops that were never closed and loops that could never be raised:

* A correction never closed a loop. "Actually, my mum lives in Bendigo, not Ballarat"
  and "I've dropped the half-marathon" superseded the FACTS, but the Ballarat and
  half-marathon loops stayed open, ready to be raised as if still true.
  ``resolve_for_supersede`` closes them whenever a fact is retired: the implicit
  path (``memory_supersede``) and every ``MemoryService.review(edit)``.
* A health worry was never a loop: "migraines most afternoons … worry me" was extracted
  and then dropped by ``open_loop_quality.loop_is_concrete`` — no anchor names a
  symptom. ``HEALTH_NOUNS`` join the anchors while the flag is on (the gate stays).

The other parts of the flag live with their owners: the selector's decayed relevance
and raise phrasing (``proactive/selector.py``), the brief marking what it mentioned
(``brief_first_turn``), and the extractor's horizon guidance and resolved-recently
dedupe (``memory_digest._extract_open_loops``). Record:
docs/knowledge/synthetic-users-and-proactive-recipients.md ("Open-loop lifecycle").
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

logger = logging.getLogger(__name__)
# Counts only, on the open-loop logger the standalone dreaming runner surfaces.
_loops_log = logging.getLogger("memory_digest.open_loops")

# Conditions, not moods: "migraines" names a thing to ask about; "a bit rough" does not.
# No generic word ("pain", "sick", "cold") — anchors are also cue words, and "what a
# pain" must not raise someone's back.
HEALTH_NOUNS = frozenset({
    "migraine", "headache", "injury", "illness", "flu", "fever", "cough", "infection",
    "symptom", "diagnosis", "medication", "prescription", "insomnia", "anxiety",
    "depression", "asthma", "allergy", "allergies",
})
# A retired fact's topic must cover at least this share of the smaller token set
# (memory_supersede.MIN_CONTAINMENT, the same bar the fact-to-fact match uses).
_MIN_CONTAINMENT = 0.5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def lifecycle_enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_LOOP_LIFECYCLE", False)


def retired_match(loop: str, old: str, new: str = "", *, ended: bool = False) -> bool:
    """True when an open loop rests on a fact that was just retired. Pure.

    * The retirement took an anchor away (the corrected-away place, person or event:
      "Ballarat" when Ballarat became Bendigo) and the loop names it.
    * ``ended`` (a cue-driven retirement — the old state ended or was replaced): the
      loop shares an anchor with the old fact AND is on its topic
      (``memory_supersede.topic_tokens`` containment).

    A plain edit that only adds detail ("a dentist appointment" → "…on Friday for a
    cracked molar") removes no anchor and is not ``ended``: the loop stays open."""
    from memory_supersede import topic_tokens
    from open_loop_quality import loop_anchors

    anchors = loop_anchors(loop)
    if not anchors:
        return False
    old_anchors = loop_anchors(old)
    if anchors & (old_anchors - loop_anchors(new)):
        return True
    if not ended or not anchors & old_anchors:
        return False
    a, b = topic_tokens(loop), topic_tokens(old)
    shared = a & b
    return bool(shared) and len(shared) / min(len(a), len(b)) >= _MIN_CONTAINMENT


async def resolve_for_supersede(user_id: str, pairs: Iterable[tuple[str, str]], *,
                                ended: bool, source: str) -> int:
    """Close the user's open loops that rest on retired facts; ``pairs`` are
    ``(old_text, new_text)``. Also expires their proactive candidates, so a loop closed
    this morning is not raised this afternoon. No I/O when off. Never raises."""
    pairs = [(o or "", n or "") for o, n in pairs if o]
    if not pairs or not user_id or not lifecycle_enabled():
        return 0
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute(
                "SELECT id, loop_text FROM open_loops WHERE user_id = ? AND resolved IS NOT TRUE",
                (user_id,),
            ) as cur:
                loops = [(r[0], str(r[1] or "")) for r in await cur.fetchall()]
            hit = [lid for lid, text in loops
                   if any(retired_match(text, o, n, ended=ended) for o, n in pairs)]
            stamp = _now().strftime("%Y-%m-%dT%H:%M:%SZ")  # proactive_candidates format
            for lid in hit:
                await db.execute(
                    "UPDATE open_loops SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND user_id = ?", (lid, user_id),
                )
                await db.execute(
                    "UPDATE proactive_candidates SET expires_at = ? WHERE user_id = ? "
                    "AND source_ref = ? AND expires_at > ?",
                    (stamp, user_id, f"open_loops:{lid}", stamp),
                )
    except Exception as exc:  # noqa: BLE001 — a memory write must never fail on this
        logger.warning("open-loop lifecycle: resolve failed user=%s: %s", user_id,
                       type(exc).__name__)
        return 0
    if hit:
        _loops_log.info("OPEN_LOOPS user=%s resolved_by_supersede=%d source=%s",
                        user_id, len(hit), source)
    return len(hit)
