"""person_health.py — Relationship health scoring.

Health score = weighted combination of:
  - Recency: exponential decay from last contact, half-life depends on context + tier
  - Frequency: log-scaled lifetime contact count
  - Birthday proximity boost: +0.3 if birthday within 14 days

Score is in [0.0, 1.0]. Stored in people.health_score and recalculated
whenever a person record or related activity is written.

Context/tier model (replaces old 6-value circle):
  personal:inner  — partner, kids, closest family, best friends
  personal:circle — regular friends/family
  personal:public — acquaintances
  work:inner      — key colleagues, sponsor, close work friend
  work:circle     — regular colleagues
  work:public     — professional contacts
"""

import math
import logging
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# Half-life in days for exponential recency decay by context:tier
_HALF_LIFE: dict[str, int] = {
    "personal:inner":  14,   # neglect felt quickly for closest people
    "personal:circle": 30,
    "personal:public": 90,
    "work:inner":      21,
    "work:circle":     45,
    "work:public":    120,
}

# Backward-compatibility aliases (old single-dimension circle values)
_HALF_LIFE_LEGACY: dict[str, int] = {
    "inner":       14,
    "friends":     30,
    "family":      21,
    "work":        45,
    "acquaintance":60,
    "public":      90,
}


def _next_occurrence(month: int, day: int, ref: Optional[date] = None) -> date:
    """Return the next calendar occurrence of (month, day) on or after ref (default: today)."""
    ref = ref or date.today()
    try:
        candidate = date(ref.year, month, day)
    except ValueError:
        candidate = date(ref.year, month, min(day, 28))
    if candidate < ref:
        try:
            candidate = date(ref.year + 1, month, day)
        except ValueError:
            candidate = date(ref.year + 1, month, min(day, 28))
    return candidate


def calc_health_score(
    last_contacted_at: Optional[str],
    contact_count: int,
    circle: str,
    next_birthday: Optional[date] = None,
    context: str = "personal",
) -> float:
    """Calculate a relationship health score in [0.0, 1.0].

    Args:
        last_contacted_at: ISO-8601 string or None.
        contact_count: Cumulative count of logged interactions.
        circle: Tier — 'inner', 'circle', or 'public'. Also accepts legacy values.
        next_birthday: Upcoming birthday date or None.
        context: 'personal' or 'work' (default 'personal').
    """
    # Look up half-life by context:tier, then by legacy single-dim value
    key = f"{context}:{circle}"
    half_life = _HALF_LIFE.get(key) or _HALF_LIFE_LEGACY.get(circle, 60)

    if last_contacted_at:
        try:
            last_dt = datetime.fromisoformat(last_contacted_at.replace("Z", "+00:00"))
            days_since = max((datetime.now(last_dt.tzinfo) - last_dt).days, 0)
        except (ValueError, TypeError):
            days_since = 365
    else:
        days_since = 365

    recency = math.exp(-days_since / half_life)
    freq = min(math.log1p(contact_count) / math.log1p(50), 1.0)

    bday_boost = 0.0
    if next_birthday:
        days_to_bday = (next_birthday - date.today()).days
        if 0 <= days_to_bday <= 14:
            bday_boost = 0.3

    score = min(round(0.6 * recency + 0.3 * freq + bday_boost, 3), 1.0)
    return score


async def recalc_and_save(person_id: str, user_id: str, db) -> float:
    """Recalculate health_score for one person and persist it to the DB.

    Args:
        person_id: UUID of the person row.
        user_id: Owner of the person record.
        db: asyncpg/aiosqlite connection (must support execute + fetchone).

    Returns:
        The new health_score (or 0.5 if the person is not found).
    """
    try:
        row = await (await db.execute(
            "SELECT circle, context, last_contacted_at, contact_count FROM people WHERE id=$1 AND user_id=$2",
            person_id, user_id,
        )).fetchone()
    except Exception:
        try:
            row = await (await db.execute(
                "SELECT circle, context, last_contacted_at, contact_count FROM people WHERE id=? AND user_id=?",
                (person_id, user_id),
            )).fetchone()
        except Exception as exc:
            logger.warning("recalc_and_save: DB read failed for %s: %s", person_id, exc)
            return 0.5

    if not row:
        return 0.5

    circle, context, last_contacted_at, contact_count = row[0], row[1], row[2], row[3] or 0
    context = context or "personal"

    # Look up next birthday from person_important_dates
    try:
        bday_row = await (await db.execute(
            "SELECT month, day FROM person_important_dates "
            "WHERE person_id=$1 AND date_type='birthday' ORDER BY created_at LIMIT 1",
            person_id,
        )).fetchone()
    except Exception:
        try:
            bday_row = await (await db.execute(
                "SELECT month, day FROM person_important_dates "
                "WHERE person_id=? AND date_type='birthday' ORDER BY created_at LIMIT 1",
                (person_id,),
            )).fetchone()
        except Exception:
            bday_row = None

    next_bday: Optional[date] = None
    if bday_row and bday_row[0] and bday_row[1]:
        try:
            next_bday = _next_occurrence(bday_row[0], bday_row[1])
        except Exception:
            pass

    score = calc_health_score(
        last_contacted_at,
        contact_count,
        circle or "circle",
        next_bday,
        context,
    )

    try:
        await db.execute(
            "UPDATE people SET health_score=$1 WHERE id=$2 AND user_id=$3",
            score, person_id, user_id,
        )
    except Exception:
        try:
            await db.execute(
                "UPDATE people SET health_score=? WHERE id=? AND user_id=?",
                (score, person_id, user_id),
            )
        except Exception as exc:
            logger.warning("recalc_and_save: DB write failed for %s: %s", person_id, exc)

    return score


__all__ = ["calc_health_score", "recalc_and_save", "_next_occurrence"]
