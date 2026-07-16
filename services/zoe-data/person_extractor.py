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
import os
import re
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Temporal-relationships flag (default OFF) ─────────────────────────────────
#
# When OFF (default), ``_write_relationship`` behaves byte-for-byte as before:
# an insert-or-ignore against the (now partial) current-edge index — an existing
# current edge for the pair is left untouched, NO supersession. When ON, a write
# with a *different* rel_type for an existing current edge closes the old edge
# (valid_to + superseded_by) and inserts the new current edge, preserving
# history. Read lazily (per-call, no import-time side effect) — same idiom as
# ``zoe_memory_compose.compose_enabled``.

_TEMPORAL_TRUTHY = frozenset({"1", "true", "yes", "on"})


def temporal_relationships_enabled() -> bool:
    """Cheap per-call read of the temporal-relationships flag (default OFF)."""
    return (
        os.environ.get("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", "").strip().lower()
        in _TEMPORAL_TRUTHY
    )


def birthday_capture_enabled() -> bool:
    """Cheap per-call read of the birthday-capture flag (default OFF).

    When ON, a birthday mentioned for a person who is not yet a contact creates a
    bare is_partial=1 stub so the date can land on a real row, instead of being
    dropped. Byte-for-byte no-op while OFF. Phase 3 of
    docs/adr/ADR-contacts-from-known-people.md; replay-gated at enable time.
    """
    return (
        os.environ.get("ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED", "").strip().lower()
        in _TEMPORAL_TRUTHY
    )

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


# Relationship detection  e.g. "Sarah is Mike's wife" / "Mike and Sarah are siblings"
_REL_RE = re.compile(
    r"(?:"
    r"(?P<a>[A-Z][a-z]{1,30}(?:\s[A-Z][a-z]{1,20})?)\s+is\s+(?P<b>[A-Z][a-z]{1,30}(?:\s[A-Z][a-z]{1,20})?)'s\s+(?P<role1>wife|husband|partner|mother|father|sister|brother|daughter|son|aunt|uncle|cousin|niece|nephew|grandparent|grandchild|boss|mentor|colleague|friend)"
    r"|(?P<c>[A-Z][a-z]{1,30}(?:\s[A-Z][a-z]{1,20})?)\s+and\s+(?P<d>[A-Z][a-z]{1,30}(?:\s[A-Z][a-z]{1,20})?)\s+are\s+(?P<role2>siblings?|partners?|friends?|colleagues?|spouses?|twins?|cousins?)"
    r")",
    re.IGNORECASE,
)

# Map detected role strings → RELATIONSHIP_TYPES keys + group
_ROLE_TO_TYPE: dict[str, tuple[str, str]] = {
    "wife":        ("spouse",     "love"),
    "husband":     ("spouse",     "love"),
    "partner":     ("partner",    "love"),
    "spouse":      ("spouse",     "love"),
    "spouses":     ("spouse",     "love"),
    "mother":      ("parent",     "family"),
    "father":      ("parent",     "family"),
    "sister":      ("sibling",    "family"),
    "brother":     ("sibling",    "family"),
    "siblings":    ("sibling",    "family"),
    "sibling":     ("sibling",    "family"),
    "twins":       ("sibling",    "family"),
    # The are-branch call site does ``role.rstrip("s")``, so "twins" -> "twin";
    # keep this singular alias or "X and Y are twins" silently drops the relation.
    "twin":        ("sibling",    "family"),
    "daughter":    ("parent",     "family"),
    "son":         ("parent",     "family"),
    "aunt":        ("aunt_uncle", "family"),
    "uncle":       ("aunt_uncle", "family"),
    "cousin":      ("cousin",     "family"),
    "cousins":     ("cousin",     "family"),
    "niece":       ("aunt_uncle", "family"),
    "nephew":      ("aunt_uncle", "family"),
    "grandparent": ("grandparent","family"),
    "grandchild":  ("grandparent","family"),
    "friend":      ("friend",     "friend"),
    "friends":     ("friend",     "friend"),
    "boss":        ("boss",       "work"),
    "mentor":      ("mentor",     "work"),
    "colleague":   ("colleague",  "work"),
    "colleagues":  ("colleague",  "work"),
}

