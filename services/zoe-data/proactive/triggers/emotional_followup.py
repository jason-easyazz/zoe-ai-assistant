"""Emotional follow-up trigger — Samantha proactivity (VISION pillar 3).

After a durable, high-weight WORRY is captured (an ``emotional_moment`` with
negative/mixed valence and real intensity), gently check in ONCE, at a waking
hour, on a *later* day — "I know the settlement's been weighing on you; how's
that sitting now?" — and then never nag about it again. This turns the emotional
substrate (capture #1003 + recall #1004/#1005/#1014) from recall-when-asked into
cares-unprompted.

Tier-2 trigger: the engine's slow loop calls ``check(db)`` every
``ZOE_PROACTIVE_SLOW_LOOP_S``; it returns ``TriggerResult``s and the engine owns
push + ``proactive_pending`` + quiet hours + (for non-reminder types) LLM
composition from ``context``.

Flag-gated OFF (``ZOE_EMOTIONAL_FOLLOWUP_ENABLED``): flag off ⇒ ``check`` returns
``[]`` before any query — a true no-op. Sparse by construction (neg/mixed +
intensity gate), one-per-moment-ever, and at most one per user per day.
"""
from __future__ import annotations

import logging
import os
import re
import zoneinfo
from datetime import datetime, timezone

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))

# Only follow up on worries that carry real, lasting weight.
_FOLLOW_VALENCES = {"neg", "mixed"}
_MIN_INTENSITY = 0.6
# Age window: old enough to be a *later* check-in (not a same-day pile-on),
# fresh enough to still matter.
_MIN_AGE_H = 20
_MAX_AGE_D = 7
# Waking-hours window (local), on top of the engine's own quiet-hours guard.
_WAKE_START_H = 9
_WAKE_END_H = 20
# How many recent memories to scan for candidates (metadata-only read).
_SCAN_LIMIT = 200


def _enabled() -> bool:
    return os.environ.get("ZOE_EMOTIONAL_FOLLOWUP_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_dt(raw) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _intensity(meta: dict) -> float:
    try:
        return float(meta.get("candidate_intensity"))
    except (TypeError, ValueError):
        return 0.0


def _topic(fact: str) -> str:
    """Best-effort second-person topic for the deterministic fallback message —
    the durable fact is stored third-person ("Jason has been anxious about the
    house settlement"), so pull the "about X" clause and soften the pronoun. The
    engine's LLM compose (primary) does the natural phrasing; this only shows if
    the model is unavailable."""
    m = re.search(r"\babout\s+(.+)$", fact.strip().rstrip("."), re.IGNORECASE)
    topic = m.group(1) if m else ""
    topic = re.sub(r"^(his|her|their|my)\s+", "the ", topic, flags=re.IGNORECASE)
    return topic.strip()


def _fallback_message(fact: str) -> str:
    topic = _topic(fact)
    if topic:
        return f"Hey — I've been thinking about you. How are things with {topic} now?"
    return "Hey — I've been thinking about you. Something seemed to be weighing on you lately — how are you doing with it now?"


class EmotionalFollowUpTrigger(ProactiveTrigger):
    trigger_type = "emotional_followup"

    async def check(self, db) -> list[TriggerResult]:
        if not _enabled():
            return []
        now = datetime.now(_ZOE_TZ)
        if not (_WAKE_START_H <= now.hour < _WAKE_END_H):
            return []

        # Active users (anyone who chatted in the last 7 days) — same population
        # the morning brief serves.
        async with db.execute(
            """SELECT DISTINCT user_id FROM chat_sessions
               WHERE created_at::timestamptz > (CURRENT_TIMESTAMP - INTERVAL '7 days')"""
        ) as cur:
            users = [row[0] async for row in cur]

        results: list[TriggerResult] = []
        for user_id in users:
            if not user_id:
                continue
            # Daily cap: at most one emotional follow-up per user per day (any
            # claimed/unclaimed row today) so multiple worries never barrage.
            async with db.execute(
                "SELECT 1 FROM proactive_pending WHERE trigger_type=? AND user_id=? "
                "AND created_at::date = CURRENT_DATE LIMIT 1",
                (self.trigger_type, user_id),
            ) as cur:
                if await cur.fetchone():
                    continue

            moment = await self._pick_moment(db, user_id, now)
            if moment is None:
                continue
            mem_id, fact, meta = moment
            results.append(
                TriggerResult(
                    user_id=user_id,
                    message=_fallback_message(fact),
                    trigger_type=self.trigger_type,
                    item_id=mem_id,   # per-moment dedup key
                    context={
                        "kind": "emotional_followup",
                        "fact": fact,
                        "valence": str(meta.get("candidate_valence") or ""),
                        "intensity": _intensity(meta),
                        "user_id": user_id,
                    },
                )
            )
        return results

    async def _pick_moment(self, db, user_id: str, now: datetime):
        """Highest-intensity qualifying worry for the user that hasn't already
        been followed up. Returns (mem_id, fact, metadata) or None."""
        from memory_service import get_memory_service

        try:
            refs = await get_memory_service().load_for_prompt(user_id, limit=_SCAN_LIMIT)
        except Exception as exc:
            log.debug("emotional_followup: memory read failed for %s: %s", user_id, exc)
            return None

        now_utc = now.astimezone(timezone.utc)
        candidates = []
        for r in refs:
            meta = r.metadata or {}
            if str(meta.get("memory_type")) != "emotional_moment":
                continue
            if str(meta.get("candidate_valence") or "").lower() not in _FOLLOW_VALENCES:
                continue
            if _intensity(meta) < _MIN_INTENSITY:
                continue
            added = _parse_dt(meta.get("added_at"))
            if added is None:
                continue
            age_h = (now_utc - added).total_seconds() / 3600.0
            if age_h < _MIN_AGE_H or age_h > _MAX_AGE_D * 24:
                continue
            candidates.append((_intensity(meta), r.id, (r.text or "").strip(), meta))

        # Highest-intensity first; skip any already followed up (one per moment ever).
        for _, mem_id, fact, meta in sorted(candidates, key=lambda c: c[0], reverse=True):
            if not fact:
                continue
            async with db.execute(
                "SELECT 1 FROM proactive_pending WHERE trigger_type=? AND item_id=? LIMIT 1",
                (self.trigger_type, mem_id),
            ) as cur:
                if await cur.fetchone():
                    continue
            return mem_id, fact, meta
        return None
