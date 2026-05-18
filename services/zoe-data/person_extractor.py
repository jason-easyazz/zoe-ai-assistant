"""person_extractor.py — Extract person facts from conversation text.

Dual fan-out: PostgreSQL (structured tables) + MemPalace (semantic recall).
Called from chat, voice, journal, notes, and the introduction flow.

Entity ID strategy:
- If person exists in people table → entity_id = DB UUID
- If person unknown → entity_id = name slug, entity_type = 'person_pending'

Every write also recalculates health_score and increments notification_count.
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

_NAME = r"([A-Z][a-z]{1,30}(?:\s[A-Z][a-z]{1,20})?)"

# Likes/loves/hates
_PREF_RE = re.compile(
    rf"{_NAME}\s+(?:loves?|likes?|hates?|prefers?|enjoys?|dislikes?)\s+(.+?)(?:[.!?]|$)",
    re.IGNORECASE,
)

# Birthday
_BDAY_RE = re.compile(
    rf"{_NAME}(?:'s)?\s+birthday\s+is\s+(?:on\s+)?([A-Za-z0-9 ]+?)(?:[.!?]|$)",
    re.IGNORECASE,
)

# Works at/for
_WORK_RE = re.compile(
    rf"{_NAME}\s+(?:works?\s+(?:at|for)|is\s+(?:a|an)\s+\w+\s+at)\s+(.+?)(?:[.!?]|$)",
    re.IGNORECASE,
)

# Met for coffee/lunch/dinner/drinks/a walk
_MEETING_RE = re.compile(
    rf"(?:met|seen?|caught\s+up\s+with|had\s+\w+\s+with)\s+{_NAME}\s+"
    rf"(?:for\s+)?(?:coffee|lunch|dinner|drinks?|a\s+walk|breakfast|brunch)(?:\s+at\s+(.+?))?(?:[.!?]|$)",
    re.IGNORECASE,
)

# Gift bought/giving
_GIFT_IDEA_RE = re.compile(
    rf"(?:buying?|getting?|get|thinking\s+about\s+getting?)\s+{_NAME}\s+a?\s*(.+?)(?:[.!?]|$)",
    re.IGNORECASE,
)
_GIFT_GIVEN_RE = re.compile(
    rf"(?:gave?|bought?|got)\s+{_NAME}\s+(?:a\s+)?(.+?)\s+for\s+(?:(?:her|his|their)\s+)?(?:birthday|xmas|christmas|anniversary)(?:[.!?]|$)",
    re.IGNORECASE,
)

# Bucket list
_BUCKET_RE = re.compile(
    rf"(?:want\s+to|would\s+love\s+to|hope\s+to|should)\s+(.+?)\s+with\s+{_NAME}(?:[.!?]|$)",
    re.IGNORECASE,
)


# ── Month name → int ──────────────────────────────────────────────────────────
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


def _parse_birthday(raw: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (month, day, year) from a birthday string, any values may be None."""
    raw = raw.strip()
    month = day = year = None

    # "15 March" or "March 15"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)", raw)
    if m:
        day = int(m.group(1))
        month = _MONTHS.get(m.group(2).lower()[:3])

    m2 = re.search(r"([A-Za-z]+)\s+(\d{1,2})", raw)
    if not month and m2:
        month = _MONTHS.get(m2.group(1).lower()[:3])
        day = int(m2.group(2))

    # YYYY-MM-DD
    m3 = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m3:
        year, month, day = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))

    # Validate
    if day and (day < 1 or day > 31):
        day = None
    if month and (month < 1 or month > 12):
        month = None
    return month, day, year


# ── DB UUID resolution ────────────────────────────────────────────────────────