# Capitalized tokens the name regex over-captures but which are never a person's
# name — subject pronouns and sentence-openers. "She is Tom's sister" / "He is
# Jason's brother" must NOT mint a "She"/"He" person node + relationship edge.
# Deliberately conservative: real given names that collide with calendar words
# (April, May, June, Grace, Will) are intentionally NOT listed, so recall is kept.
_NON_NAME_TOKENS = frozenset({
    "he", "she", "they", "we", "it", "you", "i", "him", "her", "them", "us", "me",
    "his", "hers", "its", "their", "theirs", "my", "mine", "your", "yours", "our", "ours",
    "there", "here", "this", "that", "these", "those", "then", "now", "who", "what",
    "the", "a", "an", "yes", "no", "well", "so", "but", "and", "or",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    # Discourse/correction openers: "actually Delia's birthday…" must not mint
    # a person called "Actually Delia" (live repro 2026-07-13, slug:actually_delia).
    "actually", "wait", "sorry", "oh", "okay", "ok", "hey", "um", "uh",
    "also", "anyway", "remember", "note", "please", "maybe", "hmm",
    # Quantifiers/deictics: "forget everything about Delia" must never mint a
    # person "everything about" (live repro 2026-07-13, junk gift-idea row).
    "everything", "anything", "nothing", "something", "about", "forget",
    "delete", "remove", "erase",
})


_NAME_LEADIN_ROLES = frozenset({
    "friend", "mate", "buddy", "bestie", "wife", "husband", "partner", "girlfriend",
    "boyfriend", "son", "daughter", "kid", "kids", "child", "children", "girl", "boy",
    "brother", "sister", "mum", "mom", "mother", "dad", "father", "grandma", "grandpa",
    "grandmother", "grandfather", "aunt", "uncle", "niece", "nephew", "cousin",
    "colleague", "coworker", "boss", "neighbour", "neighbor", "parent", "sibling",
    "birthday", "name",
})


def _looks_like_person_name(name: str) -> bool:
    """False for the pronouns / sentence-openers the name regex over-captures.

    Multi-word captures ("Mary Jane") pass. The IGNORECASE pattern branches
    captured lead-ins like "friend Jessica" and minted them as persons (QA
    review F4) — but users also legitimately type lowercase names ("my friend
    jessica"), so rejection is by a KNOWN lead-in vocabulary (roles, pronouns,
    stop tokens) on the first token, not by capitalization.
    """
    name = (name or "").strip()
    if not name or name.lower() in _NON_NAME_TOKENS:
        return False
    first = name.split()[0].lower().rstrip("'s") if name.split()[0].lower().endswith("'s") else name.split()[0].lower()
    if first in _NON_NAME_TOKENS or first in _NAME_LEADIN_ROLES:
        return False  # "friend Jessica", "her birthday" — lead-in, not a name
    return True


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


async def _create_partial_person(name: str, user_id: str, db) -> Optional[str]:
    """Insert a bare is_partial=1 person stub; return its UUID (None on error).

    Used only by the flag-gated birthday-capture path so a mentioned birthday for
    a not-yet-contact has a row to land on. Private by default (visibility
    'personal'); is_partial=1 keeps it out of the recall dossier until promoted.
    """
    pid = str(uuid.uuid4())
    try:
        try:
            await db.execute(
                "INSERT INTO people (id, user_id, name, context, visibility, is_partial) "
                "VALUES ($1,$2,$3,'birthday_mention','personal',1)",
                pid, user_id, name,
            )
        except Exception:
            await db.execute(
                "INSERT INTO people (id, user_id, name, context, visibility, is_partial) "
                "VALUES (?,?,?,'birthday_mention','personal',1)",
                (pid, user_id, name),
            )
        await db.commit()
        return pid
    except Exception as exc:
        logger.warning(
            "person_extractor: _create_partial_person failed for name=%r user=%s "
            "— birthday has no person row to land on: %s", name, user_id, exc)
        return None


_OWNED_DB_CLS = None


def _owned_db(db_pool, pool, conn):
    """Wrap a pooled connection so it is OWNED until ``.close()`` releases it.

    ``AsyncpgCompat.close()`` is a pool-managed no-op, and ``db_pool.get_db()``
    only releases from the generator's ``finally``. The old ``_ensure_db``
    (``async for _db in get_db(): return _db``) abandoned that generator — the
    ``finally`` then released the connection back to the pool (on GC), handing
    callers a DEAD connection ("connection has been released back to the pool"),
    which silently broke the idle re-linker and the write-path person
    resolution. This returns an ``AsyncpgCompat`` subclass that holds the
    connection and gives ``close()`` a REAL release, preserving the
    ``(db, opened)`` + ``await _db.close()`` contract with zero caller changes.

    The subclass is defined lazily (cached) so ``db_pool`` is imported only when
    a connection is actually opened — never at module load, which the CI unit
    env (a ``db_pool`` stub) cannot satisfy.
    """
    global _OWNED_DB_CLS
    if _OWNED_DB_CLS is None:
        class _OwnedDb(db_pool.AsyncpgCompat):
            __slots__ = ("_pool", "_owned_conn")

            def __init__(self, pool, conn):
                super().__init__(conn)
                self._pool = pool
                self._owned_conn = conn

            async def close(self):
                await db_pool._release_safely(self._pool, self._owned_conn)

        _OWNED_DB_CLS = _OwnedDb
    return _OWNED_DB_CLS(pool, conn)


