"""A correction reaches the stored record, and the reply says what changed.

Before this, a correction such as "that date is wrong" was answered with an apology and
nothing stored changed, and a statement such as "X is their dog" left X recorded as a child.
The correction cue (``memory_supersede`` "correction", ``memory_extractor._CORRECTION_RES``)
only ever worked on the NEXT fact write, never on the rows already stored.

This is a deterministic tier ahead of the brain (``fast_tiers.resolve``, so web chat, the
Telegram lane and LiveKit all get it). It acts ONLY when it finds the stored record to
fix; with nothing to change it returns None and the brain answers as before — a
correction is never claimed without evidence.

* **Date correction** ("you've got the date wrong", "we're in Australia"): find the most
  recent of the user's own messages carrying an ambiguous numeric date, re-read it
  day-first (``date_locale``), and rewrite every stored fact that still holds the raw
  digits or the month-first rendering a model made of them — ``MemoryService.review(edit)``
  supersedes, nothing is deleted — then the structured ``person_important_dates`` row.
* **Pet correction** ("Biscuit is their dog"): the person's relationship becomes ``pet <kind>``,
  their parent/sibling edges become the new ``pet`` edge type, stored facts that call them a
  child are rewritten, and one explicit "is a pet dog, not a child" fact is stored so a
  summary that enumerates children no longer lists them.

Flag-dark: ``ZOE_CORRECTION_APPLY`` (default OFF, read per call). Off, ``fast_tiers`` never
reaches this module.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_CORRECTION_APPLY"
SOURCE = "conversation_correction"
SCAN_LIMIT = 2000
RECENT_TURNS = 6
MAX_WORDS = 40  # a correction is a short sentence; a paragraph that mentions "wrong" is not one


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_CORRECTION_APPLY", False)


@dataclass
class CorrectionResult:
    kind: str                 # "date" | "pet"
    reply: str
    changed: list[str] = field(default_factory=list)  # the corrected texts


# ── cues ─────────────────────────────────────────────────────────────────────

_DATE_WORD = r"(?:dates?|birthdays?|dob|day|days|month|months|born|year)"
_WRONG = (r"(?:wrong|incorrect|mistaken|mistake|not right|backwards?|back to front|swapped|"
          r"mixed up|the wrong way(?: (?:round|around))?|the other way (?:round|around)|"
          r"american|us style|month first)")
_DATE_CUE_RES = (
    re.compile(rf"\b{_DATE_WORD}\b[^.!?]{{0,60}}\b{_WRONG}\b", re.IGNORECASE),
    re.compile(rf"\b{_WRONG}\b[^.!?]{{0,60}}\b{_DATE_WORD}\b", re.IGNORECASE),
    re.compile(r"\b(?:day[- ]first|dd/mm|d/m/y|australian (?:date|format))\b", re.IGNORECASE),
    re.compile(r"\b(?:we(?:'re| are)|i(?:'m| am)) in australia\b[^.!?]{0,60}\b(?:date|day|month)\b",
               re.IGNORECASE),
)


def is_date_correction(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t.split()) > MAX_WORDS:
        return False
    return any(rx.search(t) for rx in _DATE_CUE_RES)


_PET = (r"(?:dog|puppy|pup|cat|kitten|bird|parrot|budgie|rabbit|bunny|hamster|guinea pig|"
        r"fish|horse|pony|pet)")
_PET_RE = re.compile(
    r"^\s*(?:(?:no|nope|actually|sorry|wait|oh|hang on)[,.\s]+)*"
    r"(?P<n>[A-Za-z][A-Za-z'\-]{1,30}(?:\s[A-Z][a-z]{1,20})?)\s+(?:is|was)\s+(?:actually\s+|just\s+)?"
    r"(?:their|his|her|my|our|the|a|an|[A-Z][a-z]+'s)\s+(?:\w+\s+)?(?P<k>" + _PET + r")\b[.!?\s]*$",
    re.IGNORECASE,
)


def pet_statement(text: str) -> Optional[tuple[str, str]]:
    """``(name, pet kind)`` for "Biscuit is their dog" / "No, Biscuit is the dog", else None."""
    t = (text or "").strip()
    if not t or len(t.split()) > 14:
        return None
    m = _PET_RE.match(t)
    if not m:
        return None
    name = m.group("n").strip()
    try:
        from person_extractor import _looks_like_person_name

        if not _looks_like_person_name(name):
            return None
    except Exception:  # noqa: BLE001
        return None
    return name[:1].upper() + name[1:], m.group("k").lower()


def is_correction_turn(text: str) -> bool:
    """Cheap pre-check so a turn that is neither shape never touches the store."""
    return is_date_correction(text) or pet_statement(text) is not None


# ── driver helpers (asyncpg ``$n`` or sqlite ``?`` — the codebase's try-both idiom) ──────

async def _exec(db, dollar_sql: str, qmark_sql: str, args: tuple) -> bool:
    for sql, dollar in ((dollar_sql, True), (qmark_sql, False)):
        try:
            if dollar:
                await db.execute(sql, *args)
            else:
                await db.execute(sql, args)
            return True
        except Exception:  # noqa: BLE001 — try the other placeholder style
            continue
    return False


async def _fetch(db, dollar_sql: str, qmark_sql: str, args: tuple,
                 qmark_args: Optional[tuple] = None) -> list:
    for sql, dollar in ((dollar_sql, True), (qmark_sql, False)):
        try:
            qargs = args if qmark_args is None else qmark_args
            cur = await (db.execute(sql, *args) if dollar else db.execute(sql, qargs))
            return list(await cur.fetchall())
        except Exception:  # noqa: BLE001
            continue
    return []


async def _commit(db) -> None:
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
        pass


# ── date correction ──────────────────────────────────────────────────────────

def _caps(text: str) -> set[str]:
    return set(re.findall(r"\b[A-Z][a-z]{2,}\b", text or ""))


async def _recent_user_messages(user_id: str, session_id: str, current: str) -> list[str]:
    msgs: list[str] = []
    try:
        from memory_extractor import _load_recent_user_messages, recall_prev_user_turn

        msgs = await _load_recent_user_messages(user_id, session_id, current, limit=RECENT_TURNS)
        if not msgs:
            prev = recall_prev_user_turn(user_id, session_id)
            if prev and prev.strip() != (current or "").strip():
                msgs = [prev]
    except Exception as exc:  # noqa: BLE001
        logger.debug("correction_apply: history lookup failed (%s)", type(exc).__name__)
    return msgs


async def _fix_structured_date(db, user_id: str, old_mem_id: str, new_mem_id: str, d) -> None:
    """Point the person's important-date row at the corrected day/month/year."""
    if db is None:
        return
    await _exec(
        db,
        "UPDATE person_important_dates SET month=$1, day=$2, year=$3, mem_id=$4 "
        "WHERE user_id=$5 AND mem_id=$6",
        "UPDATE person_important_dates SET month=?, day=?, year=?, mem_id=? "
        "WHERE user_id=? AND mem_id=?",
        (d.month, d.day, d.year, new_mem_id, user_id, old_mem_id),
    )
    await _commit(db)