async def _resolve_person_uuid(name: str, user_id: str, db) -> Optional[str]:
    """Return DB UUID if a person with this name exists for user_id, else None."""
    try:
        try:
            cursor = await db.execute(
                "SELECT id FROM people WHERE user_id=$1 AND deleted=0 AND lower(name) LIKE lower($2)",
                user_id, f"%{name}%",
            )
        except Exception:
            cursor = await db.execute(
                "SELECT id FROM people WHERE user_id=? AND deleted=0 AND lower(name) LIKE lower(?)",
                (user_id, f"%{name}%"),
            )
        row = await cursor.fetchone()
        return row[0] if row else None
    except Exception as exc:
        logger.debug("_resolve_person_uuid failed for %r: %s", name, exc)
        return None


async def _ensure_db(db_arg):
    """Return a DB connection: use db_arg if provided, else open a new one."""
    if db_arg is not None:
        return db_arg, False
    try:
        from database import get_db
        async for _db in get_db():
            return _db, True
    except Exception as exc:
        logger.warning("person_extractor: could not open DB: %s", exc)
        return None, False


async def _ingest_to_mempalace(
    text: str,
    user_id: str,
    person_name: str,
    entity_id: Optional[str],
    memory_type: str = "person",
    source: str = "conversation",
    session_id: Optional[str] = None,
) -> Optional[str]:
    """Write one fact to MemPalace and return the mem_id."""
    try:
        from memory_service import get_memory_service
        ref = await get_memory_service().ingest(
            text,
            user_id=user_id,
            source=source,
            session_id=session_id,
            memory_type=memory_type,
            status="approved",
            tags=["person", "auto_extract", person_name.lower()],
            entity_type="person" if entity_id and not entity_id.startswith("slug:") else "person_pending",
            entity_id=entity_id or f"slug:{person_name.lower().replace(' ', '_')}",
        )
        return ref.id if ref else None
    except Exception as exc:
        logger.debug("person_extractor: mempalace ingest failed: %s", exc)
        return None


async def _write_activity(
    person_id: str,
    user_id: str,
    activity_type: str,
    description: str,
    source: str,
    db,
    mem_id: Optional[str] = None,
    venue: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    row_id = str(uuid.uuid4())
    try:
        try:
            await db.execute(
                "INSERT INTO person_activities (id, person_id, user_id, activity_type, description, source, venue, session_id, mem_id) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                row_id, person_id, user_id, activity_type, description, source, venue, session_id, mem_id,
            )
        except Exception:
            await db.execute(
                "INSERT INTO person_activities (id, person_id, user_id, activity_type, description, source, venue, session_id, mem_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (row_id, person_id, user_id, activity_type, description, source, venue, session_id, mem_id),
            )
    except Exception as exc:
        logger.debug("_write_activity failed: %s", exc)


async def _write_date(
    person_id: str, user_id: str, label: str,
    month: Optional[int], day: Optional[int], year: Optional[int],
    db, mem_id: Optional[str] = None,
) -> None:
    row_id = str(uuid.uuid4())
    try:
        try:
            await db.execute(
                "INSERT INTO person_important_dates (id, person_id, user_id, label, date_type, month, day, year, mem_id) "
                "VALUES ($1,$2,$3,$4,'birthday',$5,$6,$7,$8)",
                row_id, person_id, user_id, label, month, day, year, mem_id,
            )
        except Exception:
            await db.execute(
                "INSERT INTO person_important_dates (id, person_id, user_id, label, date_type, month, day, year, mem_id) "
                "VALUES (?,?,?,?,'birthday',?,?,?,?)",
                (row_id, person_id, user_id, label, month, day, year, mem_id),
            )
    except Exception as exc:
        logger.debug("_write_date failed: %s", exc)


async def _write_gift(
    person_id: str, user_id: str, description: str, status: str, source: str,
    db, mem_id: Optional[str] = None,
) -> None:
    row_id = str(uuid.uuid4())
    try:
        try:
            await db.execute(
                "INSERT INTO person_gift_ideas (id, person_id, user_id, description, status, source, mem_id) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                row_id, person_id, user_id, description, status, source, mem_id,
            )
        except Exception:
            await db.execute(
                "INSERT INTO person_gift_ideas (id, person_id, user_id, description, status, source, mem_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (row_id, person_id, user_id, description, status, source, mem_id),
            )
    except Exception as exc:
        logger.debug("_write_gift failed: %s", exc)


async def _write_bucket(
    person_id: str, user_id: str, description: str,
    db, mem_id: Optional[str] = None,
) -> None:
    row_id = str(uuid.uuid4())
    try:
        try:
            await db.execute(
                "INSERT INTO person_bucket_list (id, person_id, user_id, description, mem_id) "
                "VALUES ($1,$2,$3,$4,$5)",
                row_id, person_id, user_id, description, mem_id,
            )
        except Exception:
            await db.execute(
                "INSERT INTO person_bucket_list (id, person_id, user_id, description, mem_id) "
                "VALUES (?,?,?,?,?)",
                (row_id, person_id, user_id, description, mem_id),
            )
    except Exception as exc:
        logger.debug("_write_bucket failed: %s", exc)


async def _post_write_hooks(
    person_id: str, user_id: str, db,
) -> None:
    """Increment notification_count, update last_contacted_at, recalc health_score, push WS event."""
    try:
        try:
            await db.execute(
                "UPDATE people SET notification_count = notification_count + 1, "
                "last_contacted_at = $1 WHERE id = $2 AND user_id = $3",
                datetime.utcnow().isoformat() + "Z", person_id, user_id,
            )
        except Exception:
            await db.execute(
                "UPDATE people SET notification_count = notification_count + 1, "
                "last_contacted_at = ? WHERE id = ? AND user_id = ?",
                (datetime.utcnow().isoformat() + "Z", person_id, user_id),
            )
    except Exception as exc:
        logger.debug("_post_write_hooks: contact update failed: %s", exc)

    try:
        from person_health import recalc_and_save
        await recalc_and_save(person_id, user_id, db)
    except Exception as exc:
        logger.debug("_post_write_hooks: health recalc failed: %s", exc)

    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "people:updated", {"person_id": person_id})
    except Exception:
        pass