async def _ensure_db(db_arg):
    """Return ``(db, opened)``: use ``db_arg`` if given, else open a pooled one.

    When opening our own, HOLD the pooled connection (via ``_owned_db``) until
    the caller's ``await _db.close()`` releases it — never hand back a released
    connection. ``db_pool`` is imported lazily (matching the module's other
    lazy DB imports) so importing ``person_extractor`` never needs it.
    """
    if db_arg is not None:
        return db_arg, False
    try:
        import db_pool
        pool = db_pool.get_pool()
        conn = await pool.acquire()
        return _owned_db(db_pool, pool, conn), True
    except Exception as exc:
        logger.warning("person_extractor: could not open DB: %s", exc)
        return None, False


def _normalized_fact_value(text: str, person_name: str) -> str:
    """The value part of a compact person fact, normalized for comparison.
    "Delia: March 15" → "march 15"; a non-compact text normalizes whole."""
    t = (text or "").strip()
    prefix = f"{person_name}:"
    if t.lower().startswith(prefix.lower()):
        t = t[len(prefix):]
    return re.sub(r"\s+", " ", t).strip().lower().rstrip(".")


def _same_kind_row(meta: dict, row_text: str, person_name: str, pattern_type: str) -> bool:
    """Does this same-person row hold the SAME fact kind as the candidate?

    Primary key: the stored ``pattern_type`` (rows written after 2026-07-13).
    Legacy fallback (rows without pattern_type): only for ``birthday``, and only
    when the row is the compact "Name: <value>" shape whose value parses as a
    date — anything looser risks merging distinct fact kinds (meeting vs
    birthday)."""
    # MemoryService.ingest namespaces extra metadata as candidate_<key>, so
    # rows written through the real service carry candidate_pattern_type.
    stored = str(
        meta.get("pattern_type") or meta.get("candidate_pattern_type") or ""
    ).strip().lower()
    if stored:
        return stored == pattern_type
    if pattern_type != "birthday":
        return False
    t = (row_text or "").strip()
    if not t.lower().startswith(f"{person_name.lower()}:"):
        return False
    value = t.split(":", 1)[1].strip()
    try:
        month, day, _year = _parse_birthday(value)
    except Exception:
        return False
    return month is not None or day is not None


async def _reconcile_same_kind_entity_row(
    svc,
    text: str,
    user_id: str,
    person_name: str,
    entity_id: Optional[str],
    pattern_type: str,
    source: str,
) -> Optional[str]:
    """Supersede/skip against an existing row of the SAME person + SAME kind.

    Returns the surviving mem_id when the candidate was handled here (skip or
    supersede), or None to fall through to the text reconcile + plain ingest.
    """
    slug = f"slug:{person_name.lower().replace(' ', '_')}"
    entity_ids = [e for e in (entity_id, slug) if e]
    rows = await svc.list_by_entity(user_id, entity_ids)
    matches = [
        r for r in rows
        if _same_kind_row(r.metadata or {}, r.text, person_name, pattern_type)
    ]
    if not matches:
        return None
    cand_value = _normalized_fact_value(text, person_name)
    # Same value already stored → keep the existing row, write nothing.
    for r in matches:
        if _normalized_fact_value(r.text, person_name) == cand_value:
            logger.info(
                "person_extractor: entity dedup-skip kept=%s kind=%s", r.id, pattern_type
            )
            return r.id
    # Different value for the same person+kind → supersede in place. One row
    # per kind is the invariant; extra stale duplicates are archived.
    target, *extras = matches
    new_ref = await svc.review(
        target.id,
        decision="edit",
        edits=text,
        actor=source,
        note=f"person {pattern_type} supersede (entity-keyed, QA 2026-07-13)",
    )
    if new_ref is None:
        return None  # supersede failed — fall through to the normal path
    logger.info(
        "person_extractor: entity-superseded %s (kind=%s) with %s",
        target.id, pattern_type, new_ref.id,
    )
    try:
        if entity_id and not entity_id.startswith("slug:"):
            await svc.relink_entity(user_id, new_ref.id, "person", entity_id)
    except Exception as exc:
        logger.debug("person_extractor: relink after entity supersede failed: %s", exc)
    for stale in extras:
        try:
            await svc.review(
                stale.id, decision="archive", actor=source,
                note=f"stale duplicate {pattern_type} (entity-keyed supersede)",
            )
        except Exception:
            pass
    return new_ref.id