async def apply_date_correction(
    text: str, user_id: str, recent_messages: list[str], *, svc=None, db=None,
) -> Optional[CorrectionResult]:
    from date_locale import find_numeric_dates, misread_pattern, render_date

    if not is_date_correction(text):
        return None
    # The most recent user message that carries an AMBIGUOUS numeric date: the one being
    # corrected. Older messages are left alone (blast radius = one message).
    source_msg, found = "", []
    for msg in recent_messages:
        f = [x for x in find_numeric_dates(msg) if x.date.ambiguous]
        if f:
            source_msg, found = msg, f
            break
    if not found:
        return None
    if svc is None:
        from memory_service import get_memory_service

        svc = get_memory_service()
    rows = await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT)
    names = _caps(source_msg)
    changed: list[str] = []
    done: set[str] = set()
    for f in found:
        right = render_date(f.date.day, f.date.month, f.date.year)
        wrong_rx = misread_pattern(f.date)
        for row in rows:
            if row.id in done:
                continue
            old = row.text or ""
            new = None
            if f.token in old:  # the raw digits, stored as typed
                new = old.replace(f.token, right)
            elif wrong_rx is not None and wrong_rx.search(old) and (_caps(old) & names):
                # the month-first rendering a model made of them — only on a row about the
                # same person/thing as the corrected message, never on an unrelated date
                new = wrong_rx.sub(right, old, count=1)
            if not new or new == old:
                continue
            try:
                ref = await svc.review(
                    row.id, decision="edit", edits=new, actor=SOURCE,
                    note="date correction (day-first household order)",
                    source_excerpt=" ".join((text or "").split()),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("correction_apply: date edit failed (%s)", type(exc).__name__)
                continue
            if ref is None:
                continue
            done.add(row.id)
            changed.append(new)
            await _fix_structured_date(db, user_id, row.id, getattr(ref, "id", row.id), f.date)
    if not changed:
        return None
    # Say what changed, in the stored words: prefer the sentence form ("Jordan's birthday is
    # 7 August 1991") over a compact "Jordan: 7 August 1991".
    uniq = list(dict.fromkeys(c.strip().rstrip(".") for c in changed))
    uniq.sort(key=lambda c: ("birthday" not in c.lower(), len(c)))
    shown = "; ".join(uniq[:2])
    reply = (f"Fixed: {shown}. I'd read it month-first — dates are day first here, "
             "so that's what I've stored.")
    logger.info("CORRECTION_APPLIED kind=date user=%s rows=%d", user_id, len(changed))
    return CorrectionResult("date", reply, uniq)


# ── pet correction ───────────────────────────────────────────────────────────

_CHILD_CUE = re.compile(r"\b(?:child|children|kids?|sons?|daughters?|babys?|babies|girls?|boys?)\b",
                        re.IGNORECASE)


def _strip_name(text: str, name: str) -> str:
    """Remove NAME (+ an optional surname and its comma/and) from an enumeration."""
    nm = rf"{re.escape(name)}(?:\s+[A-Z][a-z]+)?"
    out = re.sub(rf",?\s*(?:and\s+)?{nm}(?=\s*(?:,|and\b|\.|$))", "", text, count=1)
    out = re.sub(r"(\b\w+)\s+,\s*", r"\1 ", out)
    return re.sub(r"\s{2,}", " ", out).strip()


async def _people_rows(db, user_id: str, name: str) -> list[tuple[str, str, str]]:
    rows = await _fetch(
        db,
        "SELECT id, name, relationship FROM people WHERE user_id=$1 AND deleted=0 AND "
        "(lower(name)=lower($2) OR lower(name) LIKE lower($3))",
        "SELECT id, name, relationship FROM people WHERE user_id=? AND deleted=0 AND "
        "(lower(name)=lower(?) OR lower(name) LIKE lower(?))",
        (user_id, name, f"{name} %"),
    )
    return [(r[0], r[1], r[2] or "") for r in rows]


async def _current_edges(db, user_id: str, person_id: str) -> list[tuple[str, str, str]]:
    rows = await _fetch(
        db,
        "SELECT id, person_a_id, person_b_id FROM person_relationships WHERE user_id=$1 "
        "AND valid_to IS NULL AND rel_type <> 'pet' AND (person_a_id=$2 OR person_b_id=$2)",
        "SELECT id, person_a_id, person_b_id FROM person_relationships WHERE user_id=? "
        "AND valid_to IS NULL AND rel_type <> 'pet' AND (person_a_id=? OR person_b_id=?)",
        (user_id, person_id),
        (user_id, person_id, person_id),
    )
    return [(r[0], r[1], r[2]) for r in rows]


async def _make_pet(db, user_id: str, person_id: str, current_rel: str, kind: str) -> int:
    """Relationship -> ``pet <kind>``; parent/sibling/... edges -> the ``pet`` edge type
    (pet = person_a, owner = person_b; labels as ``RELATIONSHIP_TYPES['pet']``).
    Returns how many things changed (0 when the record already says pet)."""
    changed = 0
    rel = "pet" if kind == "pet" else f"pet {kind}"
    now = datetime.utcnow().isoformat() + "Z"
    if current_rel.strip().lower() != rel and await _exec(
        db,
        "UPDATE people SET relationship=$1, updated_at=$2 WHERE id=$3 AND user_id=$4",
        "UPDATE people SET relationship=?, updated_at=? WHERE id=? AND user_id=?",
        (rel, now, person_id, user_id),
    ):
        changed += 1
    for edge_id, a, b in await _current_edges(db, user_id, person_id):
        owner = b if a == person_id else a
        ok = await _exec(
            db,
            "UPDATE person_relationships SET rel_type='pet', rel_a_to_b='Pet owner', "
            "rel_b_to_a='Pet', rel_group='pet', person_a_id=$1, person_b_id=$2, updated_at=$3 "
            "WHERE id=$4 AND user_id=$5",
            "UPDATE person_relationships SET rel_type='pet', rel_a_to_b='Pet owner', "
            "rel_b_to_a='Pet', rel_group='pet', person_a_id=?, person_b_id=?, updated_at=? "
            "WHERE id=? AND user_id=?",
            (person_id, owner, now, edge_id, user_id),
        )
        if not ok:  # the pair already has a current pet edge: retire the wrong one instead
            ok = await _exec(
                db,
                "UPDATE person_relationships SET valid_to=$1, updated_at=$1 WHERE id=$2 AND user_id=$3",
                "UPDATE person_relationships SET valid_to=?, updated_at=? WHERE id=? AND user_id=?",
                (now, edge_id, user_id),
            )
        changed += 1 if ok else 0
    await _commit(db)
    return changed


async def apply_pet_correction(
    text: str, user_id: str, *, svc=None, db=None,
) -> Optional[CorrectionResult]:
    stmt = pet_statement(text)
    if not stmt:
        return None
    name, kind = stmt
    if svc is None:
        from memory_service import get_memory_service

        svc = get_memory_service()
    people = await _people_rows(db, user_id, name) if db is not None else []
    full = people[0][1] if people else name
    name_rx = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    rows = await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT)
    pet_fact_rows = [r for r in rows if name_rx.search(r.text or "")
                     and "not a child" in (r.text or "").lower()]
    child_rows = [r for r in rows if name_rx.search(r.text or "") and _CHILD_CUE.search(r.text or "")
                  and "not a child" not in (r.text or "").lower()]
    if not people and not child_rows:
        return None  # nothing stored about this name: no claim, the brain answers
    pet_text = (f"{full} is a pet {kind}, not a child." if kind != "pet"
                else f"{full} is a pet, not a child.")
    changed = 0
    excerpt = " ".join((text or "").split())
    for pid, _nm, rel in people:
        changed += await _make_pet(db, user_id, pid, rel, kind)
    for r in child_rows:
        others = {w for w in _caps(r.text) if w.lower() != name.lower()}
        # an enumeration ("the kids are A, B and Biscuit") loses only the pet; a row that is
        # about the pet alone becomes the explicit pet fact
        new = _strip_name(r.text, name) if len(others) >= 2 else pet_text
        if new == r.text or not new:
            new = pet_text
        try:
            ref = await svc.review(r.id, decision="edit", edits=new, actor=SOURCE,
                                   note="pet is not a child (correction)", source_excerpt=excerpt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("correction_apply: pet edit failed (%s)", type(exc).__name__)
            continue
        if ref is not None:
            changed += 1
    if not pet_fact_rows:
        entity_id = people[0][0] if people else f"slug:{name.lower().replace(' ', '_')}"
        try:
            ref = await svc.ingest(
                pet_text, user_id=user_id, source=SOURCE, memory_type="person",
                status="approved", tags=["person", "pet", name.lower()],
                entity_type="person" if people else "person_pending", entity_id=entity_id,
                metadata={"pattern_type": "pet"}, source_excerpt=excerpt,
            )
            changed += 1 if ref is not None else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("correction_apply: pet fact ingest failed (%s)", type(exc).__name__)
    if not changed:
        return None
    word = "pet" if kind == "pet" else f"pet {kind}"
    reply = (f"Fixed: {full} is a {word}, not one of the children. "
             "I've updated their record and the family list.")
    logger.info("CORRECTION_APPLIED kind=pet user=%s changed=%d", user_id, changed)
    return CorrectionResult("pet", reply, [pet_text])


# ── entry point ──────────────────────────────────────────────────────────────

async def maybe_apply(
    text: str, user_id: str, session_id: str, *, recent_messages: Optional[list[str]] = None,
    svc=None, db=None,
) -> Optional[CorrectionResult]:
    """Apply a spoken/typed correction to the stored record, or None (the brain answers).

    Never raises: any failure is logged and the turn goes on to the brain."""
    if not enabled() or not user_id or user_id in ("guest", "voice-daemon", ""):
        return None
    if not is_correction_turn(text):
        return None
    opened = False
    try:
        try:
            import user_prefs

            if await user_prefs.is_memory_opted_out(user_id):
                return None
        except Exception:  # noqa: BLE001
            pass
        if db is None:
            from person_extractor import _ensure_db

            db, opened = await _ensure_db(None)
        if is_date_correction(text):
            if recent_messages is None:
                recent_messages = await _recent_user_messages(user_id, session_id, text)
            res = await apply_date_correction(text, user_id, recent_messages, svc=svc, db=db)
            if res:
                return res
        return await apply_pet_correction(text, user_id, svc=svc, db=db)
    except Exception as exc:  # noqa: BLE001 — a correction never breaks the turn
        logger.warning("correction_apply failed user=%s: %s", user_id, type(exc).__name__)
        return None
    finally:
        if opened and db is not None:
            try:
                await db.close()
            except Exception:  # noqa: BLE001
                pass