# ── Main entry point ──────────────────────────────────────────────────────────

async def process_text(
    text: str,
    *,
    user_id: str,
    source: str = "conversation",
    session_id: Optional[str] = None,
    db=None,
) -> int:
    """Extract person facts from text, writing to PostgreSQL + MemPalace.

    Opens its own DB connection if db is None.
    Returns count of facts written.
    """
    if not text or not user_id or user_id in ("guest", "voice-daemon", ""):
        return 0

    _db, _opened = await _ensure_db(db)
    if _db is None:
        return 0

    written = 0

    try:
        # Collect all matches across all patterns
        tasks: list[tuple[str, str, str]] = []  # (name, fact_text, pattern_type)

        # ── Preferences ────────────────────────────────────────────────────
        for m in _PREF_RE.finditer(text):
            name, what = m.group(1).strip(), m.group(2).strip()
            tasks.append((name, f"{name} {m.group(0).split(name, 1)[1].strip()[:200]}", "preference"))

        # ── Birthday ────────────────────────────────────────────────────────
        for m in _BDAY_RE.finditer(text):
            name, raw_date = m.group(1).strip(), m.group(2).strip()
            month, day, year = _parse_birthday(raw_date)
            if month or day:
                fact_text = f"{name}'s birthday is {raw_date}"
                tasks.append((name, fact_text, "birthday"))

        # ── Work ────────────────────────────────────────────────────────────
        for m in _WORK_RE.finditer(text):
            name, where = m.group(1).strip(), m.group(2).strip()
            tasks.append((name, f"{name} works at {where[:100]}", "work"))

        # ── Meeting ─────────────────────────────────────────────────────────
        for m in _MEETING_RE.finditer(text):
            name = m.group(1).strip()
            venue = m.group(2).strip() if len(m.groups()) > 1 and m.group(2) else None
            tasks.append((name, f"Met {name}" + (f" at {venue}" if venue else ""), "meeting"))

        # ── Gift ideas ───────────────────────────────────────────────────────
        for m in _GIFT_IDEA_RE.finditer(text):
            name, item = m.group(1).strip(), m.group(2).strip()
            tasks.append((name, f"Gift idea for {name}: {item[:150]}", "gift_idea"))

        # ── Gift given ───────────────────────────────────────────────────────
        for m in _GIFT_GIVEN_RE.finditer(text):
            name, item = m.group(1).strip(), m.group(2).strip()
            tasks.append((name, f"Gave {name} a {item[:150]}", "gift_given"))

        # ── Bucket list ──────────────────────────────────────────────────────
        for m in _BUCKET_RE.finditer(text):
            activity, name = m.group(1).strip(), m.group(2).strip()
            tasks.append((name, f"Want to {activity[:150]} with {name}", "bucket"))

        if not tasks:
            return 0

        # Deduplicate names to avoid redundant DB lookups
        names = list({t[0] for t in tasks})
        uuid_cache: dict[str, Optional[str]] = {}
        for name in names:
            uuid_cache[name] = await _resolve_person_uuid(name, user_id, _db)

        # Process each task
        for name, fact_text, pattern_type in tasks:
            person_uuid = uuid_cache.get(name)
            entity_id = person_uuid or None  # None → person_extractor will use slug in ingest

            # MemPalace write first (get mem_id)
            mem_id = await _ingest_to_mempalace(
                fact_text, user_id, name, entity_id,
                memory_type="person",
                source=source,
                session_id=session_id,
            )

            # PostgreSQL write (only when we have a DB UUID)
            if person_uuid:
                if pattern_type == "preference":
                    await _write_activity(person_uuid, user_id, "fact", fact_text, source, _db, mem_id, session_id=session_id)
                elif pattern_type == "birthday":
                    # Parse again for structured data
                    m_bd = _BDAY_RE.search(fact_text)
                    if m_bd:
                        raw_date = m_bd.group(2).strip() if len(m_bd.groups()) >= 2 else fact_text
                        month, day, year = _parse_birthday(raw_date)
                        await _write_date(person_uuid, user_id, f"{name}'s birthday", month, day, year, _db, mem_id)
                        await _write_activity(person_uuid, user_id, "birthday_recorded", fact_text, source, _db, mem_id, session_id=session_id)
                elif pattern_type == "work":
                    await _write_activity(person_uuid, user_id, "fact", fact_text, source, _db, mem_id, session_id=session_id)
                elif pattern_type == "meeting":
                    m_mt = _MEETING_RE.search(text)
                    venue = (m_mt.group(2).strip() if m_mt and len(m_mt.groups()) > 1 and m_mt.group(2) else None)
                    await _write_activity(person_uuid, user_id, "meeting", fact_text, source, _db, mem_id, venue=venue, session_id=session_id)
                elif pattern_type == "gift_idea":
                    m_gi = _GIFT_IDEA_RE.search(fact_text)
                    item = m_gi.group(2).strip() if m_gi else fact_text
                    await _write_gift(person_uuid, user_id, item[:200], "idea", source, _db, mem_id)
                elif pattern_type == "gift_given":
                    m_gg = _GIFT_GIVEN_RE.search(fact_text)
                    item = m_gg.group(2).strip() if m_gg else fact_text
                    await _write_gift(person_uuid, user_id, item[:200], "given", source, _db, mem_id)
                elif pattern_type == "bucket":
                    await _write_bucket(person_uuid, user_id, fact_text[:300], _db, mem_id)

                await _post_write_hooks(person_uuid, user_id, _db)

            written += 1

    except Exception as exc:
        logger.warning("person_extractor.process_text failed for user %s: %s", user_id, exc)

    return written


__all__ = ["process_text", "_resolve_person_uuid", "_parse_birthday"]