async def _ingest_to_mempalace(
    text: str,
    user_id: str,
    person_name: str,
    entity_id: Optional[str],
    memory_type: str = "person",
    source: str = "conversation",
    session_id: Optional[str] = None,
    pattern_type: Optional[str] = None,
) -> Optional[str]:
    """Write one fact to MemPalace and return the mem_id.

    ``pattern_type`` (birthday/preference/work/…) is stored on the row and
    drives the entity-keyed reconciliation: compact "Name: value" rows have no
    parseable attribute for text reconciliation, so same-person + same-kind
    supersession is decided by linkage instead (QA follow-up 2026-07-13).
    """
    try:
        # Quality gate: this LLM person-extraction path was writing transcript
        # echoes as facts ("Zoe: is being addressed in a conversation"). Drop
        # anything that isn't shaped like a storable fact before it pollutes recall.
        from memory_quality import is_storable_fact
        storable, reason = is_storable_fact(text)
        if not storable:
            logger.debug("person_extractor: dropped non-fact (%s): %r", reason, text[:60])
            return None
        from memory_service import get_memory_service
        svc = get_memory_service()
        # Entity-keyed reconciliation FIRST (QA follow-up 2026-07-13): compact
        # "Name: value" rows ("Delia: March 15") have no parseable attribute, so
        # the text reconcile below can never supersede them. When this fact is
        # linked to a person and has a known kind, look for an existing row of
        # the SAME person + SAME kind: same value → keep the existing row;
        # different value → supersede it in place. Best-effort — any failure
        # falls through to the text reconcile + plain ingest.
        if pattern_type:
            try:
                handled = await _reconcile_same_kind_entity_row(
                    svc, text, user_id, person_name, entity_id, pattern_type, source
                )
            except Exception as exc:
                logger.debug(
                    "person_extractor: entity reconcile skipped (%s)", type(exc).__name__
                )
                handled = None
            if handled is not None:
                return handled
        # Cross-writer reconciliation (QA review F9): this path used to blind-ADD,
        # so a re-stated or corrected person fact accumulated near-duplicate /
        # contradicting rows next to the memory-expert and digest copies. Route
        # through the shared ADD/UPDATE/SKIP decision (entity-guarded on the
        # person's name). reconcile_for_ingest never raises — errors → ADD.
        try:
            from memory_quality import reconcile_for_ingest
            op, target_id = await reconcile_for_ingest(
                svc, text, user_id, title=person_name)
        except Exception as exc:
            logger.debug("person_extractor: reconciliation unavailable (%s) — plain ingest", exc)
            op, target_id = "add", None
        if op in ("skip", "update") and target_id:
            # Linkage guard (Greptile P1): the matched row may have come from
            # another writer (raw voice_fact / digest) and NOT be keyed to this
            # person — returning/editing it would hand the activity/date/gift
            # callers a mem_id that person-scoped recall can't see, and edit
            # preserves the row's old entity link. Only reconcile onto rows
            # already keyed to THIS person (resolved uuid or same-name pending
            # slug); anything else falls back to a plain, correctly-linked ADD.
            try:
                target = await svc.get(target_id)
                meta = getattr(target, "metadata", None) or {}
                slug = f"slug:{person_name.lower().replace(' ', '_')}"
                acceptable = (
                    str(meta.get("entity_type") or "") in ("person", "person_pending")
                    and str(meta.get("entity_id") or "") in {e for e in (entity_id, slug) if e}
                )
            except Exception:
                acceptable = False
            if not acceptable:
                op, target_id = "add", None
        if op == "skip" and target_id:
            # Existing row is at least as informative — keep it, write nothing.
            logger.info("person_extractor: dedup-skip kept=%s cand=%r", target_id, text[:60])
            return target_id
        if op == "update" and target_id:
            try:
                new_ref = await svc.review(
                    target_id,
                    decision="edit",
                    edits=text,
                    actor=source,
                    note="person fact supersede (QA F9)",
                )
                if new_ref is not None:
                    logger.info("person_extractor: superseded %s with %r", target_id, text[:60])
                    # review(edit) keeps the row's old entity link. If this call
                    # holds a RESOLVED people.id but the matched row is still a
                    # same-name pending slug, promote it (Greptile P1) —
                    # relink_entity is metadata-only, per-user-locked, and a
                    # no-op unless the row is still person_pending. Best-effort:
                    # a failed relink leaves the pre-existing pending linkage,
                    # which the idle link-resolver also repairs.
                    try:
                        if entity_id and not entity_id.startswith("slug:"):
                            await svc.relink_entity(
                                user_id, new_ref.id, "person", entity_id)
                    except Exception as exc:
                        logger.debug("person_extractor: relink after supersede failed: %s", exc)
                    return new_ref.id
            except Exception as exc:
                logger.warning("person_extractor: supersede failed (%s) — plain ingest", exc)
        ref = await svc.ingest(
            text,
            user_id=user_id,
            source=source,
            session_id=session_id,
            memory_type=memory_type,
            status="approved",
            metadata={"pattern_type": pattern_type} if pattern_type else None,
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
        logger.warning(
            "person_extractor: _write_activity failed for person=%s user=%s "
            "type=%s — activity NOT stored: %s",
            person_id, user_id, activity_type, exc)


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
        logger.warning(
            "person_extractor: _write_date failed for person=%s user=%s "
            "label=%r — date NOT stored: %s", person_id, user_id, label, exc)


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
        logger.warning(
            "person_extractor: _write_gift failed for person=%s user=%s "
            "— gift idea NOT stored: %s", person_id, user_id, exc)


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
        logger.warning(
            "person_extractor: _write_bucket failed for person=%s user=%s "
            "— bucket-list item NOT stored: %s", person_id, user_id, exc)


async def _current_edge_for_pair(db, user_id: str, pid_a: str, pid_b: str):
    """Return (id, rel_type) of the CURRENT edge for the pair, or None.

    Current = ``valid_to IS NULL``. Handles both DB param styles: tries the
    asyncpg ($1) form first and falls back to the SQLite (?) form. Read-only.
    """
    sql_pg = (
        "SELECT id, rel_type FROM person_relationships "
        "WHERE user_id=$1 AND person_a_id=$2 AND person_b_id=$3 AND valid_to IS NULL"
    )
    sql_sqlite = (
        "SELECT id, rel_type FROM person_relationships "
        "WHERE user_id=? AND person_a_id=? AND person_b_id=? AND valid_to IS NULL"
    )
    try:
        cur = await db.execute(sql_pg, user_id, pid_a, pid_b)
    except Exception:
        cur = await db.execute(sql_sqlite, (user_id, pid_a, pid_b))
    try:
        row = await cur.fetchone()
    finally:
        # aiosqlite cursors expose close(); asyncpg's execute returns rows
        # directly and may not — guard so neither path raises.
        close = getattr(cur, "close", None)
        if close is not None:
            try:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass
    if not row:
        return None
    # Row may be a tuple, sqlite Row, or asyncpg Record — all index by position.
    return (row[0], row[1])


async def _supersede_edge(db, user_id: str, old_id: str, new_id: str, now: str) -> None:
    """Close a current edge: set valid_to + superseded_by + updated_at.

    Runs BEFORE the replacement insert so no current edge remains (keeps the
    partial unique index satisfiable). Handles both DB param styles.
    """
    try:
        await db.execute(
            "UPDATE person_relationships "
            "SET valid_to=$1, superseded_by=$2, updated_at=$3 "
            "WHERE id=$4 AND user_id=$5",
            now, new_id, now, old_id, user_id,
        )
    except Exception:
        await db.execute(
            "UPDATE person_relationships "
            "SET valid_to=?, superseded_by=?, updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, new_id, now, old_id, user_id),
        )


async def _reopen_edge(db, user_id: str, edge_id: str, now: str) -> None:
    """Re-open a superseded edge (compensating action): clear valid_to +
    superseded_by so the pair keeps a current edge when the replacement insert
    fails. PostgreSQL auto-commits the supersede UPDATE, so without this a failed
    insert would leave the pair with NO current edge + a dangling superseded_by.
    Handles both DB param styles.
    """
    try:
        await db.execute(
            "UPDATE person_relationships "
            "SET valid_to=NULL, superseded_by=NULL, updated_at=$1 "
            "WHERE id=$2 AND user_id=$3",
            now, edge_id, user_id,
        )
    except Exception:
        await db.execute(
            "UPDATE person_relationships "
            "SET valid_to=NULL, superseded_by=NULL, updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, edge_id, user_id),
        )


async def _write_relationship(
    user_id: str,
    name_a: str,
    name_b: str,
    rel_type: str,
    rel_group: str,
    db,
) -> None:
    """Upsert a relationship edge, creating partial stubs for unknown people."""
    from routers.people import RELATIONSHIP_TYPES, _WORK_GROUPS

    # Resolve labels
    lbl_a, lbl_b = rel_type.replace("_", " ").title(), rel_type.replace("_", " ").title()
    for group, entries in RELATIONSHIP_TYPES.items():
        for key, la, lb in entries:
            if key == rel_type:
                lbl_a, lbl_b = la, lb
                break

    inferred_ctx = "work" if rel_group in _WORK_GROUPS else "personal"
    now = datetime.utcnow().isoformat() + "Z"

    # Resolve or create person_a
    pid_a = await _resolve_person_uuid(name_a, user_id, db)
    if not pid_a:
        pid_a = str(uuid.uuid4())
        try:
            try:
                await db.execute(
                    "INSERT INTO people (id, user_id, name, circle, context, visibility, is_partial) "
                    "VALUES ($1,$2,$3,'circle',$4,'family',1)",
                    pid_a, user_id, name_a, inferred_ctx,
                )
            except Exception:
                await db.execute(
                    "INSERT INTO people (id, user_id, name, circle, context, visibility, is_partial) "
                    "VALUES (?,?,?,'circle',?,'family',1)",
                    (pid_a, user_id, name_a, inferred_ctx),
                )
            await db.commit()
        except Exception as exc:
            logger.warning(
                "person_extractor: _write_relationship stub insert for name_a=%r "
                "user=%s failed — edge %r NOT stored: %s",
                name_a, user_id, rel_type, exc)
            return

    # Resolve or create person_b
    pid_b = await _resolve_person_uuid(name_b, user_id, db)
    if not pid_b:
        pid_b = str(uuid.uuid4())
        try:
            try:
                await db.execute(
                    "INSERT INTO people (id, user_id, name, circle, context, visibility, is_partial) "
                    "VALUES ($1,$2,$3,'circle',$4,'family',1)",
                    pid_b, user_id, name_b, inferred_ctx,
                )
            except Exception:
                await db.execute(
                    "INSERT INTO people (id, user_id, name, circle, context, visibility, is_partial) "
                    "VALUES (?,?,?,'circle',?,'family',1)",
                    (pid_b, user_id, name_b, inferred_ctx),
                )
            await db.commit()
        except Exception as exc:
            logger.warning(
                "person_extractor: _write_relationship stub insert for name_b=%r "
                "user=%s failed — edge %r NOT stored: %s",
                name_b, user_id, rel_type, exc)
            return

    if pid_a == pid_b:
        return

    rel_id = str(uuid.uuid4())
    superseded_old_id: Optional[str] = None
    try:
        # ── Temporal supersession (flag ON only) ─────────────────────────
        # When the flag is ON and a *current* edge already exists for this pair
        # with a DIFFERENT rel_type, close it (valid_to + superseded_by) BEFORE
        # inserting so no current edge remains and the new one lands cleanly on
        # the partial (current-only) unique index. Same rel_type → leave it
        # (dedup). When the flag is OFF this whole block is skipped and the
        # insert-or-ignore below reproduces the pre-temporal behaviour exactly.
        if temporal_relationships_enabled():
            existing = await _current_edge_for_pair(db, user_id, pid_a, pid_b)
            if existing is not None:
                existing_id, existing_type = existing
                if existing_type == rel_type:
                    # Unchanged relationship — nothing to supersede or insert.
                    return
                # rel_type changed → close the old edge, then fall through to
                # insert the new current edge.
                await _supersede_edge(db, user_id, existing_id, rel_id, now)
                await db.commit()
                superseded_old_id = existing_id

        # ── Insert the new current edge (valid_from=now, valid_to=NULL) ──
        # ON CONFLICT / INSERT OR IGNORE now target the PARTIAL current-edge
        # index (person_relationships_pair_active, WHERE valid_to IS NULL).
        try:
            await db.execute(
                "INSERT INTO person_relationships "
                "(id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, rel_group, "
                "valid_from, valid_to, superseded_by, created_at, updated_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NULL,NULL,$10,$11) "
                "ON CONFLICT (user_id, person_a_id, person_b_id) WHERE valid_to IS NULL DO NOTHING",
                rel_id, user_id, pid_a, pid_b, rel_type, lbl_a, lbl_b, rel_group, now, now, now,
            )
        except Exception:
            try:
                # SQLite: INSERT OR IGNORE already honours the partial unique
                # index (a conflict only fires against a current row).
                await db.execute(
                    "INSERT OR IGNORE INTO person_relationships "
                    "(id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, rel_group, "
                    "valid_from, valid_to, superseded_by, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,?,?)",
                    (rel_id, user_id, pid_a, pid_b, rel_type, lbl_a, lbl_b, rel_group, now, now, now),
                )
            except Exception as exc2:
                # Insert failed. If we just superseded a prior edge, RE-OPEN it so
                # the pair keeps a current edge (no dangling superseded_by).
                if superseded_old_id is not None:
                    try:
                        await _reopen_edge(db, user_id, superseded_old_id, now)
                        await db.commit()
                        logger.warning(
                            "_write_relationship: insert failed after supersede; "
                            "re-opened prior edge %s: %s", superseded_old_id, exc2)
                    except Exception as exc3:
                        logger.warning(
                            "_write_relationship: insert AND re-open failed; pair "
                            "may lack a current edge until re-mentioned: %s / %s",
                            exc2, exc3)
                else:
                    logger.warning(
                        "person_extractor: _write_relationship insert failed for "
                        "user=%s %r -[%s]- %r — edge NOT stored: %s",
                        user_id, name_a, rel_type, name_b, exc2)
                return
        await db.commit()
        # Update context for both people
        for pid in (pid_a, pid_b):
            try:
                await db.execute(
                    "UPDATE people SET context=$1 WHERE id=$2 AND user_id=$3",
                    inferred_ctx, pid, user_id,
                )
            except Exception:
                await db.execute(
                    "UPDATE people SET context=? WHERE id=? AND user_id=?",
                    (inferred_ctx, pid, user_id),
                )
        await db.commit()
        logger.debug("_write_relationship: %s -[%s]- %s", name_a, rel_type, name_b)
    except Exception as exc:
        logger.warning(
            "person_extractor: _write_relationship failed for user=%s "
            "%r -[%s]- %r — edge NOT stored: %s",
            user_id, name_a, rel_type, name_b, exc)


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
        logger.warning(
            "person_extractor: _post_write_hooks contact update failed for "
            "person=%s user=%s — last_contacted_at/notification_count stale: %s",
            person_id, user_id, exc)

    try:
        from person_health import recalc_and_save
        await recalc_and_save(person_id, user_id, db)
    except Exception as exc:
        logger.debug("_post_write_hooks: health recalc failed: %s", exc)

    try:
        from push import broadcaster
        await broadcaster.broadcast(
            "people", "people:updated", {"person_id": person_id}, user_id=user_id
        )
    except Exception:
        pass


# ── Main entry point ──────────────────────────────────────────────────────────

async def apply_person_fact(
    name: str,
    fact_type: str,
    value: str,
    *,
    user_id: str,
    source: str,
    session_id: str | None = None,
    db=None,
) -> bool:
    """Apply one structured person fact (regex or LLM). Returns True if written."""
    name = (name or "").strip()
    value = (value or "").strip()
    if not name or not value:
        return False

    _db, should_close = await _ensure_db(db)
    if _db is None:
        return False

    # POOL-LEAK guard: when _ensure_db OPENED this connection (db was None), it
    # is an owned pooled connection that MUST be released. This function runs
    # per extracted fact on every chat turn; leaking one connection per call
    # drained the 10-slot pool in ~8 memory-bearing turns and wedged ALL of
    # /api/chat while /health stayed green (live outage, 2026-07-12).
    try:
        pattern_type = fact_type.strip().lower()
        if pattern_type == "bucket_list":
            pattern_type = "bucket"

        fact_text = value if name.lower() in value.lower() else f"{name}: {value}"
        person_uuid = await _resolve_person_uuid(name, user_id, _db)
        entity_id = person_uuid

        mem_id = await _ingest_to_mempalace(
            fact_text[:300],
            user_id,
            name,
            entity_id,
            memory_type="person",
            source=source,
            session_id=session_id,
            pattern_type=pattern_type,
        )

        if not person_uuid:
            return bool(mem_id)

        try:
            if pattern_type == "preference":
                await _write_activity(person_uuid, user_id, "fact", fact_text, source, _db, mem_id, session_id=session_id)
            elif pattern_type == "birthday":
                month, day, year = _parse_birthday(value)
                await _write_date(person_uuid, user_id, f"{name}'s birthday", month, day, year, _db, mem_id)
                await _write_activity(person_uuid, user_id, "birthday_recorded", fact_text, source, _db, mem_id, session_id=session_id)
            elif pattern_type == "work":
                await _write_activity(person_uuid, user_id, "fact", fact_text, source, _db, mem_id, session_id=session_id)
            elif pattern_type == "meeting":
                await _write_activity(person_uuid, user_id, "meeting", fact_text, source, _db, mem_id, session_id=session_id)
            elif pattern_type == "gift_idea":
                await _write_gift(person_uuid, user_id, value[:200], "idea", source, _db, mem_id)
            elif pattern_type == "gift_given":
                await _write_gift(person_uuid, user_id, value[:200], "given", source, _db, mem_id)
            elif pattern_type == "bucket":
                await _write_bucket(person_uuid, user_id, fact_text[:300], _db, mem_id)
            else:
                await _write_activity(person_uuid, user_id, "fact", fact_text, source, _db, mem_id, session_id=session_id)
            await _post_write_hooks(person_uuid, user_id, _db)
            return True
        except Exception as exc:
            logger.debug("apply_person_fact failed for %r: %s", name, exc)
            return False
    finally:
        if should_close and _db is not None:
            try:
                await _db.close()
            except Exception:
                pass


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

        # ── Relationships ────────────────────────────────────────────────────
        for m in _REL_RE.finditer(text):
            if m.group("role1"):
                name_a = m.group("a").strip()
                name_b = m.group("b").strip()
                role = m.group("role1").lower()
            else:
                name_a = m.group("c").strip()
                name_b = m.group("d").strip()
                role = m.group("role2").lower().rstrip("s")
            rel_info = _ROLE_TO_TYPE.get(role)
            if rel_info and _looks_like_person_name(name_a) and _looks_like_person_name(name_b):
                rel_type, rel_group = rel_info
                try:
                    await _write_relationship(user_id, name_a, name_b, rel_type, rel_group, _db)
                    written += 1
                except Exception as exc:
                    logger.debug("person_extractor: relationship write failed: %s", exc)
            elif rel_info:
                # A pronoun / sentence-opener captured as a name ("She is Tom's
                # sister") would silently mint a junk person node + edge. Drop it.
                logger.debug(
                    "person_extractor: skipped non-name relationship %r/%r", name_a, name_b
                )

        if not tasks:
            return written

        # Deduplicate names to avoid redundant DB lookups
        names = list({t[0] for t in tasks})
        uuid_cache: dict[str, Optional[str]] = {}
        for name in names:
            uuid_cache[name] = await _resolve_person_uuid(name, user_id, _db)

        # Process each task
        for name, fact_text, pattern_type in tasks:
            # QA review F2/F4: the pattern branches capture raw regex groups, and
            # with IGNORECASE that minted junk person entities — literally "her"
            # (from "her birthday is actually…") and "friend Jessica". One guard
            # at the consumption point covers every branch; the pronoun/possessive
            # shapes are handled by memory_extractor's coreference path instead.
            if not _looks_like_person_name(name):
                logger.debug("person_extractor: skipping non-name %r (%s)", name, pattern_type)
                continue
            person_uuid = uuid_cache.get(name)

            # Birthday capture (Phase 3, flag-gated dark): a birthday mentioned for
            # someone who isn't yet a contact has nowhere to land — the structured
            # write below needs a row. When enabled, mint a stub so the date sticks.
            # Byte-for-byte no-op while ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED is OFF.
            if (
                person_uuid is None
                and pattern_type == "birthday"
                and birthday_capture_enabled()
                and _looks_like_person_name(name)
            ):
                person_uuid = await _create_partial_person(name, user_id, _db)
                if person_uuid:
                    uuid_cache[name] = person_uuid

            entity_id = person_uuid or None  # None → person_extractor will use slug in ingest

            # MemPalace write first (get mem_id)
            mem_id = await _ingest_to_mempalace(
                fact_text, user_id, name, entity_id,
                memory_type="person",
                source=source,
                session_id=session_id,
                pattern_type=pattern_type,
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
    finally:
        # POOL-LEAK guard: release the owned pooled connection _ensure_db opened
        # (db=None call sites — the per-turn chat memory pass). Leaking one per
        # turn drained the 10-slot pool and wedged /api/chat while /health stayed
        # green (live outage, 2026-07-12). Same guard as apply_person_fact.
        if _opened and _db is not None:
            try:
                await _db.close()
            except Exception:
                pass

    return written


__all__ = ["process_text", "apply_person_fact", "_resolve_person_uuid", "_parse_birthday"]
